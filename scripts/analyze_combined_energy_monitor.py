#!/usr/bin/env python3
from __future__ import annotations

import json
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "sources.json"
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
OUT_JSON = DATA_DIR / "latest_combined_energy_monitor.json"
OUT_REPORT = REPORT_DIR / "combined_energy_monitor.md"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ)


def num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fmt(value: Any, digits: int = 1) -> str:
    value = num(value)
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def pct(value: Any) -> str:
    value = num(value)
    if value is None:
        return "n/a"
    return f"{value:.1%}"


def alert(severity: str, title: str, detail: str) -> dict[str, str]:
    return {"severity": severity, "title": title, "detail": detail}


def alarm_cache_comparison_status(now: datetime, thresholds: dict[str, Any]) -> dict[str, Any]:
    payload = load_json(DATA_DIR / "latest_alarm_homebridge_state.json")
    stale_count = payload.get("staleCount")
    stale_count_value = int(stale_count) if isinstance(stale_count, (int, float)) else None
    generated_at = parse_dt(payload.get("generatedAt"))
    age_hours_value = (now - generated_at).total_seconds() / 3600 if generated_at else None
    max_age_hours = float(thresholds.get("source_status_stale_hours", 24))
    current = age_hours_value is not None and age_hours_value < max_age_hours
    return {
        "generatedAt": payload.get("generatedAt"),
        "ageHours": age_hours_value,
        "staleCount": stale_count_value,
        "current": current,
        "healthy": bool(current and stale_count_value == 0),
    }


def latest_charge_session(chargepoint: dict[str, Any]) -> dict[str, Any]:
    sessions = load_json(DATA_DIR / "chargepoint_sessions.json").get("sessions", [])
    if sessions:
        return sessions[0]
    pairs = chargepoint.get("pairs") or []
    if pairs:
        row = pairs[0]
        return {
            "startAt": row.get("chargepointStartAt"),
            "endAt": row.get("chargepointEndAt"),
            "energyKwh": row.get("chargepointEnergyKwh"),
        }
    return {}


def live_sense_ev_watts(sense_now: dict[str, Any], now: datetime, max_age_minutes: float = 5) -> float | None:
    captured_at = parse_dt(sense_now.get("capturedAt"))
    if sense_now.get("ok") is not True or sense_now.get("online") is False or captured_at is None:
        return None
    age_minutes = (now - captured_at).total_seconds() / 60
    if age_minutes < 0 or age_minutes > max_age_minutes:
        return None
    for device in sense_now.get("devices") or []:
        if device.get("id") == "category-ev":
            return num(device.get("watts"))
    return None


def age_hours(now: datetime, raw: str | None) -> float | None:
    parsed = parse_dt(raw)
    if not parsed:
        return None
    return (now - parsed).total_seconds() / 3600


def alarm_energy_capture_at(alarm: dict[str, Any]) -> str | None:
    energy = alarm.get("energy") if isinstance(alarm.get("energy"), dict) else {}
    return alarm.get("capturedAtLocal") or energy.get("capturedAtLocal")


def fresher_alarm_energy(base_alarm: dict[str, Any], latest_alarm: dict[str, Any]) -> dict[str, Any]:
    energy = latest_alarm.get("energy") if isinstance(latest_alarm.get("energy"), dict) else {}
    energy_captured = energy.get("capturedAtLocal")
    if not energy_captured:
        return base_alarm
    base_captured = alarm_energy_capture_at(base_alarm)
    energy_dt = parse_dt(energy_captured)
    base_dt = parse_dt(base_captured)
    if base_dt and (not energy_dt or energy_dt <= base_dt):
        return base_alarm
    merged = dict(base_alarm)
    merged["capturedAtLocal"] = energy_captured
    if isinstance(energy.get("dashboard"), dict):
        merged["dashboard"] = energy["dashboard"]
    merged["sourceCapture"] = "latest_alarm_com.energy"
    return merged


def alarm_dashboard_comparison(alarm: dict[str, Any], mismatch_threshold: float) -> dict[str, Any]:
    raw_mismatch = num(alarm.get("dailyTotalMinusDashboardMtdKwh"))
    daily_total = num(alarm.get("dailyTotalKwh"))
    dashboard_total = num((alarm.get("dashboard") or {}).get("monthToDateKwh"))
    period_21d = num((alarm.get("periodKwh") or {}).get("21d"))
    rows = [row for row in alarm.get("dailyRows") or [] if row.get("date")]
    dates = sorted({str(row["date"]) for row in rows})
    partial_21d_window = False

    if (
        raw_mismatch is not None
        and daily_total is not None
        and dashboard_total is not None
        and period_21d is not None
        and daily_total + mismatch_threshold <= dashboard_total
        and abs(daily_total - period_21d) <= 1.0
    ):
        partial_21d_window = True

    return {
        "rawMismatchKwh": raw_mismatch,
        "effectiveMismatchKwh": None if partial_21d_window else raw_mismatch,
        "dailyTotalKwh": daily_total,
        "dashboardCurrentPeriodKwh": dashboard_total,
        "period21dKwh": period_21d,
        "partialCoverage": partial_21d_window,
        "dailyRowCount": len(rows),
        "dailyStart": dates[0] if dates else None,
        "dailyEnd": dates[-1] if dates else None,
        "status": "partial_21d_window" if partial_21d_window else ("compared" if raw_mismatch is not None else "missing"),
    }


def status_label(age: float | None, stale_hours: float) -> str:
    if age is None:
        return "missing"
    if age >= stale_hours:
        return "stale"
    return "fresh"


def latest_sce_file_modified(all_energy: dict[str, Any]) -> datetime | None:
    modified: list[datetime] = []
    for item in (all_energy.get("sceGreenButton") or {}).get("files") or []:
        parsed = parse_dt(item.get("modified"))
        if parsed:
            modified.append(parsed)
    return max(modified) if modified else None


def sce_status_label(
    now: datetime,
    all_energy: dict[str, Any],
    coverage_age: float | None,
    thresholds: dict[str, Any],
) -> str:
    stale_hours = float(thresholds.get("sce_interval_stale_hours", thresholds.get("sce_interval_stale_days", 30) * 24))
    if coverage_age is None:
        return "missing"
    if coverage_age < stale_hours:
        return "fresh"

    lag_hours = float(thresholds.get("sce_interval_normal_lag_hours", 48))
    fresh_export_hours = float(thresholds.get("sce_fresh_export_grace_hours", 24))
    latest_modified = latest_sce_file_modified(all_energy)
    export_age = (now - latest_modified).total_seconds() / 3600 if latest_modified else None
    if coverage_age <= lag_hours and export_age is not None and export_age <= fresh_export_hours:
        return "lagging"
    return "stale"


def effective_sce_summary(all_energy: dict[str, Any]) -> dict[str, Any]:
    green_button = dict((all_energy.get("sceGreenButton") or {}).get("summary", {}) or {})
    api = load_json(DATA_DIR / "latest_sce_api.json")
    api_end = parse_dt(api.get("coverageEnd"))
    green_end = parse_dt(green_button.get("coverageEnd"))
    if api.get("ok") is True and api_end and (not green_end or api_end > green_end):
        return {
            **green_button,
            "coverageStart": api.get("coverageStart") or green_button.get("coverageStart"),
            "coverageEnd": api.get("coverageEnd"),
            "intervalCount": api.get("intervalRows") or green_button.get("intervalCount"),
            "source": "UtilityAPI",
            "sourceFile": api.get("file"),
            "sourceFinishedAt": api.get("finishedAt"),
        }
    green_button.setdefault("source", "Green Button")
    return green_button


def sce_monitor_coverage(sce_summary: dict[str, Any], all_energy: dict[str, Any]) -> dict[str, Any]:
    sce_end = parse_dt(sce_summary.get("coverageEnd"))
    monitor_starts = [
        parsed
        for name, summary in (all_energy.get("smartHomeMonitor") or {}).items()
        if name == "sense" or name.startswith("envoy:")
        if (parsed := parse_dt((summary or {}).get("start"))) is not None
    ]
    if sce_end is None or not monitor_starts:
        return {}
    monitor_start = min(monitor_starts)
    gap_days = (monitor_start - sce_end).total_seconds() / 86400
    return {
        "sceEnd": sce_end.isoformat(timespec="seconds"),
        "monitorStart": monitor_start.isoformat(timespec="seconds"),
        "gapDays": gap_days,
        "overlaps": gap_days <= 0,
    }


def live_envoy_source() -> dict[str, Any] | None:
    envoy = load_json(DATA_DIR / "latest_envoy_direct.json")
    if not envoy.get("finishedAt"):
        return None
    status = str(envoy.get("status") or "")
    if envoy.get("ok") is True and status == "live":
        status = "fresh"
    return {
        "status": status or "missing",
        "timestamp": envoy.get("finishedAt"),
        "detail": f"{envoy.get('host') or 'n/a'} {envoy.get('serialNumber') or ''}".strip(),
    }


def live_sense_source() -> dict[str, Any] | None:
    sense = load_json(DATA_DIR / "sense_now_latest.json")
    if not sense.get("capturedAt"):
        return None
    if sense.get("online") is False:
        status = "offline"
    elif sense.get("ok") is False:
        status = "failed"
    else:
        status = "fresh"
    return {
        "status": status,
        "timestamp": sense.get("capturedAt"),
        "detail": sense.get("connectionState") or sense.get("capturedAt"),
    }


def build_source_status(
    now: datetime,
    thresholds: dict[str, Any],
    all_energy: dict[str, Any],
    bill_home: dict[str, Any],
    chargepoint_sessions: dict[str, Any],
    chargepoint_refresh: dict[str, Any],
    alarm: dict[str, Any],
    energy_costs: dict[str, Any],
) -> list[dict[str, Any]]:
    stale_hours = float(thresholds.get("source_status_stale_hours", 24))
    chargepoint_stale_hours = float(thresholds.get("chargepoint_refresh_stale_hours", 24))
    sce_summary = effective_sce_summary(all_energy)
    sce_age = age_hours(now, sce_summary.get("coverageEnd"))
    sce_status = sce_status_label(now, all_energy, sce_age, thresholds)
    envoy_live = live_envoy_source()
    sense_live = live_sense_source()

    cp_refresh_status = str(chargepoint_refresh.get("status") or "missing")
    if chargepoint_refresh.get("ok") is True:
        cp_status = status_label(age_hours(now, chargepoint_sessions.get("capturedAt")), chargepoint_stale_hours)
    elif cp_refresh_status in {"fresh_enough", "downloaded"} and chargepoint_sessions.get("capturedAt"):
        cp_status = status_label(age_hours(now, chargepoint_sessions.get("capturedAt")), chargepoint_stale_hours)
    elif chargepoint_sessions.get("capturedAt"):
        cp_status = "fallback"
    else:
        cp_status = "missing"

    envoy_fallback_end = (bill_home.get("envoy") or {}).get("coverage", {}).get("end")
    sense_fallback_end = (bill_home.get("sense") or {}).get("end")
    envoy_timestamp = envoy_live.get("timestamp") if envoy_live else envoy_fallback_end
    sense_timestamp = sense_live.get("timestamp") if sense_live else sense_fallback_end
    rows = [
        {
            "source": "Envoy",
            "status": envoy_live.get("status") if envoy_live else ("fresh" if envoy_fallback_end else "missing"),
            "ageHours": age_hours(now, envoy_timestamp),
            "detail": envoy_live.get("detail") if envoy_live else envoy_fallback_end,
        },
        {
            "source": "Sense",
            "status": sense_live.get("status") if sense_live else ("fresh" if sense_fallback_end else "missing"),
            "ageHours": age_hours(now, sense_timestamp),
            "detail": sense_live.get("detail") if sense_live else sense_fallback_end,
        },
        {
            "source": "SCE",
            "status": sce_status,
            "ageHours": sce_age,
            "detail": sce_summary.get("coverageEnd"),
        },
        {
            "source": "ChargePoint",
            "status": cp_status,
            "ageHours": age_hours(now, chargepoint_sessions.get("capturedAt")),
            "detail": cp_refresh_status,
        },
        {
            "source": "Alarm.com",
            "status": status_label(age_hours(now, alarm_energy_capture_at(alarm)), stale_hours),
            "ageHours": age_hours(now, alarm_energy_capture_at(alarm)),
            "detail": alarm_energy_capture_at(alarm),
        },
        {
            "source": "Energy costs",
            "status": status_label(age_hours(now, energy_costs.get("generatedAt")), stale_hours),
            "ageHours": age_hours(now, energy_costs.get("generatedAt")),
            "detail": energy_costs.get("generatedAt"),
        },
    ]
    for row in rows:
        if (
            row["source"] in {"Envoy", "Sense"}
            and row.get("ageHours") is not None
            and row.get("status") not in {"failed", "offline"}
        ):
            row["status"] = status_label(row.get("ageHours"), stale_hours)
    return rows


SOURCE_ROLES = [
    {
        "source": "SCE",
        "role": "Utility grid import/export and billing truth",
        "useFor": "Delivered/received kWh, bill reconciliation, rates",
    },
    {
        "source": "Envoy",
        "role": "Primary site energy model",
        "useFor": "Site load, solar production, grid net, storage",
    },
    {
        "source": "Sense",
        "role": "Secondary non-battery load and solar cross-check",
        "useFor": "House load excluding Enphase storage effects, Sense solar trend checks",
    },
    {
        "source": "Alarm.com",
        "role": "Broad load/budget context",
        "useFor": "Energy Clamp budget and daily/monthly usage sanity checks",
    },
    {
        "source": "ChargePoint",
        "role": "EV session truth",
        "useFor": "Charging sessions, EV kWh, cost allocation",
    },
]


def add_sum(target: dict[str, Any], key: str, value: Any) -> None:
    value = num(value)
    if value is None:
        return
    target[key] = target.get(key, 0.0) + value


def load_sce_daily_totals() -> dict[str, dict[str, Any]]:
    path = DATA_DIR / "sce_usage_intervals.csv"
    if not path.exists():
        return {}

    daily: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            day = str(row.get("start") or "")[:10]
            if not day:
                continue
            item = daily.setdefault(day, {"date": day})
            add_sum(item, "sceDeliveredKwh", row.get("delivered_kwh"))
            add_sum(item, "sceReceivedKwh", row.get("received_kwh"))
            add_sum(item, "sceNetImportKwh", row.get("net_import_kwh"))
            item["sceIntervalCount"] = int(item.get("sceIntervalCount") or 0) + 1
    for item in daily.values():
        item["sceComplete"] = item.get("sceIntervalCount") in {92, 96, 100}
    return daily


def build_daily_summary(
    all_energy: dict[str, Any],
    chargepoint: dict[str, Any],
    meter: dict[str, Any],
    alarm: dict[str, Any],
    sense_trends: dict[str, Any],
) -> list[dict[str, Any]]:
    days: dict[str, dict[str, Any]] = {}
    sce_daily = load_sce_daily_totals()
    for day, row in sce_daily.items():
        days[day] = dict(row)

    for row in all_energy.get("overlapPairs") or []:
        day = str(row.get("start") or "")[:10]
        if not day:
            continue
        item = days.setdefault(day, {"date": day})
        if day not in sce_daily:
            add_sum(item, "sceDeliveredKwh", row.get("sceDeliveredKwh"))
            add_sum(item, "sceReceivedKwh", row.get("sceReceivedKwh"))
            add_sum(item, "sceNetImportKwh", row.get("sceNetImportKwh"))
        if row.get("envoyConsumptionTotalKwhEstimate") is not None:
            item["envoyIntervalCount"] = int(item.get("envoyIntervalCount") or 0) + 1
        if row.get("senseKwhEstimate") is not None:
            item["senseIntervalCount"] = int(item.get("senseIntervalCount") or 0) + 1
        add_sum(item, "envoySiteLoadKwh", row.get("envoyConsumptionTotalKwhEstimate"))
        add_sum(item, "envoyGridNetKwh", row.get("envoyConsumptionNetKwhEstimate"))
        add_sum(item, "envoySolarProductionKwh", row.get("envoyProductionKwhEstimate"))
        add_sum(item, "senseKwh", row.get("senseKwhEstimate"))

    for row in (chargepoint.get("alarm") or {}).get("daily") or []:
        day = str(row.get("date") or "")
        if not day:
            continue
        item = days.setdefault(day, {"date": day})
        add_sum(item, "chargepointKwh", row.get("chargepointKwh"))

    for row in meter.get("dailyRows") or []:
        day = str(row.get("date") or "")
        if not day:
            continue
        item = days.setdefault(day, {"date": day})
        add_sum(item, "envoySiteLoadKwh", row.get("envoyConsumptionTotalKwhTodayLatest"))
        add_sum(item, "envoyGridNetKwh", row.get("envoyConsumptionNetKwhTodayLatest"))
        add_sum(item, "envoySolarProductionKwh", row.get("envoyProductionKwhTodayLatest"))

    for row in alarm.get("dailyRows") or []:
        day = str(row.get("date") or "")
        if not day:
            continue
        item = days.setdefault(day, {"date": day})
        add_sum(item, "alarmEnergyClampKwh", row.get("kwh"))

    for raw_day, row in (sense_trends.get("trends") or {}).items():
        day = str(raw_day or "")[:10]
        if not day:
            continue
        item = days.setdefault(day, {"date": day})
        item["senseTrendAvailable"] = True
        add_sum(item, "senseLoadKwh", (row.get("consumption") or {}).get("total"))
        add_sum(item, "senseSolarProductionKwh", (row.get("production") or {}).get("total"))

    out: list[dict[str, Any]] = []
    today = datetime.now(timezone.utc).astimezone().date().isoformat()
    for day in sorted(days):
        item = days[day]
        item["envoyComplete"] = item.get("envoyIntervalCount") in {92, 96, 100}
        item["senseIntervalComplete"] = item.get("senseIntervalCount") in {92, 96, 100}
        item["senseComplete"] = bool(item.get("senseTrendAvailable") and day < today)
        cp = num(item.get("chargepointKwh"))
        site = num(item.get("envoySiteLoadKwh")) or num(item.get("alarmEnergyClampKwh"))
        item["chargepointShareOfSiteLoad"] = cp / site if cp is not None and site else None
        envoy_solar = num(item.get("envoySolarProductionKwh"))
        sense_solar = num(item.get("senseSolarProductionKwh"))
        item["envoyMinusSenseSolarKwh"] = (
            envoy_solar - sense_solar if envoy_solar is not None and sense_solar is not None else None
        )
        gaps: list[str] = []
        if item.get("sceDeliveredKwh") is None and item.get("sceReceivedKwh") is None:
            gaps.append("SCE interval")
        if item.get("envoySiteLoadKwh") is None:
            gaps.append("Envoy site load")
        if item.get("envoySolarProductionKwh") is None:
            gaps.append("Envoy solar production")
        if item.get("senseSolarProductionKwh") is None:
            gaps.append("Sense solar production")
        if item.get("chargepointKwh") is None:
            gaps.append("ChargePoint")
        if item.get("alarmEnergyClampKwh") is None:
            gaps.append("Alarm.com")
        if item.get("chargepointShareOfSiteLoad") is not None and item["chargepointShareOfSiteLoad"] > 1.0:
            gaps.append("ChargePoint/site-load day alignment")
        item["unresolvedGaps"] = gaps
        out.append(item)
    return out


def build_payload() -> dict[str, Any]:
    config = load_json(CONFIG_PATH)
    thresholds = config.get("alerts", {})
    all_energy = load_json(DATA_DIR / "latest_all_energy_pairs.json")
    bill_home = load_json(DATA_DIR / "latest_bill_home_pairing.json")
    energy_costs = load_json(DATA_DIR / "latest_energy_costs.json")
    meter = load_json(DATA_DIR / "latest_meter_reconciliation.json")
    chargepoint = load_json(DATA_DIR / "latest_chargepoint_pairs.json")
    chargepoint_sessions = load_json(DATA_DIR / "chargepoint_sessions.json")
    chargepoint_refresh = load_json(DATA_DIR / "latest_chargepoint_refresh.json")
    sense_now = load_json(DATA_DIR / "sense_now_latest.json")
    sense_trends = load_json(DATA_DIR / "sense_trends_latest.json")
    latest = load_json(DATA_DIR / "latest.json")
    latest_alarm = load_json(DATA_DIR / "latest_alarm_com.json")

    now = datetime.now(timezone.utc).astimezone(LOCAL_TZ)
    alerts: list[dict[str, str]] = []
    states: list[str] = []
    insights: list[str] = []

    sce_summary = effective_sce_summary(all_energy)
    sce_overlap_count = int(all_energy.get("overlapPairCount") or 0)
    sce_age = age_hours(now, sce_summary.get("coverageEnd"))
    sce_status = sce_status_label(now, all_energy, sce_age, thresholds)
    if sce_status in {"missing", "stale"}:
        source_name = sce_summary.get("source") or "SCE"
        alerts.append(
            alert(
                "warning",
                "SCE interval data is stale",
                f"Newest SCE {source_name} interval ends `{sce_summary.get('coverageEnd') or 'n/a'}`; current bill/home matching is bill-level only.",
            )
        )

    coverage = sce_monitor_coverage(sce_summary, all_energy)
    if sce_overlap_count <= 0 and coverage.get("gapDays", 0) > 0:
        alerts.append(
            alert(
                "warning",
                "SCE and home energy history do not overlap",
                f"Newest SCE interval ends `{fmt(coverage.get('gapDays'), 1)}` days before Envoy/Sense monitor coverage starts.",
            )
        )

    alarm = fresher_alarm_energy(bill_home.get("alarm") or {}, latest_alarm)
    alarm_mismatch_threshold = float(thresholds.get("alarm_daily_dashboard_mismatch_kwh", 25))
    alarm_dashboard = alarm_dashboard_comparison(alarm, alarm_mismatch_threshold)
    alarm_mismatch = alarm_dashboard["effectiveMismatchKwh"]
    alarm_raw_mismatch = alarm_dashboard["rawMismatchKwh"]

    charge_alarm = (chargepoint.get("alarm") or {})
    cp_share = charge_alarm.get("recentChargepointShareOfAlarm7d")
    if cp_share is not None and cp_share >= float(thresholds.get("chargepoint_alarm_7d_share_high", 0.35)):
        alerts.append(
            alert(
                "warning",
                "Recent EV charging share is high",
                f"ChargePoint is `{pct(cp_share)}` of the available Alarm.com 7-day Energy Clamp window.",
            )
        )

    latest_session = latest_charge_session(chargepoint)
    session_start = parse_dt(latest_session.get("startAt"))
    session_end = parse_dt(latest_session.get("endAt"))
    sense_ev_watts = live_sense_ev_watts(
        sense_now,
        now,
        float(thresholds.get("sense_live_stale_minutes", 5)),
    )
    ev_charging_watts = float(thresholds.get("sense_ev_charging_watts", 500))
    chargepoint_session_active = bool(
        session_start and (session_end is None or session_start <= now <= session_end)
    )
    if (sense_ev_watts is not None and sense_ev_watts >= ev_charging_watts) or (
        sense_ev_watts is None and chargepoint_session_active
    ):
        states.append("EV charging")

    sense_all = (meter.get("senseEnvoySummary") or {}).get("all") or {}
    sense_envoy_raw_load_gap = sense_all.get("avgEnvoyMinusSenseKw")
    sense_envoy_non_battery_gap = sense_all.get("avgEnvoyNonBatteryLoadMinusSenseKw")
    sense_gap_for_alert = (
        sense_envoy_non_battery_gap
        if sense_envoy_non_battery_gap is not None
        else sense_envoy_raw_load_gap
    )
    if sense_gap_for_alert is not None and abs(sense_gap_for_alert) >= float(thresholds.get("sense_envoy_adjusted_gap_kw", 0.75)):
        alerts.append(
            alert(
                "warning",
                "Sense and Envoy readings disagree",
                f"Envoy non-battery load minus Sense load is `{fmt(sense_gap_for_alert, 3)}` kW.",
            )
        )

    metrics = latest.get("homebridge", {}).get("logs", {}).get("latestMetrics", {})
    if isinstance(metrics.get("enphase_consumption_net_kw"), (int, float)):
        if metrics["enphase_consumption_net_kw"] >= float(thresholds.get("grid_import_kw", 0.05)):
            states.append("Grid importing")
        if metrics["enphase_consumption_net_kw"] <= float(thresholds.get("grid_export_kw", -0.05)):
            states.append("Grid exporting")
    if (
        isinstance(metrics.get("enphase_production_kw"), (int, float))
        and isinstance(metrics.get("enphase_consumption_total_kw"), (int, float))
        and metrics["enphase_production_kw"] >= metrics["enphase_consumption_total_kw"] + float(thresholds.get("solar_surplus_margin_kw", 0.2))
    ):
        states.append("Solar surplus")

    latest_bill = bill_home.get("latestClosedBill") or {}
    cost_model = (energy_costs.get("model") or {}).get("latestClosedBill") or {}
    envoy_meters = (bill_home.get("envoy") or {}).get("meters", {})
    daily_summary = build_daily_summary(all_energy, chargepoint, meter, alarm, sense_trends)
    source_status = build_source_status(now, thresholds, all_energy, bill_home, chargepoint_sessions, chargepoint_refresh, alarm, energy_costs)
    alarm_captured_at = alarm_energy_capture_at(alarm)
    alarm_capture_dt = parse_dt(alarm_captured_at)
    alarm_capture_age_hours = (now - alarm_capture_dt).total_seconds() / 3600 if alarm_capture_dt else None
    alarm_capture_stale_hours = float(thresholds.get("alarm_energy_capture_stale_hours", 24))
    alarm_capture_stale = alarm_capture_age_hours is None or alarm_capture_age_hours >= alarm_capture_stale_hours
    alarm_totals_inconsistent = alarm_mismatch is not None and abs(num(alarm_mismatch) or 0) >= alarm_mismatch_threshold
    alarm_cache_comparison = alarm_cache_comparison_status(now, thresholds)
    alarm_capture_stale_downgraded = bool(alarm_capture_stale and alarm_cache_comparison.get("healthy"))
    alarm_recapture_reasons: list[str] = []
    if alarm_capture_stale:
        alarm_recapture_reasons.append("stale capture")
        if alarm_capture_stale_downgraded:
            alerts.append(
                alert(
                    "info",
                    "Alarm.com energy capture is stale but cache is clean",
                    (
                        f"Last captured `{alarm_captured_at or 'n/a'}`; capture age is "
                        f"`{fmt(alarm_capture_age_hours, 1)}` hours, but the current Alarm.com/Homebridge "
                        "comparison has `0` stale cached devices."
                    ),
                )
            )
        else:
            states.append("Alarm.com energy stale")
            alerts.append(
                alert(
                    "warning",
                    "Alarm.com energy is stale",
                    f"Last captured `{alarm_captured_at or 'n/a'}`; capture age is `{fmt(alarm_capture_age_hours, 1)}` hours.",
                )
            )
    if alarm_totals_inconsistent:
        alarm_recapture_reasons.append("inconsistent totals")
        states.append("Alarm.com energy inconsistent")
        alerts.append(
            alert(
                "warning",
                "Alarm.com energy totals disagree",
                f"Daily rows differ from the dashboard current period by `{fmt(alarm_mismatch, 1)}` kWh.",
            )
        )
    alarm_needs_recapture = bool(alarm_recapture_reasons)
    insights.append(
        "SCE is utility grid exchange, while Envoy Consumption Total, Alarm.com Energy Clamp, and ChargePoint are site-load views."
    )
    if sce_overlap_count > 0:
        if sce_status == "fresh":
            insights.append(f"Fresh SCE interval data overlaps the Smart Home monitor with {sce_overlap_count} paired intervals.")
        elif sce_status == "lagging":
            insights.append(f"SCE interval data was freshly exported and overlaps the Smart Home monitor with {sce_overlap_count} paired intervals; latest SCE interval is within normal utility lag.")
        else:
            insights.append(f"SCE interval data overlaps the Smart Home monitor with {sce_overlap_count} paired intervals, but the newest interval is stale.")
    if latest_bill:
        insights.append(
            f"Latest SCE bill net import was {fmt(latest_bill.get('net_import_kwh'), 0)} kWh after {fmt(latest_bill.get('export_kwh_sce'), 0)} kWh exported."
        )
    if cost_model:
        insights.append(
            f"Latest SCE bill-derived import cost is ${fmt(cost_model.get('importRateUsdPerKwh'), 3)}/kWh; exported solar was credited at ${fmt(cost_model.get('exportCreditRateUsdPerKwh'), 3)}/kWh."
        )
    if envoy_meters.get("Consumption Total"):
        insights.append(
            f"Envoy current monitor window shows {fmt(envoy_meters['Consumption Total'].get('deltaKwh'), 1)} kWh total site load and {fmt((envoy_meters.get('Production') or {}).get('deltaKwh'), 1)} kWh production."
        )
    if cp_share is not None:
        insights.append(f"ChargePoint accounts for {pct(cp_share)} of the available Alarm.com 7-day Energy Clamp window.")
    if sense_envoy_raw_load_gap is not None:
        insights.append(
            f"Sense Watts is a whole-home load signal, not grid import/export; raw Envoy total minus Sense load is {fmt(sense_envoy_raw_load_gap, 3)} kW."
        )
    if sense_envoy_non_battery_gap is not None:
        insights.append(
            f"After removing Enphase storage charge/discharge from Envoy total, Envoy non-battery load minus Sense is {fmt(sense_envoy_non_battery_gap, 3)} kW."
        )
    latest_solar_pair = next(
        (
            item
            for item in reversed(daily_summary)
            if item.get("envoyMinusSenseSolarKwh") is not None
        ),
        None,
    )
    if latest_solar_pair:
        insights.append(
            f"Latest daily solar cross-check: Envoy minus Sense solar is {fmt(latest_solar_pair.get('envoyMinusSenseSolarKwh'), 1)} kWh on {latest_solar_pair.get('date')}."
        )
    if alarm_dashboard["partialCoverage"]:
        insights.append(
            "Alarm.com daily rows cover a complete 21-day Energy Clamp window, which is shorter than the dashboard current period; this no longer requires recapture."
        )
    elif alarm_mismatch is not None and abs(num(alarm_mismatch) or 0) >= alarm_mismatch_threshold:
        insights.append(f"Alarm.com dashboard current period and copied daily rows disagree by {fmt(alarm_mismatch, 1)} kWh, so it needs a fresh capture.")
    elif alarm_mismatch is not None:
        insights.append(f"Alarm.com dashboard current period and copied daily rows agree within {fmt(abs(num(alarm_mismatch) or 0), 1)} kWh.")

    return {
        "generatedAt": now.isoformat(timespec="seconds"),
        "sources": {
            "envoy": (bill_home.get("envoy") or {}).get("coverage", {}),
            "sense": bill_home.get("sense", {}),
            "sce": sce_summary,
            "chargepoint": {
                "generatedAt": chargepoint.get("generatedAt"),
                "visibleTotalKwh": chargepoint_sessions.get("visibleTotals", {}).get("energyKwh"),
                "latestSession": latest_session,
                "refresh": chargepoint_refresh,
                "liveSenseEvWatts": sense_ev_watts,
            },
            "alarm": alarm,
            "energyCosts": {
                "generatedAt": energy_costs.get("generatedAt"),
                "latestImportRateUsdPerKwh": cost_model.get("importRateUsdPerKwh"),
                "latestExportCreditRateUsdPerKwh": cost_model.get("exportCreditRateUsdPerKwh"),
                "latestSolarSelfConsumptionValueUsdPerKwh": cost_model.get("solarSelfConsumptionValueUsdPerKwh"),
                "latestBatterySelfConsumptionValueUsdPerKwh": cost_model.get("batterySelfConsumptionValueUsdPerKwh"),
                "latestSelfConsumptionValueUsdPerKwh": cost_model.get("selfConsumptionValueUsdPerKwh"),
            },
        },
        "sourceStatus": source_status,
        "sourceRoles": SOURCE_ROLES,
        "dailySummary": daily_summary,
        "alarmEnergyStatus": {
            "capturedAtLocal": alarm_captured_at,
            "captureAgeHours": alarm_capture_age_hours,
            "dailyTotalMinusDashboardMtdKwh": alarm_mismatch,
            "rawDailyTotalMinusDashboardMtdKwh": alarm_raw_mismatch,
            "dashboardComparison": alarm_dashboard,
            "isStale": alarm_capture_stale,
            "stalenessDowngraded": alarm_capture_stale_downgraded,
            "cacheComparison": alarm_cache_comparison,
            "isInconsistent": alarm_totals_inconsistent,
            "needsRecapture": alarm_needs_recapture,
            "recaptureReasons": alarm_recapture_reasons,
        },
        "states": sorted(set(states)),
        "alerts": alerts,
        "insights": insights,
    }


def write_report(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    sources = payload.get("sources", {})
    lines = [
        "# Combined Energy Monitor",
        "",
        f"- Generated: `{payload['generatedAt']}`",
        "- Sources: Envoy, Sense, SCE, ChargePoint, Alarm.com",
        f"- Active HomeKit states: `{', '.join(payload.get('states') or []) or 'none'}`",
        f"- Energy alerts: `{len(payload.get('alerts') or [])}`",
        "",
        "## Source Coverage",
        "",
        f"- Envoy: `{(sources.get('envoy') or {}).get('start') or 'n/a'}` to `{(sources.get('envoy') or {}).get('end') or 'n/a'}`",
        f"- Sense: `{(sources.get('sense') or {}).get('start') or 'n/a'}` to `{(sources.get('sense') or {}).get('end') or 'n/a'}`",
        f"- SCE {(sources.get('sce') or {}).get('source') or 'intervals'}: `{(sources.get('sce') or {}).get('coverageStart') or 'n/a'}` to `{(sources.get('sce') or {}).get('coverageEnd') or 'n/a'}`",
        f"- ChargePoint latest session: `{((sources.get('chargepoint') or {}).get('latestSession') or {}).get('startAt') or 'n/a'}` to `{((sources.get('chargepoint') or {}).get('latestSession') or {}).get('endAt') or 'n/a'}`",
        f"- Alarm.com captured: `{(sources.get('alarm') or {}).get('capturedAtLocal') or 'n/a'}`",
        f"- Energy costs: `{(sources.get('energyCosts') or {}).get('generatedAt') or 'n/a'}`",
        "",
        "## Source Status",
        "",
        "| Source | Status | Age | Detail |",
        "|---|---|---:|---|",
    ]
    for item in payload.get("sourceStatus") or []:
        age = f"{fmt(item.get('ageHours'), 1)} h" if item.get("ageHours") is not None else (f"{item.get('ageDays')} d" if item.get("ageDays") is not None else "n/a")
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("source") or "n/a"),
                    f"`{item.get('status') or 'n/a'}`",
                    age,
                    f"`{item.get('detail') or 'n/a'}`",
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Source Roles",
            "",
            "| Source | Role | Use for |",
            "|---|---|---|",
        ]
    )
    for item in payload.get("sourceRoles") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("source") or "n/a"),
                    str(item.get("role") or "n/a"),
                    str(item.get("useFor") or "n/a"),
                ]
            )
            + " |"
        )
    lines.extend(
        [
        "",
        "## Alerts",
        "",
        ]
    )
    if payload.get("alerts"):
        for item in payload["alerts"]:
            lines.append(f"- `{item['severity']}` {item['title']}: {item['detail']}")
    else:
        lines.append("- No combined energy alerts.")
    alarm_status = payload.get("alarmEnergyStatus") or {}
    alarm_dashboard_status = alarm_status.get("dashboardComparison") or {}
    lines.extend(
        [
            "",
            "## Alarm.com Energy Status",
            "",
            f"- Captured: `{alarm_status.get('capturedAtLocal') or 'n/a'}`",
            f"- Capture age: `{fmt(alarm_status.get('captureAgeHours'), 1)}` hours",
            f"- Raw daily rows minus dashboard current period: `{fmt(alarm_status.get('rawDailyTotalMinusDashboardMtdKwh'), 1)}` kWh",
            f"- Alerted daily rows minus dashboard current period: `{fmt(alarm_status.get('dailyTotalMinusDashboardMtdKwh'), 1)}` kWh",
            f"- Dashboard comparison: `{alarm_dashboard_status.get('status') or 'n/a'}`",
            f"- Alarm.com daily row window: `{alarm_dashboard_status.get('dailyStart') or 'n/a'}` to `{alarm_dashboard_status.get('dailyEnd') or 'n/a'}`",
            f"- Energy Clamp daily rows / 21-day period / dashboard: `{fmt(alarm_dashboard_status.get('dailyTotalKwh'), 1)}` / `{fmt(alarm_dashboard_status.get('period21dKwh'), 1)}` / `{fmt(alarm_dashboard_status.get('dashboardCurrentPeriodKwh'), 1)}` kWh",
            f"- Stale capture: `{alarm_status.get('isStale')}`",
            f"- Staleness downgraded: `{alarm_status.get('stalenessDowngraded')}`",
            f"- Cache comparison stale count: `{((alarm_status.get('cacheComparison') or {}).get('staleCount'))}`",
            f"- Inconsistent totals: `{alarm_status.get('isInconsistent')}`",
            f"- Needs recapture: `{alarm_status.get('needsRecapture')}`",
            f"- Recapture reasons: `{', '.join(alarm_status.get('recaptureReasons') or []) or 'none'}`",
            "",
            "## Cost Summary",
            "",
            f"- Latest SCE import rate: `${fmt((sources.get('energyCosts') or {}).get('latestImportRateUsdPerKwh'), 3)}/kWh`",
            f"- Latest SCE export credit rate: `${fmt((sources.get('energyCosts') or {}).get('latestExportCreditRateUsdPerKwh'), 3)}/kWh`",
            f"- Direct solar self-consumption value: `${fmt((sources.get('energyCosts') or {}).get('latestSolarSelfConsumptionValueUsdPerKwh'), 3)}/kWh`",
            f"- Battery-backed self-consumption value: `${fmt((sources.get('energyCosts') or {}).get('latestBatterySelfConsumptionValueUsdPerKwh'), 3)}/kWh`",
            "",
            "## Daily Energy Summary",
            "",
            "| Date | SCE delivered | SCE received | SCE net import | Envoy site load | Sense load | Envoy solar | Sense solar | Solar gap | ChargePoint | CP share | Unresolved gaps |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for item in payload.get("dailySummary") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{item.get('date')}`",
                    fmt(item.get("sceDeliveredKwh")),
                    fmt(item.get("sceReceivedKwh")),
                    fmt(item.get("sceNetImportKwh")),
                    fmt(item.get("envoySiteLoadKwh")),
                    fmt(item.get("senseLoadKwh")),
                    fmt(item.get("envoySolarProductionKwh")),
                    fmt(item.get("senseSolarProductionKwh")),
                    fmt(item.get("envoyMinusSenseSolarKwh")),
                    fmt(item.get("chargepointKwh")),
                    pct(item.get("chargepointShareOfSiteLoad")),
                    ", ".join(item.get("unresolvedGaps") or []) or "none",
                ]
            )
            + " |"
        )
    lines.extend(["", "## Insights", ""])
    for item in payload.get("insights", []):
        lines.append(f"- {item}")
    OUT_REPORT.write_text("\n".join(lines) + "\n")


def main() -> int:
    payload = build_payload()
    write_report(payload)
    print(OUT_REPORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
