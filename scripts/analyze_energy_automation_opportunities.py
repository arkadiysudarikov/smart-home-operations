#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
OUT_JSON = DATA_DIR / "latest_energy_automation_opportunities.json"
OUT_REPORT = REPORT_DIR / "energy_automation_opportunities.md"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def now() -> str:
    return datetime.now(timezone.utc).astimezone(LOCAL_TZ).isoformat(timespec="seconds")


def rule_by_name(rules: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next((rule for rule in rules if rule.get("name") == name), {})


def build_payload() -> dict[str, Any]:
    alarm_rules = load_json(DATA_DIR / "alarm_com_automation_rules.json")
    alarm_energy = load_json(ROOT / "config" / "alarm_energy_readings.json")
    meter = load_json(DATA_DIR / "latest_meter_reconciliation.json")
    chargepoint = load_json(DATA_DIR / "chargepoint_sessions.json")
    rules = alarm_rules.get("rules") or []
    child_summary = {
        item.get("period"): item
        for item in (meter.get("childSummary") or [])
        if item.get("period")
    }
    dashboard = alarm_energy.get("dashboard") or {}
    visible_totals = chargepoint.get("visibleTotals") or {}
    sensor_left_open = rule_by_name(rules, "Sensor Left Open Energy Saver")
    sensor_left_open_active = (
        sensor_left_open
        and not sensor_left_open.get("isPaused")
        and "3 minute" in str(sensor_left_open.get("trigger", "")).lower()
        and "trim by 2" in str(sensor_left_open.get("action", "")).lower()
    )
    sensor_left_open_finding = (
        "Sensor Left Open Energy Saver is active: open sensors trigger after 3 minutes and trim heat/cool by 2°F."
        if sensor_left_open_active
        else f"Alarm.com projects {dashboard.get('energyClampProjectedKwh')} kWh against a {dashboard.get('energyClampBudgetKwh')} kWh goal."
    )
    sensor_left_open_recommendation = (
        "Leave this rule enabled and watch comfort complaints or unexpected thermostat changes before making it more aggressive."
        if sensor_left_open_active
        else "Keep Smart Away enabled, but make Sensor Left Open Energy Saver meaningful again with a real setback instead of the paused 0°F/0°F action."
    )
    sensor_left_open_automation = (
        "Alarm.com: current rule is already enabled; next practical step is reviewing peak-hour HVAC behavior before adding stricter setbacks."
        if sensor_left_open_active
        else "Alarm.com: edit Sensor Left Open Energy Saver to trim cooling/heating by a real amount after doors/windows are open for 1-3 minutes, then re-enable it."
    )

    opportunities = [
        {
            "area": "EV charging",
            "priority": "high",
            "finding": f"ChargePoint has {visible_totals.get('energyKwh', 0):.1f} kWh across {visible_totals.get('sessionCount', 0)} visible sessions and is about 30% of the recent Alarm.com 7-day site-load window.",
            "recommendation": "Shift EV charging to solar-surplus or cheapest off-peak windows where practical; avoid default overnight charging on already-high load days.",
            "automation": "Use Home/ChargePoint trigger or manual routine: if Solar is triggered and Grid Out is triggered, allow charging; otherwise defer charging unless departure requires it.",
        },
        {
            "area": "Thermostat setbacks",
            "priority": "high",
            "finding": sensor_left_open_finding,
            "recommendation": sensor_left_open_recommendation,
            "automation": sensor_left_open_automation,
        },
        {
            "area": "Solar-aware load shifting",
            "priority": "medium",
            "finding": "Home exposes Solar and Grid Out virtual sensors; Envoy is now live and can distinguish export/import.",
            "recommendation": "Use Solar/Grid Out as the condition for flexible loads rather than running them on a clock.",
            "automation": "Home: when Solar and Grid Out are triggered, permit discretionary loads; when Grid In is triggered during peak, avoid starting EV or other large flexible loads.",
        },
        {
            "area": "Garage lighting",
            "priority": "medium",
            "finding": "Home garage automations now target Garage Activity, while Alarm.com still has three active direct Garage Light 100% rules.",
            "recommendation": "Pick one owner for instant-on. Keep Alarm.com if latency is better, but add a short auto-off/follow-up if those rules do not already restore the light.",
            "automation": "Alarm.com direct rules or Home Garage Activity should include hold/restore behavior; avoid duplicate direct light-on rules across both systems.",
        },
        {
            "area": "Alarm.com unclassified load",
            "priority": "medium",
            "finding": f"Named Alarm.com child meters explain only {(child_summary.get('7d') or {}).get('namedChildShare', 0):.1%} of the 7-day Energy Clamp load.",
            "recommendation": "Do not spend time optimizing Lava Lamp/Sideyard submeter loads first; they are noise relative to the parent clamp.",
            "automation": "Add submeters or device-level monitoring only for suspected large loads: HVAC, EV, pumps, dryer/oven, and always-on infrastructure.",
        },
        {
            "area": "Notifications",
            "priority": "low",
            "finding": "Alarm.com energy daily/weekly/usage alert controls exist but were previously captured as off.",
            "recommendation": "Enable weekly energy status or a usage alert if you want the system to prompt action before the month is blown.",
            "automation": "Alarm.com Energy Settings: turn on weekly status or usage alerts for the primary recipient.",
        },
    ]

    return {
        "generatedAt": now(),
        "homeAppObserved": {
            "automationList": [
                "Garage Door Contact Opens -> Garage Activity",
                "Garage Door Lock Unlocks -> Garage Activity",
                "Garage Door Opener 2207 Opens -> Garage Activity",
                "Garage Door Opener 2210 Opens -> Garage Activity",
                "When Motion Detected in Garage -> Garage Activity",
                "When First Person Arrives Home -> Garage Activity, Panel Off",
                "When Last Person Leaves Home -> 18 accessories, thermostat setpoints 69/72",
            ],
            "leaveHomeAutomation": "enabled; shuts off lights/TVs and thermostats/garage temperature devices",
            "arrivalAutomation": "enabled; does not turn on HVAC or large loads",
        },
        "alarmComObserved": {
            "checkedAt": alarm_rules.get("checkedAt"),
            "ruleCount": alarm_rules.get("ruleCount"),
            "pausedTracked": alarm_rules.get("pausedTracked"),
            "smartAway": rule_by_name(rules, "Smart Away"),
            "sensorLeftOpen": rule_by_name(rules, "Sensor Left Open Energy Saver"),
            "weatherEvent": rule_by_name(rules, "Weather Event - Energy Savings"),
            "garageLightRules": alarm_rules.get("garageLightRules", []),
        },
        "opportunities": opportunities,
    }


def write_report(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    lines = [
        "# Energy Automation Opportunities",
        "",
        f"- Generated: `{payload['generatedAt']}`",
        "",
        "## Home App Observed",
    ]
    for item in payload["homeAppObserved"]["automationList"]:
        lines.append(f"- {item}")
    lines.extend(
        [
            f"- Leave-home automation: {payload['homeAppObserved']['leaveHomeAutomation']}",
            f"- Arrival automation: {payload['homeAppObserved']['arrivalAutomation']}",
            "",
            "## Alarm.com Observed",
            "",
            f"- Checked: `{payload['alarmComObserved'].get('checkedAt')}`",
            f"- Rules found: `{payload['alarmComObserved'].get('ruleCount')}`",
            f"- Paused tracked rules: `{', '.join(payload['alarmComObserved'].get('pausedTracked') or []) or 'none'}`",
            f"- Smart Away paused: `{payload['alarmComObserved'].get('smartAway', {}).get('isPaused')}`",
            f"- Sensor Left Open paused: `{payload['alarmComObserved'].get('sensorLeftOpen', {}).get('isPaused')}`",
            f"- Weather Event Energy Savings paused: `{payload['alarmComObserved'].get('weatherEvent', {}).get('isPaused')}`",
            f"- Garage light direct rules: `{len(payload['alarmComObserved'].get('garageLightRules') or [])}`",
            "",
            "## Recommendations",
            "",
            "| Priority | Area | Finding | Recommendation | Automation path |",
            "|---|---|---|---|---|",
        ]
    )
    for item in payload["opportunities"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    item["priority"],
                    item["area"],
                    item["finding"],
                    item["recommendation"],
                    item["automation"],
                ]
            )
            + " |"
        )
    OUT_REPORT.write_text("\n".join(lines) + "\n")


def main() -> int:
    payload = build_payload()
    write_report(payload)
    print(OUT_REPORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
