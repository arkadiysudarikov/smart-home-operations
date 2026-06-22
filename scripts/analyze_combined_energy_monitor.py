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


def age_hours(now: datetime, raw: str | None) -> float | None:
    parsed = parse_dt(raw)
    if not parsed:
        return None
    return (now - parsed).total_seconds() / 3600


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
    return {
        "status": "fresh" if sense.get("ok") is not False else "failed",
        "timestamp": sense.get("capturedAt"),
        "detail": sense.get("capturedAt"),
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
            "status": status_label(age_hours(now, alarm.get("capturedAtLocal")), stale_hours),
            "ageHours": age_hours(now, alarm.get("capturedAtLocal")),
            "detail": alarm.get("capturedAtLocal"),
        },
        {
            "source": "Energy costs",
            "status": status_label(age_hours(now, energy_costs.get("generatedAt")), stale_hours),
            "ageHours": age_hours(now, energy_costs.get("generatedAt")),
            "detail": energy_costs.get("generatedAt"),
        },
    ]
    for row in rows:
        if row["source"] in {"Envoy", "Sense"} and row.get("ageHours") is not None:
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
        add_sum(item, "senseLoadKwh", (row.get("consumption") or {}).get("total"))
        add_sum(item, "senseSolarProductionKwh", (row.get("production") or {}).get("total"))

    out: list[dict[str, Any]] = []
    for day in sorted(days):
        item = days[day]
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
    return out[-10:]


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
    sense_trends = load_json(DATA_DIR / "sense_trends_latest.json")
    latest = load_json(DATA_DIR / "latest.json")

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

    overlap = bill_home.get("overlap") or {}
    if sce_overlap_count <= 0 and not overlap.get("closedBillDirectlyOverlapsEnvoySense"):
        days = overlap.get("latestBillEndsBeforeEnvoyStartsDays")
        alerts.append(
            alert(
                "warning",
                "Energy readings need reconciliation",
                f"Latest closed SCE bill ends `{fmt(days, 0)}` days before Envoy/Sense monitor coverage starts.",
            )
        )

    alarm = bill_home.get("alarm") or {}
    alarm_mismatch = alarm.get("dailyTotalMinusDashboardMtdKwh")
    alarm_mismatch_threshold = float(thresholds.get("alarm_daily_dashboard_mismatch_kwh", 25))
    if abs(num(alarm_mismatch) or 0) >= alarm_mismatch_threshold:
        alerts.append(
            alert(
                "warning",
                "Alarm.com energy totals disagree",
                f"Alarm.com daily rows differ from the dashboard current period by `{fmt(alarm_mismatch, 1)}` kWh.",
            )
        )

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
    if session_start and (session_end is None or session_start <= now <= session_end):
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
                "Energy readings need reconciliation",
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
    alarm_capture_dt = parse_dt(alarm.get("capturedAtLocal"))
    alarm_capture_age_hours = (now - alarm_capture_dt).total_seconds() / 3600 if alarm_capture_dt else None
    alarm_capture_stale_hours = float(thresholds.get("alarm_energy_capture_stale_hours", 24))
    alarm_capture_stale = alarm_capture_age_hours is None or alarm_capture_age_hours >= alarm_capture_stale_hours
    alarm_totals_inconsistent = alarm_mismatch is not None and abs(num(alarm_mismatch) or 0) >= alarm_mismatch_threshold
    alarm_recapture_reasons: list[str] = []
    if alarm_capture_stale:
        alarm_recapture_reasons.append("stale capture")
        states.append("Alarm.com energy stale")
        alerts.append(
            alert(
                "warning",
                "Alarm.com energy is stale",
                f"Last captured `{alarm.get('capturedAtLocal') or 'n/a'}`; capture age is `{fmt(alarm_capture_age_hours, 1)}` hours.",
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
    if alarm_mismatch is not None and abs(num(alarm_mismatch) or 0) >= alarm_mismatch_threshold:
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
            "capturedAtLocal": alarm.get("capturedAtLocal"),
            "captureAgeHours": alarm_capture_age_hours,
            "dailyTotalMinusDashboardMtdKwh": alarm_mismatch,
            "isStale": alarm_capture_stale,
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
    lines.extend(
        [
            "",
            "## Alarm.com Energy Status",
            "",
            f"- Captured: `{alarm_status.get('capturedAtLocal') or 'n/a'}`",
            f"- Capture age: `{fmt(alarm_status.get('captureAgeHours'), 1)}` hours",
            f"- Daily rows minus dashboard current period: `{fmt(alarm_status.get('dailyTotalMinusDashboardMtdKwh'), 1)}` kWh",
            f"- Stale capture: `{alarm_status.get('isStale')}`",
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
