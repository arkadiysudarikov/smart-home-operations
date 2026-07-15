#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
DB_PATH = DATA_DIR / "smart_home.sqlite"
LATEST_PATH = DATA_DIR / "latest_energy_observability.json"
HISTORY_RETENTION_DAYS = 90
SCE_INTERVAL_PATH = DATA_DIR / "sce_usage_intervals.csv"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def rounded(value: Any, digits: int = 3) -> float | None:
    parsed = num(value)
    return round(parsed, digits) if parsed is not None else None


def subtract(left: Any, right: Any) -> float | None:
    left_num = num(left)
    right_num = num(right)
    if left_num is None or right_num is None:
        return None
    return round(left_num - right_num, 3)


def alarm_daily_rows(alarm: dict[str, Any]) -> dict[str, float]:
    rows: dict[str, float] = {}
    for item in alarm.get("dailyKwh") or []:
        if not isinstance(item, dict) or item.get("meter") != "Energy Clamp":
            continue
        date = item.get("date")
        value = num(item.get("kwh"))
        if isinstance(date, str) and value is not None:
            rows[date] = value
    return rows


def sce_daily_rows(path: Path = SCE_INTERVAL_PATH) -> dict[str, dict[str, float]]:
    rows: dict[str, dict[str, float]] = {}
    if not path.exists():
        return rows
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for item in csv.DictReader(handle):
                start = str(item.get("start") or "")
                if "T" not in start:
                    continue
                date = start.split("T", 1)[0]
                daily = rows.setdefault(date, {"delivered": 0.0, "received": 0.0, "net": 0.0})
                try:
                    daily["delivered"] += float(item.get("delivered_kwh") or 0)
                    daily["received"] += float(item.get("received_kwh") or 0)
                    daily["net"] += float(item.get("net_import_kwh") or 0)
                except (TypeError, ValueError):
                    continue
    except (OSError, csv.Error):
        return {}
    return rows


def build_daily_comparison(
    combined: dict[str, Any], alarm: dict[str, Any], sce_by_date: dict[str, dict[str, float]] | None = None
) -> list[dict[str, Any]]:
    alarm_by_date = alarm_daily_rows(alarm)
    sce_by_date = sce_by_date or {}
    combined_by_date = {
        item.get("date"): item
        for item in combined.get("dailySummary") or []
        if isinstance(item, dict) and isinstance(item.get("date"), str)
    }
    rows: list[dict[str, Any]] = []
    for date in sorted(set(combined_by_date) | set(alarm_by_date) | set(sce_by_date)):
        source = combined_by_date.get(date) or {}
        sce = sce_by_date.get(date) or {}
        row = {
            "date": date,
            "alarmClampKwh": rounded(alarm_by_date.get(date)),
            "sceDeliveredKwh": rounded(sce.get("delivered") if sce else source.get("sceDeliveredKwh")),
            "sceReceivedKwh": rounded(sce.get("received") if sce else source.get("sceReceivedKwh")),
            "sceNetImportKwh": rounded(sce.get("net") if sce else source.get("sceNetImportKwh")),
            "envoySiteLoadKwh": rounded(source.get("envoySiteLoadKwh")),
            "senseLoadKwh": rounded(source.get("senseLoadKwh")),
            "envoySolarKwh": rounded(source.get("envoySolarProductionKwh")),
            "senseSolarKwh": rounded(source.get("senseSolarProductionKwh")),
            "sceComplete": source.get("sceComplete", True if sce else None),
            "envoyComplete": source.get("envoyComplete"),
            "senseComplete": source.get("senseComplete"),
        }
        row.update(
            {
                "alarmMinusSenseKwh": subtract(row["alarmClampKwh"], row["senseLoadKwh"]),
                "envoyMinusSenseKwh": subtract(row["envoySiteLoadKwh"], row["senseLoadKwh"]),
                "alarmMinusSceDeliveredKwh": subtract(row["alarmClampKwh"], row["sceDeliveredKwh"]),
            }
        )
        availability = {
            "Alarm.com": row["alarmClampKwh"] is not None,
            "SCE": row["sceDeliveredKwh"] is not None and row.get("sceComplete") is not False,
            "Envoy": row["envoySiteLoadKwh"] is not None and row.get("envoyComplete") is not False,
            "Sense": row["senseLoadKwh"] is not None and row.get("senseComplete") is not False,
        }
        row["availableSourceCount"] = sum(availability.values())
        row["partialSources"] = [label for label, available in availability.items() if not available]
        rows.append(row)
    return rows


def live_summary(latest: dict[str, Any], sense_now: dict[str, Any], alarm: dict[str, Any]) -> dict[str, Any]:
    metrics = (((latest.get("homebridge") or {}).get("logs") or {}).get("latestMetrics") or {})
    dashboard = alarm.get("dashboard") or {}
    devices = sense_now.get("devices") or []
    sense_solar_watts = next(
        (num(item.get("watts")) for item in devices if isinstance(item, dict) and item.get("id") == "solar"),
        None,
    )
    sense_load_watts = num(sense_now.get("watts"))
    return {
        "capturedAt": sense_now.get("capturedAt") or latest.get("captured_at") or latest.get("generatedAt"),
        "envoyProductionKw": rounded(metrics.get("enphase_production_kw")),
        "envoySiteLoadKw": rounded(metrics.get("enphase_consumption_total_kw")),
        "envoyGridNetKw": rounded(metrics.get("enphase_consumption_net_kw")),
        "envoyStorageKw": rounded(metrics.get("enphase_storage_kw")),
        "batteryPercent": rounded(metrics.get("enphase_backup_percent"), 1),
        "batteryCharging": bool(metrics.get("enphase_battery_charging")),
        "batteryDischarging": bool(metrics.get("enphase_battery_discharging")),
        "senseLoadKw": rounded(sense_load_watts / 1000 if sense_load_watts is not None else None),
        "senseSolarKw": rounded(sense_solar_watts / 1000 if sense_solar_watts is not None else None),
        "alarmMonthToDateKwh": rounded(dashboard.get("monthToDateKwh"), 1),
        "alarmProjectedKwh": rounded(dashboard.get("energyClampProjectedKwh"), 1),
        "alarmBudgetKwh": rounded(dashboard.get("energyClampBudgetKwh"), 1),
    }


def peak_events(all_energy: dict[str, Any], limit: int = 12) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in all_energy.get("overlapPairs") or []:
        if not isinstance(item, dict):
            continue
        delivered = num(item.get("sceDeliveredKwh"))
        if delivered is None:
            continue
        rows.append(
            {
                "start": item.get("start"),
                "end": item.get("end"),
                "sceImportKw": round(delivered * 4, 3),
                "sceExportKw": rounded(value * 4) if (value := num(item.get("sceReceivedKwh"))) is not None else None,
                "envoySiteLoadKw": rounded(value * 4) if (value := num(item.get("envoyConsumptionTotalKwhEstimate"))) is not None else None,
                "senseLoadKw": rounded(value * 4) if (value := num(item.get("senseKwhEstimate"))) is not None else None,
            }
        )
    return sorted(rows, key=lambda item: item["sceImportKw"], reverse=True)[:limit]


def source_quality(combined: dict[str, Any], all_energy: dict[str, Any], daily: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = [item for item in combined.get("sourceStatus") or [] if isinstance(item, dict)]
    meter_sources = {"SCE", "Envoy", "Sense", "Alarm.com", "ChargePoint"}
    degraded = [
        item.get("source")
        for item in statuses
        if item.get("source") in meter_sources and item.get("status") not in {"fresh", "available"}
    ]
    overlap = int(all_energy.get("overlapPairCount") or 0)
    comparable_days = sum(1 for row in daily if row.get("availableSourceCount", 0) >= 3)
    issues: list[dict[str, str]] = []
    if degraded:
        issues.append(
            {
                "severity": "warning",
                "title": "Source freshness limits comparisons",
                "detail": ", ".join(str(item) for item in degraded),
            }
        )
    if overlap == 0:
        issues.append(
            {
                "severity": "warning",
                "title": "No interval overlap",
                "detail": "SCE and monitor intervals cannot be reconciled yet.",
            }
        )
    if daily and comparable_days < max(1, round(len(daily) * 0.75)):
        issues.append(
            {
                "severity": "warning",
                "title": "Historical comparison coverage is limited",
                "detail": f"{comparable_days} of {len(daily)} retained days have at least three complete sources.",
            }
        )
    source_meta = combined.get("sources") or {}
    sce_coverage_date = str((source_meta.get("sce") or {}).get("coverageEnd") or "").split("T")[0]
    lagging_monitors: list[str] = []
    for label, key in (("Envoy", "envoy"), ("Sense", "sense")):
        monitor_date = str((source_meta.get(key) or {}).get("end") or "").split("T")[0]
        if sce_coverage_date and monitor_date and monitor_date < sce_coverage_date:
            lagging_monitors.append(f"{label} through {monitor_date}")
    if lagging_monitors:
        issues.append(
            {
                "severity": "warning",
                "title": "Monitor history trails utility coverage",
                "detail": f"SCE through {sce_coverage_date}; " + ", ".join(lagging_monitors),
            }
        )
    return {
        "status": "ready" if not issues else "degraded",
        "overlapPairCount": overlap,
        "comparableDayCount": comparable_days,
        "issues": issues,
        "sourceSemantics": [
            {"source": "SCE", "measurement": "Utility grid import/export", "use": "Billing and net-grid truth"},
            {"source": "Envoy", "measurement": "Site load, solar, grid, and storage", "use": "System energy flow"},
            {"source": "Sense", "measurement": "Non-battery house load and solar", "use": "Device and load attribution"},
            {"source": "Alarm.com", "measurement": "Broad Energy Clamp consumption", "use": "Budget and gross-load trend"},
            {"source": "ChargePoint", "measurement": "Completed EV sessions", "use": "Historical EV allocation, not live state"},
        ],
    }


def persist_observation(generated_at: str, live: dict[str, Any], combined: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """
            create table if not exists energy_observations (
              captured_at text primary key,
              envoy_production_kw real,
              envoy_site_load_kw real,
              envoy_grid_net_kw real,
              envoy_storage_kw real,
              battery_percent real,
              battery_charging integer,
              battery_discharging integer,
              sense_load_kw real,
              sense_solar_kw real,
              alarm_mtd_kwh real,
              alarm_projected_kwh real,
              energy_alert_count integer,
              active_states_json text not null
            )
            """
        )
        db.execute(
            """
            insert or replace into energy_observations values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                generated_at,
                live.get("envoyProductionKw"),
                live.get("envoySiteLoadKw"),
                live.get("envoyGridNetKw"),
                live.get("envoyStorageKw"),
                live.get("batteryPercent"),
                int(bool(live.get("batteryCharging"))),
                int(bool(live.get("batteryDischarging"))),
                live.get("senseLoadKw"),
                live.get("senseSolarKw"),
                live.get("alarmMonthToDateKwh"),
                live.get("alarmProjectedKwh"),
                len(combined.get("alerts") or []),
                json.dumps(combined.get("states") or []),
            ),
        )
        cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_RETENTION_DAYS)).isoformat(
            timespec="seconds"
        )
        db.execute("delete from energy_observations where julianday(captured_at) < julianday(?)", (cutoff,))
        db.commit()


def build_payload() -> dict[str, Any]:
    combined = load_json(DATA_DIR / "latest_combined_energy_monitor.json")
    all_energy = load_json(DATA_DIR / "latest_all_energy_pairs.json")
    latest = load_json(DATA_DIR / "latest.json")
    sense_now = load_json(DATA_DIR / "sense_now_latest.json")
    alarm = load_json(ROOT / "config" / "alarm_energy_readings.json")
    generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    daily = build_daily_comparison(combined, alarm, sce_daily_rows())
    live = live_summary(latest, sense_now, alarm)
    payload = {
        "ok": bool(combined),
        "generatedAt": generated_at,
        "historyRetentionDays": HISTORY_RETENTION_DAYS,
        "live": live,
        "dailyComparison": daily,
        "peakEvents": peak_events(all_energy),
        "quality": source_quality(combined, all_energy, daily),
        "sourceStatus": combined.get("sourceStatus") or [],
        "states": combined.get("states") or [],
    }
    persist_observation(generated_at, live, combined)
    return payload


def fmt(value: Any, digits: int = 1) -> str:
    parsed = num(value)
    return "n/a" if parsed is None else f"{parsed:.{digits}f}"


def write_report(payload: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    live = payload.get("live") or {}
    quality = payload.get("quality") or {}
    lines = [
        "# Energy Observability",
        "",
        f"- Generated: `{payload.get('generatedAt')}`",
        f"- Quality: `{quality.get('status')}`",
        f"- SCE/monitor interval overlap: `{quality.get('overlapPairCount')}` pairs",
        f"- Comparable daily rows: `{quality.get('comparableDayCount')}`",
        f"- Live solar / site load / grid: `{fmt(live.get('envoyProductionKw'))}` / `{fmt(live.get('envoySiteLoadKw'))}` / `{fmt(live.get('envoyGridNetKw'))}` kW",
        f"- Battery: `{fmt(live.get('batteryPercent'), 0)}`%; storage `{fmt(live.get('envoyStorageKw'))}` kW",
        f"- Sense non-battery load: `{fmt(live.get('senseLoadKw'))}` kW",
        "",
        "## Source Semantics",
        "",
        "| Source | Measurement | Best use |",
        "|---|---|---|",
    ]
    for item in quality.get("sourceSemantics") or []:
        lines.append(f"| {item['source']} | {item['measurement']} | {item['use']} |")
    lines.extend(
        [
            "",
            "## Recent Daily Comparison",
            "",
            "| Date | Alarm clamp | Sense load | Envoy site load | SCE delivered | SCE received | Net import |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in (payload.get("dailyComparison") or [])[-14:]:
        lines.append(
            f"| {row['date']} | {fmt(row.get('alarmClampKwh'))} | {fmt(row.get('senseLoadKwh'))} | "
            f"{fmt(row.get('envoySiteLoadKwh'))} | {fmt(row.get('sceDeliveredKwh'))} | "
            f"{fmt(row.get('sceReceivedKwh'))} | {fmt(row.get('sceNetImportKwh'))} |"
        )
    (REPORT_DIR / "energy_observability.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    payload = build_payload()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    write_report(payload)
    print(LATEST_PATH)
    print(REPORT_DIR / "energy_observability.md")
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
