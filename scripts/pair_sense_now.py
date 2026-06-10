#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
DB_PATH = DATA_DIR / "smart_home.sqlite"
SENSE_NOW_PATH = DATA_DIR / "sense_now_latest.json"
PAIRING_JSON_PATH = DATA_DIR / "sense_now_pairing_latest.json"
PAIRING_REPORT_PATH = REPORT_DIR / "sense_now_pairing.md"

ENVOY_POWER_RE = re.compile(r"(?:Meter:|Power And Energy,) ([^,]+), power: ([\d.-]+) kW")
METERS = ["Consumption Total", "Consumption Net", "Production", "Storage"]


def parse_dt(raw: str) -> datetime:
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def nearest_envoy_samples(target: datetime) -> dict[str, dict[str, Any]]:
    nearest: dict[str, dict[str, Any]] = {}
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """
            select captured_at, message
            from home_events
            where component = 'Enphase Envoy'
              and message like '%power:%'
            order by captured_at desc
            limit 1000
            """
        )
        for row in rows:
            match = ENVOY_POWER_RE.search(row["message"] or "")
            if not match:
                continue
            meter = match.group(1)
            if meter not in METERS:
                continue
            captured_at = row["captured_at"]
            dt = parse_dt(captured_at)
            gap = abs((dt - target).total_seconds())
            current = nearest.get(meter)
            if current is None or gap < current["gapSeconds"]:
                nearest[meter] = {
                    "capturedAt": captured_at,
                    "gapSeconds": gap,
                    "kw": float(match.group(2)),
                }
    return nearest


def main() -> int:
    if not SENSE_NOW_PATH.exists():
        raise SystemExit("No Sense Now capture found. Run scripts/capture_sense_now.js first.")
    sense = json.loads(SENSE_NOW_PATH.read_text())
    captured_at = parse_dt(str(sense["capturedAt"]))
    sense_kw = float(sense["watts"]) / 1000
    envoy = nearest_envoy_samples(captured_at)

    result: dict[str, Any] = {
        "senseCapturedAtLocal": captured_at.isoformat(timespec="seconds"),
        "senseKw": sense_kw,
        "senseWatts": sense.get("watts"),
        "senseCurrent": sense.get("current"),
        "senseVoltage": sense.get("voltage"),
        "senseDevices": sense.get("devices", []),
        "envoy": envoy,
    }
    total = envoy.get("Consumption Total")
    storage = envoy.get("Storage")
    if total:
        result["envoyMinusSenseKw"] = total["kw"] - sense_kw
    if total and storage:
        result["envoyNonBatteryLoadKw"] = total["kw"] - abs(storage["kw"])
        result["envoyNonBatteryLoadMinusSenseKw"] = result["envoyNonBatteryLoadKw"] - sense_kw
        result["envoyTotalMinusStorageAbsMinusSenseKw"] = result["envoyNonBatteryLoadMinusSenseKw"]
    production = envoy.get("Production")
    if production and storage:
        result["envoySolarAfterStorageKw"] = production["kw"] + storage["kw"]

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    PAIRING_JSON_PATH.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    lines = [
        "# Sense Now / Envoy Pairing",
        "",
        f"- Sense captured: `{result['senseCapturedAtLocal']}`",
        f"- Sense total: `{sense_kw:.3f} kW`",
        "",
    ]
    for device in result["senseDevices"]:
        watts = float(device.get("watts") or 0)
        lines.append(f"- Sense device `{device.get('name')}`: `{watts / 1000:.3f} kW`")
    lines.extend(["", "## Nearest Envoy Meters", ""])
    for meter in METERS:
        sample = envoy.get(meter)
        if sample:
            lines.append(
                f"- {meter}: `{sample['kw']:.3f} kW` at `{sample['capturedAt']}` "
                f"({sample['gapSeconds']:.0f}s away)"
            )
    if "envoyMinusSenseKw" in result:
        lines.extend(["", f"- Raw Envoy total minus Sense: `{result['envoyMinusSenseKw']:.3f} kW`"])
    if "envoyNonBatteryLoadKw" in result:
        lines.append(f"- Envoy non-battery load estimate (`total - abs(storage)`): `{result['envoyNonBatteryLoadKw']:.3f} kW`")
    if "envoyNonBatteryLoadMinusSenseKw" in result:
        lines.append(f"- Envoy non-battery load estimate minus Sense: `{result['envoyNonBatteryLoadMinusSenseKw']:.3f} kW`")
    if "envoySolarAfterStorageKw" in result:
        lines.append(f"- Envoy solar after battery charge/discharge: `{result['envoySolarAfterStorageKw']:.3f} kW`")
    if "envoyTotalMinusStorageAbsMinusSenseKw" in result:
        lines.append(
            "- Envoy total minus battery charge/discharge magnitude minus Sense: "
            f"`{result['envoyTotalMinusStorageAbsMinusSenseKw']:.3f} kW`"
        )
    PAIRING_REPORT_PATH.write_text("\n".join(lines) + "\n")
    print(PAIRING_REPORT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
