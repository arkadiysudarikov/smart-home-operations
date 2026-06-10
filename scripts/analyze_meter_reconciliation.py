#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sqlite3
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
DB_PATH = DATA_DIR / "smart_home.sqlite"
ALARM_PATH = ROOT / "config" / "alarm_energy_readings.json"
LEGACY_ALARM_PATH = DATA_DIR / "alarm_energy_readings.json"
ALL_ENERGY_PATH = DATA_DIR / "latest_all_energy_pairs.json"
SENSE_NOW_PAIRING_PATH = DATA_DIR / "sense_now_pairing_latest.json"
OUT_JSON = DATA_DIR / "latest_meter_reconciliation.json"
OUT_REPORT = REPORT_DIR / "meter_reconciliation.md"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")

SENSE_RE = re.compile(r"Watts: ([\d.-]+), Current: ([\d.-]+), Voltage: ([\d.-]+)")
ENVOY_POWER_RE = re.compile(r"(?:Meter:|Power And Energy,) ([^,]+), power: ([\d.-]+) kW")
ENVOY_TODAY_RE = re.compile(r"(?:Meter:|Power And Energy,) ([^,]+), energy today: ([\d.-]+) kWh")


@dataclass
class Sample:
    source: str
    meter: str
    captured_at: datetime
    kw: float
    raw: dict[str, Any]


def parse_iso(raw: str) -> datetime:
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ)


def parse_alarm_time(item: dict[str, Any]) -> datetime:
    naive = datetime.strptime(item["alarmTimestamp"], "%Y-%m-%d %H:%M:%S")
    zone_name = item.get("assumedTimezone") or "UTC"
    if zone_name.upper() == "UTC":
        return naive.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ)
    return naive.replace(tzinfo=ZoneInfo(zone_name)).astimezone(LOCAL_TZ)


def load_alarm() -> dict[str, Any]:
    path = ALARM_PATH if ALARM_PATH.exists() else LEGACY_ALARM_PATH
    if not path.exists():
        return {"instantWatts": [], "dailyKwh": [], "periodKwh": []}
    return json.loads(path.read_text())


def load_monitor_samples() -> tuple[list[Sample], dict[str, dict[str, float]]]:
    samples: list[Sample] = []
    energy_today: dict[str, dict[str, float]] = {}
    if not DB_PATH.exists():
        return samples, energy_today
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
            captured_at = parse_iso(row["captured_at"])
            message = row["message"] or ""
            if row["component"] == "Sense Energy Meter":
                match = SENSE_RE.search(message)
                if match:
                    samples.append(
                        Sample(
                            source="Sense",
                            meter="Whole Home",
                            captured_at=captured_at,
                            kw=float(match.group(1)) / 1000.0,
                            raw={"current": float(match.group(2)), "voltage": float(match.group(3))},
                        )
                    )
                continue
            power_match = ENVOY_POWER_RE.search(message)
            if power_match:
                samples.append(
                    Sample(
                        source="Envoy",
                        meter=power_match.group(1),
                        captured_at=captured_at,
                        kw=float(power_match.group(2)),
                        raw={},
                    )
                )
            today_match = ENVOY_TODAY_RE.search(message)
            if today_match:
                local_day = captured_at.date().isoformat()
                energy_today.setdefault(local_day, {})[today_match.group(1)] = float(today_match.group(2))
    return samples, energy_today


def nearest(samples: list[Sample], source: str, meter: str, target: datetime, max_seconds: int = 600) -> Sample | None:
    candidates = [item for item in samples if item.source == source and item.meter == meter]
    if not candidates:
        return None
    best = min(candidates, key=lambda item: abs((item.captured_at - target).total_seconds()))
    if abs((best.captured_at - target).total_seconds()) > max_seconds:
        return None
    return best


def group_samples(samples: list[Sample]) -> dict[tuple[str, str], list[Sample]]:
    grouped: dict[tuple[str, str], list[Sample]] = {}
    for sample in samples:
        grouped.setdefault((sample.source, sample.meter), []).append(sample)
    for rows in grouped.values():
        rows.sort(key=lambda item: item.captured_at)
    return grouped


def nearest_grouped(
    grouped: dict[tuple[str, str], list[Sample]],
    source: str,
    meter: str,
    target: datetime,
    max_seconds: int = 600,
) -> Sample | None:
    candidates = grouped.get((source, meter), [])
    if not candidates:
        return None
    times = [item.captured_at for item in candidates]
    index = bisect_left(times, target)
    nearby: list[Sample] = []
    if index < len(candidates):
        nearby.append(candidates[index])
    if index > 0:
        nearby.append(candidates[index - 1])
    best = min(nearby, key=lambda item: abs((item.captured_at - target).total_seconds()))
    if abs((best.captured_at - target).total_seconds()) > max_seconds:
        return None
    return best


def pair_sense_envoy(grouped: dict[tuple[str, str], list[Sample]], max_seconds: int = 90) -> list[dict[str, Any]]:
    sense_samples = grouped.get(("Sense", "Whole Home"), [])
    pairs: list[dict[str, Any]] = []
    for sense in sense_samples:
        envoy_total = nearest_grouped(grouped, "Envoy", "Consumption Total", sense.captured_at, max_seconds)
        if not envoy_total:
            continue
        envoy_net = nearest_grouped(grouped, "Envoy", "Consumption Net", sense.captured_at, max_seconds)
        envoy_production = nearest_grouped(grouped, "Envoy", "Production", sense.captured_at, max_seconds)
        envoy_storage = nearest_grouped(grouped, "Envoy", "Storage", sense.captured_at, max_seconds)
        row: dict[str, Any] = {
            "senseCapturedAt": sense.captured_at.isoformat(timespec="seconds"),
            "gapSeconds": abs((envoy_total.captured_at - sense.captured_at).total_seconds()),
            "senseKw": sense.kw,
            "envoyTotalKw": envoy_total.kw,
            "envoyMinusSenseKw": envoy_total.kw - sense.kw,
            "senseToEnvoyRatio": sense.kw / envoy_total.kw if envoy_total.kw else None,
        }
        if envoy_net:
            row["envoyNetKw"] = envoy_net.kw
        if envoy_production:
            row["envoyProductionKw"] = envoy_production.kw
        if envoy_storage:
            row["envoyStorageKw"] = envoy_storage.kw
            row["envoyNonBatteryLoadKw"] = envoy_total.kw - abs(envoy_storage.kw)
            row["envoyNonBatteryLoadMinusSenseKw"] = row["envoyNonBatteryLoadKw"] - sense.kw
        pairs.append(row)
    return sorted(pairs, key=lambda item: item["senseCapturedAt"], reverse=True)


def summarize_pairs(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    if not pairs:
        return {"count": 0}
    night = [item for item in pairs if abs(item.get("envoyProductionKw") or 0) < 0.25 and abs(item.get("envoyStorageKw") or 0) < 0.25]
    solar_battery = [
        item
        for item in pairs
        if abs(item.get("envoyProductionKw") or 0) >= 0.25 or abs(item.get("envoyStorageKw") or 0) >= 0.25
    ]

    def bucket(rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {"count": 0}
        non_battery_adjusted = [
            item["envoyNonBatteryLoadMinusSenseKw"]
            for item in rows
            if item.get("envoyNonBatteryLoadMinusSenseKw") is not None
        ]
        return {
            "count": len(rows),
            "avgEnvoyMinusSenseKw": mean(item["envoyMinusSenseKw"] for item in rows),
            "avgSenseToEnvoyRatio": mean(item["senseToEnvoyRatio"] for item in rows if item.get("senseToEnvoyRatio") is not None),
            "avgEnvoyNonBatteryLoadMinusSenseKw": mean(non_battery_adjusted) if non_battery_adjusted else None,
        }

    return {
        "count": len(pairs),
        "all": bucket(pairs),
        "nightNoSolarBattery": bucket(night),
        "solarOrBatteryActive": bucket(solar_battery),
        "recent": pairs[:12],
    }


def load_sense_now_pairing() -> dict[str, Any]:
    if not SENSE_NOW_PAIRING_PATH.exists():
        return {}
    try:
        return json.loads(SENSE_NOW_PAIRING_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def pct_delta(a: float, b: float) -> float | None:
    if b == 0:
        return None
    return (a - b) / b


def load_sce_status() -> dict[str, Any]:
    if not ALL_ENERGY_PATH.exists():
        return {}
    try:
        payload = json.loads(ALL_ENERGY_PATH.read_text())
    except json.JSONDecodeError:
        return {}
    return payload.get("sceGreenButton", {}).get("summary", {})


def load_sce_overlap_count() -> int:
    if not ALL_ENERGY_PATH.exists():
        return 0
    try:
        payload = json.loads(ALL_ENERGY_PATH.read_text())
    except json.JSONDecodeError:
        return 0
    return int(payload.get("overlapPairCount") or 0)


def build_reconciliation() -> dict[str, Any]:
    alarm = load_alarm()
    samples, envoy_today = load_monitor_samples()
    grouped_samples = group_samples(samples)
    sense_envoy_pairs = pair_sense_envoy(grouped_samples)
    instant_pairs: list[dict[str, Any]] = []
    for item in alarm.get("instantWatts", []):
        if item.get("meter") != "Energy Clamp":
            continue
        alarm_at = parse_alarm_time(item)
        alarm_kw = float(item["watts"]) / 1000.0
        envoy_total = nearest_grouped(grouped_samples, "Envoy", "Consumption Total", alarm_at)
        envoy_net = nearest_grouped(grouped_samples, "Envoy", "Consumption Net", alarm_at)
        envoy_production = nearest_grouped(grouped_samples, "Envoy", "Production", alarm_at)
        envoy_storage = nearest_grouped(grouped_samples, "Envoy", "Storage", alarm_at)
        sense = nearest_grouped(grouped_samples, "Sense", "Whole Home", alarm_at)
        row: dict[str, Any] = {
            "alarmCapturedAtLocal": alarm_at.isoformat(timespec="seconds"),
            "alarmKw": alarm_kw,
            "alarmWatts": item["watts"],
        }
        for key, sample in [
            ("envoyTotal", envoy_total),
            ("envoyNet", envoy_net),
            ("envoyProduction", envoy_production),
            ("envoyStorage", envoy_storage),
            ("sense", sense),
        ]:
            if sample:
                row[key] = {
                    "capturedAt": sample.captured_at.isoformat(timespec="seconds"),
                    "gapSeconds": abs((sample.captured_at - alarm_at).total_seconds()),
                    "kw": sample.kw,
                }
        if envoy_total:
            row["envoyMinusAlarmKw"] = envoy_total.kw - alarm_kw
            row["alarmToEnvoyRatio"] = alarm_kw / envoy_total.kw if envoy_total.kw else None
        if sense:
            row["alarmMinusSenseKw"] = alarm_kw - sense.kw
        if envoy_total and envoy_storage and sense:
            row["envoyNonBatteryLoadKw"] = envoy_total.kw - abs(envoy_storage.kw)
            row["envoyNonBatteryLoadMinusSenseKw"] = row["envoyNonBatteryLoadKw"] - sense.kw
        instant_pairs.append(row)

    child_instants: list[dict[str, Any]] = []
    for parent_item in [item for item in alarm.get("instantWatts", []) if item.get("meter") == "Energy Clamp"]:
        parent_at = parse_alarm_time(parent_item)
        children_at = [
            item
            for item in alarm.get("instantWatts", [])
            if item.get("meter") in {"Lava Lamp", "Sideyard Light"}
            and abs((parse_alarm_time(item) - parent_at).total_seconds()) <= 60
        ]
        child_watts = sum(float(item["watts"]) for item in children_at)
        parent_watts = float(parent_item["watts"])
        child_instants.append(
            {
                "alarmCapturedAtLocal": parent_at.isoformat(timespec="seconds"),
                "parentWatts": parent_watts,
                "namedChildWatts": child_watts,
                "unclassifiedWatts": parent_watts - child_watts,
                "namedChildShare": child_watts / parent_watts if parent_watts else None,
            }
        )

    daily_rows: list[dict[str, Any]] = []
    latest_envoy_day = max(envoy_today) if envoy_today else None
    for item in alarm.get("dailyKwh", []):
        if item.get("meter") != "Energy Clamp":
            continue
        # Envoy "energy today" is a same-day rolling counter. Older sparse
        # monitor rows are useful as raw evidence, but not as daily totals.
        envoy = envoy_today.get(item["date"], {}) if item["date"] == latest_envoy_day else {}
        row = {
            "date": item["date"],
            "alarmKwh": item["kwh"],
            "envoyComparable": item["date"] == latest_envoy_day,
            "envoyConsumptionTotalKwhTodayLatest": envoy.get("Consumption Total"),
            "envoyConsumptionNetKwhTodayLatest": envoy.get("Consumption Net"),
            "envoyProductionKwhTodayLatest": envoy.get("Production"),
        }
        if row["envoyConsumptionTotalKwhTodayLatest"] is not None:
            row["envoyTotalMinusAlarmKwh"] = row["envoyConsumptionTotalKwhTodayLatest"] - item["kwh"]
        daily_rows.append(row)

    period = alarm.get("periodKwh", [])
    children = [item for item in period if item.get("meter") in {"Lava Lamp", "Sideyard Light"}]
    parent = {item["period"]: item["kwh"] for item in period if item.get("meter") == "Energy Clamp"}
    child_summary = []
    for window in ["24h", "7d", "21d", "6m", "12m"]:
        child_total = sum(float(item["kwh"]) for item in children if item["period"] == window)
        parent_kwh = parent.get(window)
        child_summary.append(
            {
                "period": window,
                "parentKwh": parent_kwh,
                "namedChildKwh": child_total,
                "unclassifiedKwh": parent_kwh - child_total if parent_kwh is not None else None,
                "namedChildShare": child_total / parent_kwh if parent_kwh else None,
            }
        )

    return {
        "generatedAt": datetime.now(timezone.utc).astimezone(LOCAL_TZ).isoformat(timespec="seconds"),
        "alarmSource": {
            "capturedAtLocal": alarm.get("capturedAtLocal"),
            "timeZoneAssumption": alarm.get("timeZoneAssumption"),
            "dashboard": alarm.get("dashboard", {}),
        },
        "instantPairs": instant_pairs,
        "instantChildSummary": child_instants,
        "senseNowPairing": load_sense_now_pairing(),
        "senseEnvoySummary": summarize_pairs(sense_envoy_pairs),
        "dailyRows": daily_rows,
        "childSummary": child_summary,
        "sceGreenButtonSummary": load_sce_status(),
        "sceOverlapPairCount": load_sce_overlap_count(),
    }


def fmt(value: float | None, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1%}"


def write_report(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    lines = [
        "# Meter Reconciliation",
        "",
        f"- Generated: `{payload['generatedAt']}`",
        f"- Alarm.com source captured: `{payload['alarmSource'].get('capturedAtLocal')}`",
        f"- Alarm.com timestamp assumption: {payload['alarmSource'].get('timeZoneAssumption')}",
        "",
        "## Instantaneous Pairing",
        "",
        "| Alarm time | Alarm kW | Envoy total kW | Envoy - Alarm kW | Alarm / Envoy | Sense kW | Alarm - Sense kW | Envoy net kW | Production kW | Storage kW |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["instantPairs"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row['alarmCapturedAtLocal']}`",
                    fmt(row.get("alarmKw")),
                    fmt(row.get("envoyTotal", {}).get("kw")),
                    fmt(row.get("envoyMinusAlarmKw")),
                    pct(row.get("alarmToEnvoyRatio")),
                    fmt(row.get("sense", {}).get("kw")),
                    fmt(row.get("alarmMinusSenseKw")),
                    fmt(row.get("envoyNet", {}).get("kw")),
                    fmt(row.get("envoyProduction", {}).get("kw")),
                    fmt(row.get("envoyStorage", {}).get("kw")),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Instant Alarm.com Submetering",
            "",
            "| Alarm time | Parent W | Named child W | Unclassified W | Named child share |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in payload["instantChildSummary"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row['alarmCapturedAtLocal']}`",
                    fmt(row.get("parentWatts"), 0),
                    fmt(row.get("namedChildWatts"), 0),
                    fmt(row.get("unclassifiedWatts"), 0),
                    pct(row.get("namedChildShare")),
                ]
            )
            + " |"
        )

    sense_envoy = payload.get("senseEnvoySummary") or {}
    lines.extend(
        [
            "",
            "## Sense / Envoy Regimes",
            "",
            "- Sense `Watts` is treated as whole-home usage/load. Sense solar is a separate production device stream when available.",
            "- Sense total appears to exclude Enphase battery charge/discharge, so the closest Envoy comparator is `Consumption Total - abs(Storage)`.",
            "- Envoy `Consumption Net` is grid import/export after solar, not a Sense load comparator.",
            "",
            "| Regime | Pairs | Avg raw Envoy total - Sense kW | Avg Sense / raw Envoy total | Avg Envoy non-battery load - Sense kW |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for label, key in [
        ("All paired samples", "all"),
        ("Night / low solar / low battery", "nightNoSolarBattery"),
        ("Solar or battery active", "solarOrBatteryActive"),
    ]:
        row = sense_envoy.get(key) or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    label,
                    str(row.get("count", 0)),
                    fmt(row.get("avgEnvoyMinusSenseKw")),
                    pct(row.get("avgSenseToEnvoyRatio")),
                    fmt(row.get("avgEnvoyNonBatteryLoadMinusSenseKw")),
                ]
            )
            + " |"
        )

    sense_now = payload.get("senseNowPairing") or {}
    if sense_now:
        envoy = sense_now.get("envoy") or {}
        devices = sense_now.get("senseDevices") or []
        solar = next((item for item in devices if item.get("id") == "solar"), {})
        other = next((item for item in devices if item.get("id") == "unknown"), {})
        always_on = next((item for item in devices if item.get("id") == "always_on"), {})
        lines.extend(
            [
                "",
                "## Sense One-Shot Detail",
                "",
                f"- Sense total: `{fmt(sense_now.get('senseKw'))}` kW at `{sense_now.get('senseCapturedAtLocal')}`.",
                f"- Sense device list: Solar `{fmt((solar.get('watts') or 0) / 1000.0)}` kW, Other `{fmt((other.get('watts') or 0) / 1000.0)}` kW, Always On `{fmt((always_on.get('watts') or 0) / 1000.0)}` kW.",
                f"- Envoy at nearest sample: Total `{fmt((envoy.get('Consumption Total') or {}).get('kw'))}` kW, Production `{fmt((envoy.get('Production') or {}).get('kw'))}` kW, Net `{fmt((envoy.get('Consumption Net') or {}).get('kw'))}` kW, Storage `{fmt((envoy.get('Storage') or {}).get('kw'))}` kW.",
                f"- Envoy non-battery load estimate (`total - abs(storage)`) minus Sense: `{fmt(sense_now.get('envoyTotalMinusStorageAbsMinusSenseKw'))}` kW.",
                "- The Sense device list shows Solar separately from the Sense total load reading, so Sense solar should be reconciled to Envoy Production rather than added to grid import.",
            ]
        )

    dashboard = payload.get("alarmSource", {}).get("dashboard") or {}
    if dashboard:
        lines.extend(
            [
                "",
                "## Alarm.com Budget Context",
                "",
                f"- Dashboard current period: `{fmt(dashboard.get('monthToDateKwh'), 0)}` kWh; same point last month: `{fmt(dashboard.get('samePointLastMonthKwh'), 0)}` kWh.",
                f"- Energy Clamp goal: `{fmt(dashboard.get('energyClampBudgetKwh'), 0)}` kWh; projected: `{fmt(dashboard.get('energyClampProjectedKwh'), 0)}` kWh; last billing: `{fmt(dashboard.get('energyClampLastBillingKwh'), 0)}` kWh; average billing: `{fmt(dashboard.get('energyClampAverageBillingKwh'), 0)}` kWh.",
                f"- Projected over budget: `{fmt((dashboard.get('energyClampProjectedKwh') or 0) - (dashboard.get('energyClampBudgetKwh') or 0), 0)}` kWh.",
            ]
        )

    lines.extend(
        [
            "",
            "## Named vs Unclassified Alarm.com Loads",
            "",
            "| Period | Parent kWh | Named child kWh | Unclassified kWh | Named child share |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in payload["childSummary"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row['period']}`",
                    fmt(row.get("parentKwh"), 3),
                    fmt(row.get("namedChildKwh"), 3),
                    fmt(row.get("unclassifiedKwh"), 3),
                    pct(row.get("namedChildShare")),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Daily Alarm.com vs Envoy",
            "",
            "- Envoy daily counters are only shown for the latest monitor day; older Alarm.com daily bars are retained but not paired to sparse historical Envoy counter rows.",
            "",
            "| Date | Alarm kWh | Latest Envoy total today kWh | Envoy total - Alarm kWh | Envoy net today kWh | Envoy production today kWh |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["dailyRows"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row['date']}`",
                    fmt(row.get("alarmKwh")),
                    fmt(row.get("envoyConsumptionTotalKwhTodayLatest")),
                    fmt(row.get("envoyTotalMinusAlarmKwh")),
                    fmt(row.get("envoyConsumptionNetKwhTodayLatest")),
                    fmt(row.get("envoyProductionKwhTodayLatest")),
                ]
            )
            + " |"
        )

    sce = payload.get("sceGreenButtonSummary") or {}
    sce_overlap_count = payload.get("sceOverlapPairCount") or 0
    sce_status = (
        f"- Current SCE Green Button data overlaps the live monitor window with `{sce_overlap_count}` interval pairs."
        if sce_overlap_count
        else "- Current SCE Green Button data does not overlap the live monitor window; fresh SCE interval export is needed for live reconciliation."
    )
    lines.extend(
        [
            "",
            "## SCE Status",
            "",
            f"- Green Button coverage: `{sce.get('coverageStart') or 'n/a'}` to `{sce.get('coverageEnd') or 'n/a'}`",
            f"- Green Button net import: `{fmt(sce.get('netImportKwh'), 1)}` kWh",
            sce_status,
            "",
            "## Interpretation",
            "",
            "- Envoy Consumption Total is the best whole-home site-load reference currently available.",
            "- Alarm.com Energy Clamp tracks Envoy Consumption Total closely for the 10:59 local instant.",
            "- Envoy Consumption Net is grid import/export after solar, not house load.",
            "- Sense `Watts` is also a whole-home usage/load stream, not grid import/export.",
            "- Sense total appears to exclude Enphase battery charge/discharge. Compare it first to Envoy non-battery load (`Consumption Total - abs(Storage)`), then to raw Envoy Consumption Total for battery-inclusive site load.",
            "- Sense Solar is a separate production stream. Compare it to Envoy Production when the Sense device list is available.",
            "- Recent battery-adjusted Sense/Envoy load pairs are close enough to use Sense as a secondary non-battery load signal, but Envoy remains the stronger source of truth because it also exposes production, net grid, and storage consistently.",
            "- Alarm.com child meters explain only a tiny share of Energy Clamp consumption; most usage remains unclassified under the parent clamp.",
            "- SCE is the utility-bill truth source for grid import/export where Green Button intervals overlap the monitor window.",
        ]
    )
    OUT_REPORT.write_text("\n".join(lines) + "\n")


def main() -> int:
    payload = build_reconciliation()
    write_report(payload)
    print(OUT_REPORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
