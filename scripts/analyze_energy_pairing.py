#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sqlite3
from bisect import bisect_left
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "smart_home.sqlite"
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"

SENSE_RE = re.compile(r"Watts: ([\d.-]+), Current: ([\d.-]+), Voltage: ([\d.-]+)")
ENVOY_POWER_RE = re.compile(r"(?:Meter:|Power And Energy,) ([^,]+), power: ([\d.-]+) kW")


def parse_dt(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def nearest(samples: list[dict[str, Any]], target: datetime, max_seconds: int) -> dict[str, Any] | None:
    if not samples:
        return None
    times = [item["dt"] for item in samples]
    index = bisect_left(times, target)
    candidates = []
    if index < len(samples):
        candidates.append(samples[index])
    if index > 0:
        candidates.append(samples[index - 1])
    best = min(candidates, key=lambda item: abs((item["dt"] - target).total_seconds()))
    gap_seconds = abs((best["dt"] - target).total_seconds())
    if gap_seconds > max_seconds:
        return None
    result = dict(best)
    result["gapSeconds"] = gap_seconds
    return result


def load_samples() -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    sense: list[dict[str, Any]] = []
    envoy: dict[str, list[dict[str, Any]]] = {
        "Consumption Total": [],
        "Consumption Net": [],
        "Production": [],
        "Storage": [],
    }
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """
            select captured_at, component, message
            from home_events
            where component in ('Sense Energy Meter', 'Enphase Envoy')
            order by captured_at asc
            """
        )
        for row in rows:
            captured_at = row["captured_at"]
            message = row["message"] or ""
            if row["component"] == "Sense Energy Meter":
                match = SENSE_RE.search(message)
                if not match:
                    continue
                watts = float(match.group(1))
                sense.append(
                    {
                        "capturedAt": captured_at,
                        "dt": parse_dt(captured_at),
                        "senseKw": watts / 1000,
                        "senseWatts": watts,
                        "senseCurrent": float(match.group(2)),
                        "senseVoltage": float(match.group(3)),
                    }
                )
                continue
            match = ENVOY_POWER_RE.search(message)
            if not match:
                continue
            meter = match.group(1)
            if meter not in envoy:
                continue
            envoy[meter].append(
                {
                    "capturedAt": captured_at,
                    "dt": parse_dt(captured_at),
                    "meter": meter,
                    "kw": float(match.group(2)),
                }
            )
    return sense, envoy


def build_pairs(max_gap_seconds: int = 90) -> list[dict[str, Any]]:
    sense, envoy = load_samples()
    pairs: list[dict[str, Any]] = []
    for sample in sense:
        total = nearest(envoy["Consumption Total"], sample["dt"], max_gap_seconds)
        if total is None:
            continue
        net = nearest(envoy["Consumption Net"], sample["dt"], max_gap_seconds)
        production = nearest(envoy["Production"], sample["dt"], max_gap_seconds)
        storage = nearest(envoy["Storage"], sample["dt"], max_gap_seconds)
        delta_kw = total["kw"] - sample["senseKw"]
        pairs.append(
            {
                "senseCapturedAt": sample["capturedAt"],
                "envoyCapturedAt": total["capturedAt"],
                "gapSeconds": total["gapSeconds"],
                "senseKw": sample["senseKw"],
                "senseWatts": sample["senseWatts"],
                "senseCurrent": sample["senseCurrent"],
                "senseVoltage": sample["senseVoltage"],
                "envoyConsumptionTotalKw": total["kw"],
                "deltaKw": delta_kw,
                "ratioSenseToEnvoyTotal": sample["senseKw"] / total["kw"] if total["kw"] else None,
                "envoyConsumptionNetKw": net["kw"] if net else None,
                "envoyProductionKw": production["kw"] if production else None,
                "envoyStorageKw": storage["kw"] if storage else None,
            }
        )
    return pairs


def fmt_kw(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def summarize(values: list[float]) -> dict[str, float | int] | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        median = ordered[middle]
    else:
        median = (ordered[middle - 1] + ordered[middle]) / 2
    return {
        "count": len(ordered),
        "avg": sum(ordered) / len(ordered),
        "median": median,
        "min": ordered[0],
        "max": ordered[-1],
        "p10": ordered[int(0.10 * (len(ordered) - 1))],
        "p90": ordered[int(0.90 * (len(ordered) - 1))],
    }


def fmt_summary(summary: dict[str, float | int] | None) -> str:
    if summary is None:
        return "n/a"
    return (
        f"avg `{summary['avg']:.3f} kW`, median `{summary['median']:.3f} kW`, "
        f"p10 `{summary['p10']:.3f} kW`, p90 `{summary['p90']:.3f} kW`"
    )


def write_report(pairs: list[dict[str, Any]]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "pairCount": len(pairs),
        "pairs": pairs[-80:],
    }
    (DATA_DIR / "latest_energy_pairs.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    recent = pairs[-24:]
    lines = [
        "# Envoy / Sense Pairing",
        "",
        f"- Generated: `{payload['generatedAt']}`",
        f"- Pairs found: `{len(pairs)}`",
        "- Match rule: each Sense sample is paired with nearest Envoy Consumption Total sample within `90` seconds.",
        "",
    ]
    if recent:
        for item in recent:
            storage = item["envoyStorageKw"]
            item["envoyHouseLoadKw"] = (
                item["envoyConsumptionTotalKw"] + storage if storage is not None else None
            )
            item["houseLoadMinusSenseKw"] = (
                item["envoyHouseLoadKw"] - item["senseKw"] if item["envoyHouseLoadKw"] is not None else None
            )
        avg_delta = sum(item["deltaKw"] for item in recent) / len(recent)
        avg_ratio = sum(item["ratioSenseToEnvoyTotal"] for item in recent if item["ratioSenseToEnvoyTotal"] is not None) / len(recent)
        house_load_gaps = [
            item["houseLoadMinusSenseKw"]
            for item in recent
            if item["houseLoadMinusSenseKw"] is not None
        ]
        charging = [item for item in recent if (item["envoyStorageKw"] or 0) < -0.1]
        discharging_or_idle = [item for item in recent if (item["envoyStorageKw"] or 0) >= -0.1]
        lines.extend(
            [
                "## Recent Summary",
                "",
                f"- Recent pairs summarized: `{len(recent)}`",
                f"- Average Envoy minus Sense: `{avg_delta:.3f} kW`",
                f"- Average Sense / Envoy total ratio: `{avg_ratio:.1%}`",
                "- Battery-adjusted formula: `Envoy house load = Consumption Total + Storage`.",
                f"- Battery-adjusted Envoy house load minus Sense: {fmt_summary(summarize(house_load_gaps))}",
                f"- Battery charging pairs in recent window: `{len(charging)}`",
                f"- Battery idle/discharging pairs in recent window: `{len(discharging_or_idle)}`",
                "",
                "## Recent Pairs",
                "",
                "| Sense time | Gap | Sense kW | Envoy total kW | Storage kW | Envoy house load kW | House minus Sense kW | Envoy net kW | Production kW |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for item in reversed(recent):
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{item['senseCapturedAt']}`",
                        f"{item['gapSeconds']:.0f}s",
                        fmt_kw(item["senseKw"]),
                        fmt_kw(item["envoyConsumptionTotalKw"]),
                        fmt_kw(item["envoyStorageKw"]),
                        fmt_kw(item["envoyHouseLoadKw"]),
                        fmt_kw(item["houseLoadMinusSenseKw"]),
                        fmt_kw(item["envoyConsumptionNetKw"]),
                        fmt_kw(item["envoyProductionKw"]),
                    ]
                )
                + " |"
            )
    else:
        lines.append("- No Sense/Envoy pairs found yet.")
    (REPORT_DIR / "energy_pairing.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    if not DB_PATH.exists():
        raise SystemExit("No smart-home database yet. Run scripts/smart_home_snapshot.py first.")
    pairs = build_pairs()
    write_report(pairs)
    print(REPORT_DIR / "energy_pairing.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
