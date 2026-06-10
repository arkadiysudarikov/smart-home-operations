#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "sources.json"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "smart_home.sqlite"
REPORT_DIR = ROOT / "reports"
SILENCE_PATH = DATA_DIR / "alerts_silenced_until.json"
COMBINED_ENERGY_PATH = DATA_DIR / "latest_combined_energy_monitor.json"
ALARM_COM_PATH = DATA_DIR / "latest_alarm_com.json"
LATEST_CHARACTERISTICS_PATH = DATA_DIR / "latest_characteristics.json"
ALARM_STATE_COMPARISON_PATH = DATA_DIR / "latest_alarm_homebridge_state.json"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text())


def load_latest() -> dict[str, Any]:
    latest = DATA_DIR / "latest.json"
    return json.loads(latest.read_text()) if latest.exists() else {}


def load_combined_energy() -> dict[str, Any]:
    if not COMBINED_ENERGY_PATH.exists():
        return {}
    try:
        return json.loads(COMBINED_ENERGY_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def load_alarm_com() -> dict[str, Any]:
    if not ALARM_COM_PATH.exists():
        return {}
    try:
        return json.loads(ALARM_COM_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def load_latest_characteristics() -> dict[str, Any]:
    if not LATEST_CHARACTERISTICS_PATH.exists():
        return {}
    try:
        data = json.loads(LATEST_CHARACTERISTICS_PATH.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def recent_rows(limit: int) -> list[sqlite3.Row]:
    if not DB_PATH.exists():
        return []
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        return list(
            db.execute(
                "select * from snapshots order by captured_at desc limit ?",
                (limit,),
            )
        )


def row_alarm_websocket_enabled(row: sqlite3.Row) -> bool:
    try:
        raw = json.loads(row["raw_json"])
    except Exception:
        return True
    alarm_platform = next(
        (
            item
            for item in raw.get("homebridge", {}).get("config", {}).get("platforms", [])
            if item.get("platform") == "Alarmdotcom"
        ),
        {},
    )
    return alarm_platform.get("shouldUseWebSockets") is not False


def warning_category(message: str) -> str:
    lower = message.lower()
    if "security system" in lower or "alarm.com" in lower or "websocket token fetch returned 403" in lower:
        return "Alarm.com auth/websocket"
    if "smarthq" in lower and "remaining duration" in lower and "exceeded maximum of 3600" in lower:
        return "SmartHQ remaining duration"
    if ("sense energy meter" in lower or "sense" in lower) and (
        "401" in lower
        or "unauthorized" in lower
        or "unexpected server response" in lower
        or "re-auth failed" in lower
        or "authentication error" in lower
    ):
        return "Sense live websocket auth"
    if "sense energy meter" in lower or "sense" in lower:
        return "Sense other"
    if "[office]" in lower or "tahoma" in lower or "192.168.0.164:8443" in lower:
        return "Office TaHoma"
    if "unifi" in lower or "occupancy" in lower:
        return "UniFi occupancy"
    if "mopar" in lower:
        return "Mopar"
    if "smarthq" in lower:
        return "SmartHQ"
    if "enphase" in lower or "envoy" in lower:
        return "Enphase Envoy"
    return "Other"


def warning_trend(rows: list[sqlite3.Row]) -> dict[str, Any]:
    categories: Counter[str] = Counter()
    examples: dict[str, str] = {}
    mentions = 0
    for row in rows:
        try:
            raw = json.loads(row["raw_json"])
        except Exception:
            continue
        for item in raw.get("homebridge", {}).get("logs", {}).get("recentWarnings", []) or []:
            message = str(item)
            category = warning_category(message)
            categories[category] += 1
            mentions += 1
            examples.setdefault(category, message)
    leaders = [
        {"category": category, "count": count, "example": examples.get(category, "")}
        for category, count in categories.most_common()
    ]
    return {
        "windowSnapshots": len(rows),
        "warningMentions": mentions,
        "leaders": leaders,
    }


def summarize_warning_trend(
    trend: dict[str, Any],
    max_items: int = 3,
    excluded_categories: set[str] | None = None,
) -> str:
    excluded_categories = excluded_categories or set()
    leaders = [item for item in trend.get("leaders") or [] if item.get("category") not in excluded_categories]
    if not leaders:
        return "no classified warning leader"
    total = sum(int(item.get("count") or 0) for item in leaders)
    parts = []
    for item in leaders[:max_items]:
        count = int(item.get("count") or 0)
        pct = (count / total * 100) if total else 0
        parts.append(f"{item.get('category')} `{count}` ({pct:.0f}%)")
    return ", ".join(parts)


def warning_count_excluding(trend: dict[str, Any], excluded_categories: set[str]) -> int:
    return sum(
        int(item.get("count") or 0)
        for item in trend.get("leaders") or []
        if item.get("category") not in excluded_categories
    )


def severity_rank(severity: str) -> int:
    return {"critical": 0, "warning": 1, "info": 2}.get(severity, 3)


def recommended_action(alert: dict[str, str]) -> str | None:
    title = alert.get("title", "")
    detail = alert.get("detail", "")
    if title == "Alarm.com sensor-triggered media is missing":
        return "Trip Entry Door or Sideyard Gate once, wait 1-2 minutes, then refresh Alarm.com activity/media and confirm a new clip or image event."
    if title == "Alarm.com video recording rules are missing":
        return "Recreate the missing Alarm.com Recording Rules for Entry Door, Sideyard Gate, and related cameras; then run a post-rule door or gate trip test."
    if title == "Office TaHoma child bridge is unreachable":
        return "Check TaHoma power, Wi-Fi, and IP reservation for 192.168.0.164; then rerun the Office child bridge check or restart."
    if title == "Recent Homebridge warning volume is high":
        return "Use the Warning Trend section below and fix the top non-dedicated category first; if Alarm.com dominates, refresh the portal cookie and websocket path."
    if title == "Alarm.com websocket is unreliable":
        return "Refresh the Alarm.com portal capture; if 403 reauth churn continues, consider disabling Alarm.com websockets again."
    if title == "Alarm.com portal websocket token failed":
        return "Refresh Alarm.com with the Homebridge cookie and verify the portal websocket token endpoint still returns a token."
    if title == "Alarm.com activity history is degraded":
        return "Refresh Alarm.com with the Homebridge cookie, then rerun the monitor so activity history and media validation recapture cleanly."
    if title == "Alarm.com Homebridge cache is stale":
        return "Use Alarm.com portal state as current truth; restart or refresh the Alarm child bridge if these cached Homebridge values remain stale after the next monitor run."
    if title == "Alarm.com portal capture failed":
        return "Refresh Alarm.com with the Homebridge cookie, then rerun the monitor so energy, activity, and media health are recaptured."
    if title == "Alarm.com device issue":
        return "Open Alarm.com device status, resolve the listed device trouble, then recapture Alarm.com."
    if title == "SCE interval data is stale":
        return "Run Refresh SCE/UtilityAPI or import a fresh Green Button interval export, then rerun energy reconciliation."
    if title in {"Alarm.com energy is stale", "Alarm.com energy totals disagree"}:
        return "Recapture Alarm.com energy and compare the updated Energy Clamp totals against Envoy and SCE in the combined report."
    if title == "Energy readings need reconciliation":
        return "Open the combined energy report, check Source Status and daily source gaps, then refresh the stale source named there."
    if title == "Homebridge is not running":
        return "Restart Homebridge, then run the smart-home check again after accessories reconnect."
    if title == "Homebridge storage permissions are too open":
        return "Run the Homebridge permission hardening step and rerun the monitor to verify storage paths."
    if title == "UniFi occupancy authentication is failing":
        return "Refresh the UniFi occupancy credentials/session and verify the Homebridge UniFi plugin can load clients."
    if title == "House load is high":
        return "Check the current large loads in Home/Envoy, then compare against Sense live load and ChargePoint charging state."
    if title == "Sense live websocket auth is noisy":
        return "Leave daily Sense trend capture alone; it is working. Restart or reauth the Homebridge Sense live meter only if live 401s keep recurring after the next Homebridge restart."
    if title in {"Battery failed to recharge before peak", "Battery reserve is low before peak", "Battery backup is critically low", "Battery backup is low"}:
        return "Check Enphase battery status and operating mode, then verify solar production can recharge before peak pricing."
    if "source gap" in detail.lower() or "missing" in detail.lower():
        return "Refresh the named source, then rerun energy reconciliation so unresolved source gaps clear from the daily summary."
    return None


def enrich_alerts(alerts: list[dict[str, str]]) -> list[dict[str, str]]:
    enriched: list[dict[str, str]] = []
    for alert in alerts:
        item = dict(alert)
        action = recommended_action(item)
        if action:
            item["recommendedAction"] = action
        enriched.append(item)
    return enriched


def alarm_device_aliases(latest: dict[str, Any]) -> dict[str, str]:
    alarm_platform = next(
        (
            item
            for item in latest.get("homebridge", {}).get("config", {}).get("platforms", [])
            if item.get("platform") == "Alarmdotcom"
        ),
        {},
    )
    aliases: dict[str, str] = {}
    for item in alarm_platform.get("deviceAliases") or []:
        if item.get("id") and item.get("name"):
            aliases[str(item["id"])] = str(item["name"])
    return aliases


def portal_alarm_states(alarm_com: dict[str, Any], latest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    alarm_state = alarm_com.get("alarmState") or {}
    components = ((alarm_state.get("systems") or [{}])[0].get("components") or {}) if alarm_state.get("ok") else {}
    aliases = alarm_device_aliases(latest)
    portal: dict[str, dict[str, Any]] = {}
    for group, items in components.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(aliases.get(str(item.get("id"))) or item.get("description") or "").strip()
            state = item.get("stateText") or item.get("displayStateText")
            if not name or state is None:
                continue
            portal[name] = {
                "sourceName": item.get("description"),
                "group": group,
                "state": str(state),
                "id": item.get("id"),
                "remoteCommandsEnabled": item.get("remoteCommandsEnabled"),
                "isMonitoringEnabled": item.get("isMonitoringEnabled"),
                "isBypassed": item.get("isBypassed"),
            }
    return portal


def normalize_homebridge_alarm_value(characteristic: str, value: Any) -> str | None:
    if characteristic == "SecuritySystemCurrentState":
        return {0: "Armed stay", 1: "Armed away", 2: "Armed night", 3: "Disarmed", 4: "Alarm triggered"}.get(value)
    if characteristic == "ContactSensorState":
        return {0: "Closed", 1: "Open"}.get(value)
    if characteristic == "MotionDetected":
        return "Activated" if value is True else "Idle" if value is False else None
    if characteristic == "LockCurrentState":
        return {0: "Locked", 1: "Unlocked", 2: "Jammed", 3: "Unknown"}.get(value)
    if characteristic == "CurrentDoorState":
        return {0: "Open", 1: "Closed", 2: "Opening", 3: "Closing", 4: "Stopped"}.get(value)
    if characteristic == "On":
        return "On" if value is True else "Off" if value is False else None
    if characteristic == "CurrentHeatingCoolingState":
        return {0: "Off", 1: "Heating", 2: "Cooling"}.get(value)
    return None


def preferred_alarm_characteristics() -> set[str]:
    return {
        "SecuritySystemCurrentState",
        "ContactSensorState",
        "MotionDetected",
        "LockCurrentState",
        "CurrentDoorState",
        "On",
        "CurrentHeatingCoolingState",
    }


def homebridge_alarm_states() -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    for item in load_latest_characteristics().values():
        if not isinstance(item, dict):
            continue
        if item.get("plugin") != "homebridge-node-alarm-dot-com":
            continue
        characteristic = str(item.get("characteristic") or "")
        if characteristic not in preferred_alarm_characteristics():
            continue
        normalized = normalize_homebridge_alarm_value(characteristic, item.get("value"))
        if normalized is None:
            continue
        name = str(item.get("accessory") or "")
        states[name] = {
            "state": normalized,
            "characteristic": characteristic,
            "rawValue": item.get("value"),
            "service": item.get("service"),
            "cacheFile": item.get("cacheFile"),
        }
    return states


def comparable_alarm_state(homebridge_state: str, portal_state: str) -> bool:
    if homebridge_state == portal_state:
        return True
    equivalent = {
        ("Idle", "Closed"),
        ("Closed", "Idle"),
    }
    return (homebridge_state, portal_state) in equivalent


def compare_alarm_portal_to_homebridge(alarm_com: dict[str, Any], latest: dict[str, Any]) -> dict[str, Any]:
    portal = portal_alarm_states(alarm_com, latest)
    homebridge = homebridge_alarm_states()
    rows: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    for name, portal_item in sorted(portal.items()):
        hb_item = homebridge.get(name)
        if not hb_item:
            continue
        matches = comparable_alarm_state(str(hb_item["state"]), str(portal_item["state"]))
        row = {
            "device": name,
            "portalState": portal_item["state"],
            "homebridgeCachedState": hb_item["state"],
            "matches": matches,
            "homebridgeCharacteristic": hb_item.get("characteristic"),
            "homebridgeRawValue": hb_item.get("rawValue"),
            "portalGroup": portal_item.get("group"),
            "portalDeviceId": portal_item.get("id"),
            "portalSourceName": portal_item.get("sourceName"),
        }
        rows.append(row)
        if not matches:
            stale.append(row)
    return {
        "generatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "portalGeneratedAt": alarm_com.get("generatedAt"),
        "portalDeviceCount": len(portal),
        "homebridgeComparedCount": len(rows),
        "staleCount": len(stale),
        "states": rows,
        "stale": stale,
    }


def write_alarm_state_comparison_report(comparison: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ALARM_STATE_COMPARISON_PATH.write_text(json.dumps(comparison, indent=2, sort_keys=True) + "\n")
    portal_age = age_label(comparison.get("portalGeneratedAt"), comparison.get("generatedAt"))
    comparison_age = age_label(comparison.get("generatedAt"))
    lines = [
        "# Alarm.com vs Homebridge State",
        "",
        f"- Generated: `{comparison.get('generatedAt')}`",
        f"- Comparison age: `{comparison_age}`",
        f"- Alarm.com portal capture: `{comparison.get('portalGeneratedAt') or 'n/a'}`",
        f"- Alarm.com portal capture age: `{portal_age}`",
        "- Source of truth for Alarm.com current-state reporting: `Alarm.com portal state`",
        f"- Compared devices: `{comparison.get('homebridgeComparedCount')}`",
        f"- Stale Homebridge cached states: `{comparison.get('staleCount')}`",
        "",
    ]
    stale = comparison.get("stale") or []
    if stale:
        lines.extend(["## Stale Cached States", "", "| Device | Alarm.com portal | Homebridge cache | Characteristic |", "|---|---|---|---|"])
        for row in stale:
            lines.append(
                f"| {row.get('device')} | {row.get('portalState')} | {row.get('homebridgeCachedState')} | {row.get('homebridgeCharacteristic')} |"
            )
        lines.append("")
    lines.extend(["## Compared States", "", "| Device | Alarm.com portal | Homebridge cache | Match |", "|---|---|---|---|"])
    for row in comparison.get("states") or []:
        lines.append(
            f"| {row.get('device')} | {row.get('portalState')} | {row.get('homebridgeCachedState')} | {row.get('matches')} |"
        )
    (REPORT_DIR / "alarm_homebridge_state.md").write_text("\n".join(lines) + "\n")


def active_warning_silence() -> datetime | None:
    if not SILENCE_PATH.exists():
        return None
    try:
        payload = json.loads(SILENCE_PATH.read_text())
        until = datetime.fromisoformat(str(payload["until"]))
    except Exception:
        return None
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    if until <= datetime.now(timezone.utc).astimezone():
        return None
    return until


def apply_warning_silence(alerts: list[dict[str, str]], until: datetime | None) -> list[dict[str, str]]:
    if until is None:
        return alerts
    filtered = [alert for alert in alerts if alert.get("severity") != "warning"]
    filtered.append(
        {
            "severity": "info",
            "title": "Smart-home warning alerts are silenced",
            "detail": f"Warning-level alerts are muted until `{until.isoformat(timespec='seconds')}`.",
        }
    )
    return filtered


def parse_captured_at(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc).astimezone(LOCAL_TZ)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return datetime.now(timezone.utc).astimezone(LOCAL_TZ)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return parsed.astimezone(LOCAL_TZ)


def parse_report_time(raw: Any) -> datetime | None:
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return parsed.astimezone(LOCAL_TZ)


def age_label(start_raw: Any, end_raw: Any = None) -> str:
    start = parse_report_time(start_raw)
    if start is None:
        return "n/a"
    end = parse_report_time(end_raw) if end_raw else datetime.now(timezone.utc).astimezone(LOCAL_TZ)
    if end is None:
        end = datetime.now(timezone.utc).astimezone(LOCAL_TZ)
    seconds = max(0, int((end - start).total_seconds()))
    if seconds < 120:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 120:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h {minutes % 60}m"
    days = hours // 24
    return f"{days}d {hours % 24}h"


def minutes_since(start_raw: str | None, end_raw: str | None) -> float | None:
    if not start_raw or not end_raw:
        return None
    try:
        start = datetime.fromisoformat(start_raw)
        end = datetime.fromisoformat(end_raw)
    except ValueError:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=LOCAL_TZ)
    if end.tzinfo is None:
        end = end.replace(tzinfo=LOCAL_TZ)
    return (end.astimezone(LOCAL_TZ) - start.astimezone(LOCAL_TZ)).total_seconds() / 60


def homebridge_restart_grace_active(config: dict[str, Any], latest: dict[str, Any]) -> bool:
    grace_minutes = float(config["alerts"].get("energy_stale_restart_grace_minutes", 0))
    if grace_minutes <= 0:
        return False
    elapsed = minutes_since(
        latest.get("homebridge", {}).get("logs", {}).get("runStartedAt"),
        latest.get("captured_at"),
    )
    return elapsed is not None and 0 <= elapsed <= grace_minutes


def battery_cycle_alert(config: dict[str, Any], battery: float, captured_at: datetime) -> dict[str, str] | None:
    alerts_config = config["alerts"]
    if alerts_config.get("battery_alert_mode") != "solar_peak_cycle":
        if battery <= float(alerts_config["battery_critical_percent"]):
            return {
                "severity": "critical",
                "title": "Battery backup is critically low",
                "detail": f"Enphase backup level is `{battery}%`.",
            }
        if battery <= float(alerts_config["battery_low_percent"]):
            return {
                "severity": "warning",
                "title": "Battery backup is low",
                "detail": f"Enphase backup level is `{battery}%`.",
            }
        return None

    start_hour = int(alerts_config.get("battery_recharge_check_start_hour", 14))
    end_hour = int(alerts_config.get("battery_recharge_check_end_hour", 16))
    if not (start_hour <= captured_at.hour < end_hour):
        return None

    if battery <= float(alerts_config["battery_critical_percent"]):
        return {
            "severity": "critical",
            "title": "Battery failed to recharge before peak",
            "detail": (
                f"Enphase backup level is `{battery}%` during the solar recharge check window "
                f"`{start_hour}:00-{end_hour}:00`; morning and peak-discharge lows are expected."
            ),
        }
    if battery <= float(alerts_config["battery_low_percent"]):
        return {
            "severity": "warning",
            "title": "Battery reserve is low before peak",
            "detail": (
                f"Enphase backup level is `{battery}%` during the solar recharge check window "
                f"`{start_hour}:00-{end_hour}:00`; morning and peak-discharge lows are expected."
            ),
        }
    return None


def build_alerts(config: dict[str, Any], latest: dict[str, Any], rows: list[sqlite3.Row]) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    hb = latest.get("homebridge", {})
    logs = hb.get("logs", {})
    metrics = logs.get("latestMetrics", {})
    alarm_com = load_alarm_com()
    alarm_comparison: dict[str, Any] = {}
    if (alarm_com.get("alarmState") or {}).get("ok"):
        alarm_comparison = compare_alarm_portal_to_homebridge(alarm_com, latest)
    alarm_portal_state_clean = bool(alarm_comparison) and not (alarm_comparison.get("stale") or [])
    launchd_state = hb.get("launchd", {}).get("state")
    permissions = hb.get("security", {}).get("homebridgePermissions", {})
    if launchd_state != "running":
        alerts.append(
            {
                "severity": "critical",
                "title": "Homebridge is not running",
                "detail": f"Current launchd state is `{launchd_state}`.",
            }
        )

    insecure_paths = permissions.get("insecurePaths", [])
    if insecure_paths:
        alerts.append(
            {
                "severity": "warning",
                "title": "Homebridge storage permissions are too open",
                "detail": f"`{len(insecure_paths)}` checked Homebridge paths expose group/other permission bits.",
            }
        )

    captured_at = parse_captured_at(latest.get("captured_at"))
    battery = metrics.get("enphase_backup_percent")
    if isinstance(battery, (int, float)):
        alert = battery_cycle_alert(config, float(battery), captured_at)
        if alert:
            alerts.append(alert)

    load_kw = metrics.get("enphase_consumption_total_kw")
    if isinstance(load_kw, (int, float)) and load_kw >= config["alerts"]["high_load_kw"]:
        alerts.append(
            {
                "severity": "warning",
                "title": "House load is high",
                "detail": f"Enphase total consumption is `{load_kw:.3f} kW`.",
            }
        )

    recent_warnings = "\n".join(str(item) for item in logs.get("recentWarnings", []))
    if "[homebridge-unifi-occupancy]" in recent_warnings and "401" in recent_warnings:
        alerts.append(
            {
                "severity": "warning",
                "title": "UniFi occupancy authentication is failing",
                "detail": "Homebridge UniFi occupancy is receiving `401 Unauthorized` while loading clients.",
            }
        )

    office_endpoint = str(config["network"].get("known_tahoma_office", "192.168.0.164:8443"))
    office_unreachable_signals = ("ETIMEDOUT", "EHOSTUNREACH", "ENETUNREACH", "ECONNREFUSED")
    if (
        "[Office]" in recent_warnings
        and office_endpoint in recent_warnings
        and any(signal in recent_warnings for signal in office_unreachable_signals)
    ):
        alerts.append(
            {
                "severity": "warning",
                "title": "Office TaHoma child bridge is unreachable",
                "detail": f"The Office TaHoma child bridge cannot reach `{office_endpoint}`.",
            }
        )

    alarm_platform = next(
        (
            item
            for item in hb.get("config", {}).get("platforms", [])
            if item.get("platform") == "Alarmdotcom"
        ),
        {},
    )
    alarm_websocket_enabled = alarm_platform.get("shouldUseWebSockets") is not False
    alarm_window_size = int(config["alerts"]["alarm_websocket_recent_window"])
    alarm_window = [row for row in rows if row_alarm_websocket_enabled(row)][:alarm_window_size]
    if alarm_websocket_enabled and alarm_window:
        successes = sum(int(row["alarm_websocket"]) for row in alarm_window)
        if (
            len(alarm_window) >= alarm_window_size
            and successes < int(config["alerts"]["alarm_websocket_min_successes"])
            and not alarm_portal_state_clean
        ):
            alerts.append(
                {
                    "severity": "warning",
                    "title": "Alarm.com websocket is unreliable",
                    "detail": f"Only `{successes}/{len(alarm_window)}` recent snapshots saw the websocket established.",
                }
            )

    if alarm_com:
        if not (alarm_com.get("login") or {}).get("ok"):
            alerts.append(
                {
                    "severity": "warning",
                    "title": "Alarm.com portal capture failed",
                    "detail": "The Alarm.com cookie-backed capture could not log in.",
                }
            )
        if not (alarm_com.get("energy") or {}).get("ok"):
            alerts.append(
                {
                    "severity": "warning",
                    "title": "Alarm.com portal capture failed",
                    "detail": "The Alarm.com portal capture logged in but did not refresh energy data.",
                }
            )
        activity = alarm_com.get("activity") or {}
        if alarm_com.get("activity") and not activity.get("ok"):
            alerts.append(
                {
                    "severity": "warning",
                    "title": "Alarm.com activity history is degraded",
                    "detail": "The Alarm.com portal capture logged in but did not refresh activity history.",
                }
            )
        elif activity.get("refreshOk") is False:
            activity_source = activity.get("source") or "cached activity history from the last good capture"
            alerts.append(
                {
                    "severity": "warning",
                    "title": "Alarm.com activity history is degraded",
                    "detail": (
                        f"The Alarm.com activity endpoint returned `{activity.get('refreshStatus') or 'n/a'}`; "
                        f"using `{activity_source}`."
                    ),
                }
            )
        websocket = alarm_com.get("websocketToken") or {}
        if websocket and not websocket.get("ok"):
            alerts.append(
                {
                    "severity": "warning",
                    "title": "Alarm.com portal websocket token failed",
                    "detail": websocket.get("error") or "The Alarm.com API did not return a usable websocket token.",
                }
            )
        issues = (alarm_com.get("alarmState") or {}).get("issues") or []
        if issues:
            first = issues[0]
            alerts.append(
                {
                    "severity": "warning",
                    "title": "Alarm.com device issue",
                    "detail": f"`{len(issues)}` Alarm.com device issues; first is `{first.get('description') or first.get('id')}` state `{first.get('state') or 'n/a'}`.",
                }
            )
        if alarm_comparison:
            stale = alarm_comparison.get("stale") or []
            if stale:
                examples = ", ".join(
                    f"{item.get('device')} portal `{item.get('portalState')}` vs Homebridge cache `{item.get('homebridgeCachedState')}`"
                    for item in stale[:4]
                )
                alerts.append(
                    {
                        "severity": "warning",
                        "title": "Alarm.com Homebridge cache is stale",
                        "detail": (
                            f"`{len(stale)}` Alarm.com device states disagree with the cached Homebridge characteristics; "
                            f"{examples}. Fresh Alarm.com portal state is preferred for current-state reporting."
                        ),
                    }
                )
        video_rules = alarm_com.get("videoRules") or {}
        missing_video_rules = video_rules.get("missingExpected") or []
        paused_video_rules = video_rules.get("pausedExpected") or []
        if video_rules.get("ok") and (missing_video_rules or paused_video_rules):
            parts = []
            if missing_video_rules:
                parts.append(f"missing: `{', '.join(str(item) for item in missing_video_rules)}`")
            if paused_video_rules:
                parts.append(f"paused: `{', '.join(str(item) for item in paused_video_rules)}`")
            alerts.append(
                {
                    "severity": "warning",
                    "title": "Alarm.com video recording rules are missing",
                    "detail": (
                        f"Recording rule check found `{video_rules.get('ruleCount')}` rules; "
                        + "; ".join(parts)
                        + "."
                    ),
                }
            )
        media = ((alarm_com.get("activity") or {}).get("mediaTriggerHealth") or {})
        media_min_sensor_trips = int(config["alerts"].get("alarm_media_sensor_trip_min_events", 10))
        if (
            media.get("ok")
            and int(media.get("tripLikeSensorEvents") or 0) >= media_min_sensor_trips
            and int(media.get("sensorTriggeredMediaEvents") or 0) == 0
        ):
            validation_trips = int(media.get("validationTargetTripEvents") or 0)
            latest_validation = media.get("latestValidationTargetTripAt") or "none"
            rule_state = ""
            if video_rules.get("ok") and missing_video_rules:
                rule_state = " Expected video recording rules are currently missing, so media cannot be validated until they are recreated."
            alerts.append(
                {
                    "severity": "warning",
                    "title": "Alarm.com sensor-triggered media is missing",
                    "detail": (
                        f"`{media.get('tripLikeSensorEvents')}` trip-like sensor events but "
                        f"`0` sensor-triggered media events in the Alarm.com activity window; "
                        f"post-disarm media events: `{media.get('postDisarmMediaEvents') or 0}`; "
                        f"validation target trips: `{validation_trips}` "
                        f"(latest Entry Door/Sideyard Gate trip: `{latest_validation}`)."
                        f"{rule_state}"
                    ),
                }
            )

    warning_window = rows[: int(config["alerts"]["warning_recent_window"])]
    trend = warning_trend(warning_window)
    trend_counts = {item.get("category"): int(item.get("count") or 0) for item in trend.get("leaders") or []}
    sense_live_401_count = trend_counts.get("Sense live websocket auth", 0)
    if sense_live_401_count >= int(config["alerts"].get("sense_live_401_warning_min", 3)):
        alerts.append(
            {
                "severity": "warning",
                "title": "Sense live websocket auth is noisy",
                "detail": (
                    f"`{sense_live_401_count}` recent Sense live-websocket auth warnings; "
                    "daily Sense trend capture is tracked separately and may still be healthy."
                ),
            }
        )

    current_warning_count = int(latest.get("homebridge", {}).get("logs", {}).get("warningCount", 0))
    warning_total = sum(int(row["warning_count"]) for row in warning_window)
    if current_warning_count > 0 and warning_total >= int(config["alerts"]["warning_high_count"]):
        dedicated_categories = {"Office TaHoma", "Sense live websocket auth", "SmartHQ remaining duration"}
        if alarm_portal_state_clean:
            dedicated_categories.add("Alarm.com auth/websocket")
        non_dedicated_total = warning_count_excluding(
            trend,
            dedicated_categories,
        )
        threshold = int(config["alerts"]["warning_high_count"])
        if non_dedicated_total >= threshold:
            alerts.append(
                {
                    "severity": "warning",
                    "title": "Recent Homebridge warning volume is high",
                    "detail": (
                        f"`{warning_total}` warnings across the latest `{len(warning_window)}` snapshots; "
                        f"`{non_dedicated_total}` are outside dedicated Office TaHoma, Sense live auth, and SmartHQ duration checks; "
                        f"dominated by {summarize_warning_trend(trend, excluded_categories=dedicated_categories)}."
                    ),
                }
            )

    for item in load_combined_energy().get("alerts", []):
        title = item.get("title")
        detail = item.get("detail")
        severity = item.get("severity", "warning")
        if title and detail:
            alerts.append({"severity": severity, "title": title, "detail": detail})

    if not alerts:
        alerts.append(
            {
                "severity": "info",
                "title": "No active smart-home alerts",
                "detail": "Configured checks are currently below alert thresholds.",
            }
        )
    return sorted(alerts, key=lambda item: severity_rank(item["severity"]))


def active_state_titles(config: dict[str, Any], latest: dict[str, Any]) -> set[str]:
    metrics = latest.get("homebridge", {}).get("logs", {}).get("latestMetrics", {})
    states: set[str] = set()

    production_kw = metrics.get("enphase_production_kw")
    net_kw = metrics.get("enphase_consumption_net_kw")
    total_kw = metrics.get("enphase_consumption_total_kw")

    if not any(isinstance(value, (int, float)) for value in (production_kw, net_kw, total_kw)) and not homebridge_restart_grace_active(config, latest):
        states.add("Energy data stale")

    if isinstance(net_kw, (int, float)):
        if net_kw >= float(config["alerts"]["grid_import_kw"]):
            states.add("Grid importing")
        if net_kw <= float(config["alerts"]["grid_export_kw"]):
            states.add("Grid exporting")

    if isinstance(production_kw, (int, float)) and isinstance(total_kw, (int, float)):
        if production_kw >= total_kw + float(config["alerts"]["solar_surplus_margin_kw"]):
            states.add("Solar surplus")

    states.update(str(item) for item in load_combined_energy().get("states", []))

    return states


def write_reports(alerts: list[dict[str, str]], latest: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    alerts = enrich_alerts(alerts)
    config = load_config()
    warning_rows = recent_rows(int(config["alerts"]["warning_recent_window"]))
    trend = warning_trend(warning_rows)
    alarm_com = load_alarm_com()
    if (alarm_com.get("alarmState") or {}).get("ok"):
        write_alarm_state_comparison_report(compare_alarm_portal_to_homebridge(alarm_com, latest))
    payload = {
        "generatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "latestSnapshotAt": latest.get("captured_at"),
        "alerts": alerts,
        "warningTrend": trend,
    }
    (DATA_DIR / "latest_alerts.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    lines = [
        "# Smart Home Alerts",
        "",
        f"- Generated: `{payload['generatedAt']}`",
        f"- Latest snapshot: `{payload.get('latestSnapshotAt')}`",
        "",
    ]
    for alert in alerts:
        lines.append(f"- `{alert['severity']}` {alert['title']}: {alert['detail']}")
        if alert.get("recommendedAction"):
            lines.append(f"  - Recommended action: {alert['recommendedAction']}")
    lines.extend(["", "## Warning Trend", ""])
    if trend.get("leaders"):
        lines.append(
            f"- Classified warning mentions: `{trend.get('warningMentions')}` across `{trend.get('windowSnapshots')}` snapshots."
        )
        for item in trend["leaders"][:8]:
            lines.append(f"- `{item['category']}`: `{item['count']}` mentions. Example: {item.get('example') or 'n/a'}")
    else:
        lines.append("- No warning mentions were classified in the recent snapshot window.")
    (REPORT_DIR / "alerts.md").write_text("\n".join(lines) + "\n")


def update_homekit_virtual_sensors(config: dict[str, Any], alerts: list[dict[str, str]]) -> list[dict[str, Any]]:
    sensor_config = config.get("homekit_virtual_sensors", {})
    if not sensor_config.get("enabled", False):
        return []
    webhook_url = str(sensor_config.get("webhook_url", "")).rstrip("/")
    if not webhook_url:
        return []
    active_titles = {alert["title"] for alert in alerts if alert.get("severity") != "info"}
    state_titles = active_state_titles(config, load_latest())
    updates: list[dict[str, Any]] = []
    for accessory in sensor_config.get("accessories", []):
        accessory_id = accessory["id"]
        should_be_active = (
            any(title in active_titles for title in accessory.get("alert_titles", []))
            or any(title in state_titles for title in accessory.get("state_titles", []))
        )
        query = urllib.parse.urlencode(
            {
                "id": accessory_id,
                "set": "On",
                "value": "true" if should_be_active else "false",
            }
        )
        url = f"{webhook_url}/?{query}"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                body = response.read().decode("utf-8", errors="replace")
            updates.append(
                {
                    "id": accessory_id,
                    "name": accessory.get("name"),
                    "active": should_be_active,
                    "ok": True,
                    "response": body,
                }
            )
        except Exception as exc:
            updates.append(
                {
                    "id": accessory_id,
                    "name": accessory.get("name"),
                    "active": should_be_active,
                    "ok": False,
                    "error": str(exc),
                }
            )
    return updates


def write_homekit_report(updates: list[dict[str, Any]]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    alarm_com = load_alarm_com()
    comparison = {}
    if ALARM_STATE_COMPARISON_PATH.exists():
        try:
            comparison = json.loads(ALARM_STATE_COMPARISON_PATH.read_text())
        except json.JSONDecodeError:
            comparison = {}
    generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    freshness = {
        "alarmPortalGeneratedAt": alarm_com.get("generatedAt"),
        "alarmPortalAge": age_label(alarm_com.get("generatedAt"), generated_at),
        "alarmCacheComparedAt": comparison.get("generatedAt"),
        "alarmCacheComparisonAge": age_label(comparison.get("generatedAt"), generated_at),
        "alarmCacheStaleCount": comparison.get("staleCount"),
    }
    payload = {
        "generatedAt": generated_at,
        "freshness": freshness,
        "updates": updates,
    }
    (DATA_DIR / "latest_homekit_virtual_sensors.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    lines = [
        "# HomeKit Virtual Sensors",
        "",
        f"- Generated: `{payload['generatedAt']}`",
        "",
        "## Freshness",
        "",
        f"- Alarm.com portal capture: `{freshness['alarmPortalGeneratedAt'] or 'n/a'}` age=`{freshness['alarmPortalAge']}`",
        f"- Alarm.com/Homebridge cache comparison: `{freshness['alarmCacheComparedAt'] or 'n/a'}` age=`{freshness['alarmCacheComparisonAge']}` stale=`{freshness['alarmCacheStaleCount'] if freshness['alarmCacheStaleCount'] is not None else 'n/a'}`",
        "",
        "## Tiles",
        "",
    ]
    if not updates:
        lines.append("- No virtual sensor updates were attempted.")
    else:
        for update in updates:
            status = "ok" if update.get("ok") else "failed"
            lines.append(
                f"- `{status}` `{update.get('name')}` active=`{update.get('active')}`"
                + (f" error=`{update.get('error')}`" if update.get("error") else "")
            )
    (REPORT_DIR / "homekit_virtual_sensors.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    config = load_config()
    latest = load_latest()
    window = max(
        int(config["alerts"]["alarm_websocket_recent_window"]),
        int(config["alerts"]["warning_recent_window"]),
    )
    alerts = apply_warning_silence(build_alerts(config, latest, recent_rows(window)), active_warning_silence())
    write_reports(alerts, latest)
    updates = update_homekit_virtual_sensors(config, alerts)
    write_homekit_report(updates)
    print(REPORT_DIR / "alerts.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
