#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import urllib.parse
import urllib.request
from bisect import bisect_left
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path.home() / "Library" / "Application Support" / "SmartHomeMonitor"
CONFIG_PATH = ROOT / "config" / "sources.json"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "smart_home.sqlite"
REPORT_DIR = ROOT / "reports"
SILENCE_PATH = DATA_DIR / "alerts_silenced_until.json"
COMBINED_ENERGY_PATH = DATA_DIR / "latest_combined_energy_monitor.json"
ENERGY_OBSERVABILITY_PATH = DATA_DIR / "latest_energy_observability.json"
ENERGY_ALERT_STABILIZATION_PATH = DATA_DIR / "energy_alert_stabilization.json"
ENERGY_ALERT_DELIVERY_PATH = DATA_DIR / "energy_alert_delivery.json"
ENERGY_OK_ANNOUNCEMENT_PATH = DATA_DIR / "energy_ok_announcement.json"
BUBBLER_ANNOUNCEMENT_PATH = DATA_DIR / "bubbler_announcement.json"
ENERGY_HIGH_CONTEXT_PATH = DATA_DIR / "latest_energy_high_context.json"
ENERGY_HIGH_CONTEXT_REPORT_PATH = REPORT_DIR / "energy_high_context.md"
ENERGY_HIGH_EVENTS_PATH = DATA_DIR / "energy_high_events.jsonl"
ENERGY_HIGH_EVENTS_REPORT_PATH = REPORT_DIR / "energy_high_events.md"
SCE_API_STATUS_PATH = DATA_DIR / "latest_sce_api.json"
ALARM_COM_PATH = DATA_DIR / "latest_alarm_com.json"
LATEST_CHARACTERISTICS_PATH = DATA_DIR / "latest_characteristics.json"
ALARM_STATE_COMPARISON_PATH = DATA_DIR / "latest_alarm_homebridge_state.json"
SENSE_NOW_PATH = DATA_DIR / "sense_now_latest.json"
SENSE_TRENDS_PATH = DATA_DIR / "sense_trends_latest.json"
DISPLAY_AWAKE_STATUS_PATH = DATA_DIR / "latest_display_awake.json"
ACTION_STATUS_URL = "http://127.0.0.1:18765/status"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
HOMEBRIDGE_DIR = Path.home() / ".homebridge"
HOMEBRIDGE_CONFIG_PATH = HOMEBRIDGE_DIR / "config.json"
ALARM_DOT_COM_PLUGIN = "homebridge-node-alarm-dot-com"


def running_from_runtime_root() -> bool:
    return ROOT.resolve() == RUNTIME_ROOT.resolve()


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


def load_energy_observability() -> dict[str, Any]:
    data = load_json_file(ENERGY_OBSERVABILITY_PATH)
    return data if isinstance(data, dict) else {}


PROJECTION_ALERT_LEVELS = {"clear": 0, "goal": 1, "warning": 2, "critical": 3}
PROJECTION_ALERT_TITLES = {
    "Energy projection exceeds goal": "goal",
    "Energy projection is high": "warning",
    "Energy projection is critical": "critical",
}


def raw_projection_alert_level(alerts: list[dict[str, Any]]) -> str:
    levels = [
        PROJECTION_ALERT_TITLES.get(str(alert.get("title") or ""), "clear")
        for alert in alerts
    ]
    return max(levels, key=lambda item: PROJECTION_ALERT_LEVELS[item], default="clear")


def alarm_source_fresh(observability: dict[str, Any]) -> bool:
    return any(
        str(item.get("source") or "") == "Alarm.com"
        and str(item.get("status") or "").lower() in {"fresh", "available"}
        for item in observability.get("sourceStatus") or []
        if isinstance(item, dict)
    )


def next_projection_stabilization(
    previous: dict[str, Any],
    raw_level: str,
    sample_at: str,
    alarm_fresh: bool,
    required_samples: int = 3,
    updated_at: str | None = None,
) -> dict[str, Any]:
    now = updated_at or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    raw_level = raw_level if raw_level in PROJECTION_ALERT_LEVELS else "clear"
    required_samples = max(1, int(required_samples))
    effective = str(previous.get("effectiveLevel") or raw_level)
    if effective not in PROJECTION_ALERT_LEVELS:
        effective = raw_level
    events = list(previous.get("events") or [])[-19:]
    state = {
        "version": 1,
        "updatedAt": now,
        "sampleAt": sample_at,
        "rawLevel": raw_level,
        "effectiveLevel": effective,
        "pendingLevel": previous.get("pendingLevel"),
        "consecutiveFreshSamples": int(previous.get("consecutiveFreshSamples") or 0),
        "requiredFreshSamples": required_samples,
        "lastCountedSampleAt": previous.get("lastCountedSampleAt"),
        "effectiveChangedAt": previous.get("effectiveChangedAt") or now,
        "alarmSourceFresh": bool(alarm_fresh),
        "reason": "stable",
        "events": events,
    }
    if not previous:
        state["pendingLevel"] = None
        state["consecutiveFreshSamples"] = 0
        state["reason"] = "initialized"
        if raw_level != "clear":
            state["events"] = events + [
                {"at": now, "event": "published immediately", "from": "clear", "to": raw_level, "sampleAt": sample_at}
            ]
        return state

    raw_rank = PROJECTION_ALERT_LEVELS[raw_level]
    effective_rank = PROJECTION_ALERT_LEVELS[effective]
    if raw_rank > effective_rank:
        state.update(
            {
                "effectiveLevel": raw_level,
                "pendingLevel": None,
                "consecutiveFreshSamples": 0,
                "effectiveChangedAt": now,
                "reason": "escalated immediately",
            }
        )
        state["events"] = events + [
            {"at": now, "event": "published escalation", "from": effective, "to": raw_level, "sampleAt": sample_at}
        ]
        return state
    if raw_level == effective:
        state.update({"pendingLevel": None, "consecutiveFreshSamples": 0, "reason": "stable"})
        return state

    state["pendingLevel"] = raw_level
    if not alarm_fresh:
        state.update({"consecutiveFreshSamples": 0, "reason": "held for fresh Alarm.com data"})
        return state
    if sample_at == previous.get("lastCountedSampleAt"):
        state["reason"] = "duplicate sample ignored"
        return state
    count = (
        int(previous.get("consecutiveFreshSamples") or 0) + 1
        if previous.get("pendingLevel") == raw_level
        else 1
    )
    state.update(
        {
            "consecutiveFreshSamples": count,
            "lastCountedSampleAt": sample_at,
            "reason": "waiting for confirmations",
        }
    )
    if count >= required_samples:
        event = "published clear" if raw_level == "clear" else "published downgrade"
        state.update(
            {
                "effectiveLevel": raw_level,
                "pendingLevel": None,
                "consecutiveFreshSamples": 0,
                "effectiveChangedAt": now,
                "reason": "downgrade confirmed",
            }
        )
        state["events"] = events + [
            {"at": now, "event": event, "from": effective, "to": raw_level, "sampleAt": sample_at}
        ]
    return state


def update_projection_stabilization(
    alerts: list[dict[str, Any]], observability: dict[str, Any], required_samples: int
) -> dict[str, Any]:
    previous = load_json_file(ENERGY_ALERT_STABILIZATION_PATH)
    previous = previous if isinstance(previous, dict) else {}
    state = next_projection_stabilization(
        previous,
        raw_projection_alert_level(alerts),
        str(observability.get("generatedAt") or ""),
        alarm_source_fresh(observability),
        required_samples,
    )
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ENERGY_ALERT_STABILIZATION_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    return state


def projection_notification_content(
    event: dict[str, Any], observability: dict[str, Any]
) -> tuple[str, str, str]:
    level = str(event.get("to") or "clear")
    live = observability.get("live") or {}
    projected = live.get("alarmProjectedKwh")
    budget = live.get("alarmBudgetKwh")
    dashboard = "http://127.0.0.1:18765/energy?days=7"
    if level == "clear":
        return (
            "Energy projection recovered",
            "Recovery confirmed",
            f"Projection is clear after three fresh confirmations. Dashboard: {dashboard}",
        )
    label = {"goal": "above goal", "warning": "high", "critical": "critical"}.get(level, level)
    detail = (
        f"Projected {float(projected):,.0f} kWh"
        if isinstance(projected, (int, float))
        else "Projected usage is elevated"
    )
    if isinstance(projected, (int, float)) and isinstance(budget, (int, float)):
        detail += f"; {float(projected) - float(budget):,.0f} kWh over the {float(budget):,.0f} kWh goal"
    return (
        f"Energy projection is {label}",
        f"Published severity: {level}",
        f"{detail}. Dashboard: {dashboard}",
    )


def apple_script_string(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def deliver_projection_notification(
    stabilization: dict[str, Any],
    observability: dict[str, Any],
    runner: Any = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    state = load_json_file(ENERGY_ALERT_DELIVERY_PATH)
    state = state if isinstance(state, dict) else {}
    events = stabilization.get("events") or []
    if not events:
        return state
    event = events[-1]
    event_key = "|".join(
        str(event.get(key) or "") for key in ("at", "event", "from", "to", "sampleAt")
    )
    if not event_key.strip("|") or event_key == state.get("lastProcessedEventKey"):
        return state
    now = updated_at or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    history = list(state.get("deliveries") or [])[-19:]
    event_name = str(event.get("event") or "")
    notifiable = event_name in {"published immediately", "published escalation", "published clear"}
    if not notifiable:
        delivery = {
            "at": now,
            "eventKey": event_key,
            "event": event_name,
            "level": event.get("to"),
            "status": "skipped",
            "reason": "downgrades do not notify",
            "transport": "macOS Notification Center",
            "readReceipt": "unavailable",
        }
        state.update(
            {
                "version": 1,
                "updatedAt": now,
                "lastProcessedEventKey": event_key,
                "lastDecision": "skipped",
                "deliveries": history + [delivery],
            }
        )
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        ENERGY_ALERT_DELIVERY_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        return state

    title, subtitle, message = projection_notification_content(event, observability)
    script = (
        f'display notification "{apple_script_string(message)}" '
        f'with title "{apple_script_string(title)}" subtitle "{apple_script_string(subtitle)}"'
    )
    command_runner = runner or subprocess.run
    try:
        result = command_runner(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        ok = result.returncode == 0
        error = None if ok else str(result.stderr or result.stdout or f"exit {result.returncode}").strip()
    except Exception as exc:
        ok = False
        error = str(exc)
    delivery = {
        "at": now,
        "eventKey": event_key,
        "event": event_name,
        "level": event.get("to"),
        "status": "accepted" if ok else "failed",
        "title": title,
        "message": message,
        "transport": "macOS Notification Center",
        "readReceipt": "unavailable",
    }
    if error:
        delivery["error"] = error
    state.update(
        {
            "version": 1,
            "updatedAt": now,
            "lastProcessedEventKey": event_key,
            "lastDecision": delivery["status"],
            "lastAttemptAt": now,
            "lastAcceptedAt": now if ok else state.get("lastAcceptedAt"),
            "deliveries": history + [delivery],
        }
    )
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ENERGY_ALERT_DELIVERY_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    return state


def deliver_homepod_transition_announcement(
    *,
    state_path: Path,
    active: bool | None,
    notify_from: bool,
    notify_to: bool,
    enabled: bool,
    message: str,
    announcement_id: str,
    runner: Any = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    previous = load_json_file(state_path)
    previous = previous if isinstance(previous, dict) else {}
    if active is None:
        return previous

    now = updated_at or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    state = {
        **previous,
        "version": 1,
        "updatedAt": now,
        "active": active,
        "lastDecision": "no transition",
    }
    if enabled and previous.get("active") is notify_from and active is notify_to:
        delivery = run_indoor_homepod_announcement(message, announcement_id, runner, now)
        state.update(
            {
                "lastDecision": delivery["status"],
                "lastTransitionAt": now,
                "lastDelivery": delivery,
            }
        )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    return state


def run_indoor_homepod_announcement(
    message: str,
    announcement_id: str,
    runner: Any = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    now = updated_at or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    command_runner = runner or subprocess.run
    try:
        result = command_runner(
            [
                sys.executable,
                str(ROOT / "scripts" / "washer_notifier.py"),
                "--announce-message",
                message,
                "--announcement-id",
                announcement_id,
            ],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        ok = result.returncode == 0
        error = None if ok else str(result.stderr or result.stdout or f"exit {result.returncode}").strip()
    except Exception as exc:
        ok = False
        error = str(exc)
    delivery = {
        "at": now,
        "status": "accepted" if ok else "failed",
        "message": message,
        "transport": "indoor HomePods via Music AirPlay",
        "readReceipt": "unavailable",
    }
    if error:
        delivery["error"] = error
    return delivery


def deliver_energy_ok_off_announcement(
    config: dict[str, Any],
    updates: list[dict[str, Any]],
    runner: Any = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    energy_ok = next(
        (item for item in updates if item.get("id") == "smart_home_high_load_v2"),
        None,
    )
    energy_high = next(
        (item for item in updates if item.get("id") == "smart_home_energy_budget_v2"),
        None,
    )
    if (
        not energy_ok
        or not energy_high
        or not energy_ok.get("ok")
        or not energy_high.get("ok")
        or energy_ok.get("verified") is not True
        or energy_high.get("verified") is not True
    ):
        previous = load_json_file(ENERGY_OK_ANNOUNCEMENT_PATH)
        return previous if isinstance(previous, dict) else {}

    alerts = config.get("alerts", {})
    previous = load_json_file(ENERGY_OK_ANNOUNCEMENT_PATH)
    previous = previous if isinstance(previous, dict) else {}
    prior_ok = previous.get("energyOkActive", previous.get("active"))
    prior_high = previous.get("energyHighActive")
    if prior_high is None and isinstance(prior_ok, bool):
        prior_high = not prior_ok
    ok_active = bool(energy_ok.get("active"))
    high_active = bool(energy_high.get("active"))
    now = updated_at or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    state = {
        **previous,
        "version": 2,
        "updatedAt": now,
        "active": ok_active,
        "energyOkActive": ok_active,
        "energyHighActive": high_active,
        "lastDecision": "no transition",
    }
    incident_started = (prior_ok is True and not ok_active) or (prior_high is False and high_active)
    if incident_started:
        if high_active:
            enabled = bool(alerts.get("energy_high_on_homepod_announcement", False))
            message = str(alerts.get("energy_high_on_homepod_message", "Energy is high. Check current energy use."))
            announcement_id = "energy_high_on"
        else:
            enabled = bool(alerts.get("energy_ok_off_homepod_announcement", False))
            message = str(
                alerts.get(
                    "energy_ok_off_homepod_message",
                    "Energy OK has turned off. Current energy status is unavailable.",
                )
            )
            announcement_id = "energy_ok_off"
        if enabled:
            delivery = run_indoor_homepod_announcement(message, announcement_id, runner, now)
            state.update(
                {
                    "lastDecision": delivery["status"],
                    "lastTransitionAt": now,
                    "lastDelivery": delivery,
                }
            )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ENERGY_OK_ANNOUNCEMENT_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    return state


def current_bubbler_active() -> bool | None:
    for item in load_latest_characteristics().values():
        if not isinstance(item, dict):
            continue
        name = str(item.get("accessory") or item.get("service") or "").replace("🐠", "").strip()
        if name == "Bubbler" and item.get("characteristic") == "On":
            value = item.get("value")
            return value if isinstance(value, bool) else None
    return None


def deliver_bubbler_on_announcement(
    config: dict[str, Any],
    runner: Any = None,
    updated_at: str | None = None,
    active: bool | None = None,
) -> dict[str, Any]:
    active = current_bubbler_active() if active is None else active
    alerts = config.get("alerts", {})
    return deliver_homepod_transition_announcement(
        state_path=BUBBLER_ANNOUNCEMENT_PATH,
        active=active,
        notify_from=False,
        notify_to=True,
        enabled=bool(alerts.get("bubbler_on_homepod_announcement", False)),
        message=str(alerts.get("bubbler_on_homepod_message", "The bubbler is back on.")),
        announcement_id="bubbler_on",
        runner=runner,
        updated_at=updated_at,
    )


def load_sce_api_status() -> dict[str, Any]:
    if not SCE_API_STATUS_PATH.exists():
        return {}
    try:
        data = json.loads(SCE_API_STATUS_PATH.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def load_alarm_com() -> dict[str, Any]:
    if not ALARM_COM_PATH.exists():
        return {}
    try:
        return json.loads(ALARM_COM_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def load_sense_now() -> dict[str, Any]:
    data = load_json_file(SENSE_NOW_PATH)
    return data if isinstance(data, dict) else {}


def load_sense_trends() -> dict[str, Any]:
    data = load_json_file(SENSE_TRENDS_PATH)
    return data if isinstance(data, dict) else {}


def as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    position = clamp(fraction, 0.0, 1.0) * (len(values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    weight = position - lower
    return values[lower] * (1 - weight) + values[upper] * weight


def same_time_history(sample_at: datetime, lookback_days: int, window_minutes: int) -> list[dict[str, Any]]:
    if not DB_PATH.exists():
        return []
    lower_bound = sample_at - timedelta(days=max(1, lookback_days))
    upper_bound = sample_at - timedelta(hours=1)
    try:
        with sqlite3.connect(DB_PATH) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                select captured_at, envoy_site_load_kw, envoy_production_kw, envoy_grid_net_kw,
                       battery_charging, battery_discharging, sense_load_kw, active_states_json
                from energy_observations
                where captured_at >= ? and captured_at <= ?
                order by captured_at
                """,
                (lower_bound.isoformat(), upper_bound.isoformat()),
            ).fetchall()
    except sqlite3.Error:
        return []
    sample_minute = sample_at.hour * 60 + sample_at.minute
    selected: list[dict[str, Any]] = []
    for row in rows:
        captured_at_raw = str(row["captured_at"] or "")
        observed_at = parse_captured_at(captured_at_raw)
        observed_minute = observed_at.hour * 60 + observed_at.minute
        minute_delta = abs(observed_minute - sample_minute)
        minute_delta = min(minute_delta, 1440 - minute_delta)
        if minute_delta > window_minutes:
            continue
        envoy_load = as_float(row["envoy_site_load_kw"])
        sense_load = as_float(row["sense_load_kw"])
        load_candidates = [value for value in (envoy_load, sense_load) if value is not None]
        try:
            active_states = json.loads(row["active_states_json"] or "[]")
        except json.JSONDecodeError:
            active_states = []
        selected.append(
            {
                "_capturedAtRaw": captured_at_raw,
                "capturedAt": observed_at.isoformat(timespec="seconds"),
                "loadKw": max(load_candidates) if load_candidates else None,
                "solarKw": as_float(row["envoy_production_kw"]),
                "gridNetKw": as_float(row["envoy_grid_net_kw"]),
                "batteryCharging": bool(row["battery_charging"]),
                "batteryDischarging": bool(row["battery_discharging"]),
                "activeStates": active_states if isinstance(active_states, list) else [],
            }
        )
    snapshot_conditions = snapshot_conditions_for_observations(
        lower_bound,
        upper_bound,
        [(str(item.get("_capturedAtRaw") or ""), parse_captured_at(str(item.get("_capturedAtRaw") or ""))) for item in selected],
    )
    for item in selected:
        captured_at_raw = str(item.pop("_capturedAtRaw", "") or "")
        condition = snapshot_conditions.get(captured_at_raw)
        if condition:
            item["condition"] = condition
    return selected


def solar_condition_band(production_kw: float | None) -> str:
    if not isinstance(production_kw, (int, float)):
        return "unknown"
    if production_kw < 0.2:
        return "dark"
    if production_kw < 1.5:
        return "low"
    if production_kw < 4.0:
        return "medium"
    return "strong"


def grid_condition_mode(grid_kw: float | None) -> str:
    if not isinstance(grid_kw, (int, float)):
        return "unknown"
    if grid_kw >= 0.5:
        return "importing"
    if grid_kw <= -0.5:
        return "exporting"
    return "neutral"


def battery_condition_mode(charging: bool, discharging: bool) -> str:
    if charging:
        return "charging"
    if discharging:
        return "discharging"
    return "idle"


def condition_states_from_sense_devices(devices: list[dict[str, Any]]) -> set[str]:
    states: set[str] = set()
    for device in devices:
        watts = as_float(device.get("watts"))
        if watts is None or watts < 500:
            continue
        name = str(device.get("name") or "")
        device_id = str(device.get("id") or "")
        text = f"{name} {device_id}".lower()
        if any(token in text for token in ("ev", "jeep", "charger", "charging")):
            states.add("EV charging")
    return states


def snapshot_conditions_for_observations(
    lower_bound: datetime,
    upper_bound: datetime,
    observations: list[tuple[str, datetime]],
    max_age_seconds: int = 90,
) -> dict[str, dict[str, Any]]:
    observations = [(raw, observed_at) for raw, observed_at in observations if raw]
    if not observations or not DB_PATH.exists():
        return {}
    try:
        with sqlite3.connect(DB_PATH) as db:
            db.row_factory = sqlite3.Row
            snapshot_rows = db.execute(
                """
                select captured_at
                from snapshots
                where captured_at >= ? and captured_at <= ?
                order by captured_at
                """,
                (lower_bound.isoformat(), upper_bound.isoformat()),
            ).fetchall()
    except sqlite3.Error:
        return {}

    snapshot_index: list[tuple[float, str]] = []
    for row in snapshot_rows:
        raw = str(row["captured_at"] or "")
        if raw:
            snapshot_index.append((parse_captured_at(raw).timestamp(), raw))
    if not snapshot_index:
        return {}
    snapshot_epochs = [item[0] for item in snapshot_index]
    matched_snapshots: dict[str, str] = {}
    for observed_raw, observed_at in observations:
        observed_epoch = observed_at.timestamp()
        position = bisect_left(snapshot_epochs, observed_epoch)
        candidates: list[tuple[float, str]] = []
        for index in (position - 1, position):
            if 0 <= index < len(snapshot_index):
                candidates.append((abs(snapshot_index[index][0] - observed_epoch), snapshot_index[index][1]))
        if not candidates:
            continue
        delta, snapshot_raw = min(candidates, key=lambda item: item[0])
        if delta <= max_age_seconds:
            matched_snapshots[observed_raw] = snapshot_raw
    if not matched_snapshots:
        return {}

    conditions_by_snapshot: dict[str, dict[str, Any]] = {}
    snapshot_keys = sorted(set(matched_snapshots.values()))
    try:
        with sqlite3.connect(DB_PATH) as db:
            db.row_factory = sqlite3.Row
            for index in range(0, len(snapshot_keys), 250):
                chunk = snapshot_keys[index : index + 250]
                placeholders = ",".join("?" for _ in chunk)
                rows = db.execute(
                    f"select captured_at, raw_json from snapshots where captured_at in ({placeholders})",
                    chunk,
                ).fetchall()
                for row in rows:
                    try:
                        raw_snapshot = json.loads(row["raw_json"] or "{}")
                    except json.JSONDecodeError:
                        continue
                    characteristics = (raw_snapshot.get("homeEvents") or {}).get("currentCharacteristics")
                    if isinstance(characteristics, dict):
                        conditions_by_snapshot[str(row["captured_at"])] = homekit_condition_signature(characteristics)
    except sqlite3.Error:
        return {}
    return {
        observed_raw: conditions_by_snapshot[snapshot_raw]
        for observed_raw, snapshot_raw in matched_snapshots.items()
        if snapshot_raw in conditions_by_snapshot
    }


def thermostat_condition_mode(thermostat: dict[str, Any]) -> str:
    if not thermostat.get("available"):
        return "unknown"
    if thermostat.get("cooling"):
        return "cooling"
    if thermostat.get("heating"):
        return "heating"
    return "idle"


def envelope_condition_mode(envelope: dict[str, Any]) -> str:
    if envelope.get("available") is False:
        return "unknown"
    return "open" if int(envelope.get("openCount") or 0) > 0 else "closed"


def blind_condition_band(blinds: dict[str, Any]) -> str:
    if not blinds.get("available"):
        return "unknown"
    count = int(blinds.get("count") or 0)
    if count <= 0:
        return "unknown"
    open_count = int(blinds.get("openCount") or 0)
    partial_count = int(blinds.get("partialCount") or 0)
    average = as_float(blinds.get("averagePosition"))
    if open_count == 0 and partial_count == 0:
        return "closed"
    if average is not None and average <= 20:
        return "closed"
    if average is not None and average >= 70:
        return "open"
    if open_count >= max(1, round(count * 0.5)):
        return "open"
    return "mixed"


def energy_condition_signature(
    *,
    states: set[str],
    production_kw: float | None,
    grid_kw: float | None,
    battery_charging: bool,
    battery_discharging: bool,
    hvac_mode: str = "unknown",
    envelope_mode: str = "unknown",
    blind_band: str = "unknown",
) -> dict[str, Any]:
    return {
        "evCharging": "EV charging" in states,
        "gridMode": grid_condition_mode(grid_kw),
        "solarBand": solar_condition_band(production_kw),
        "batteryMode": battery_condition_mode(battery_charging, battery_discharging),
        "hvacMode": hvac_mode or "unknown",
        "envelopeMode": envelope_mode or "unknown",
        "blindBand": blind_band or "unknown",
    }


def history_row_condition_signature(row: dict[str, Any]) -> dict[str, Any]:
    states = {str(item) for item in row.get("activeStates") or []}
    condition = row.get("condition") if isinstance(row.get("condition"), dict) else {}
    return energy_condition_signature(
        states=states,
        production_kw=as_float(row.get("solarKw")),
        grid_kw=as_float(row.get("gridNetKw")),
        battery_charging=row.get("batteryCharging") is True,
        battery_discharging=row.get("batteryDischarging") is True,
        hvac_mode=str(condition.get("hvacMode") or "unknown"),
        envelope_mode=str(condition.get("envelopeMode") or "unknown"),
        blind_band=str(condition.get("blindBand") or "unknown"),
    )


def condition_value_known(value: Any) -> bool:
    return value not in (None, "", "unknown")


def condition_signatures_match(row_signature: dict[str, Any], current_signature: dict[str, Any]) -> bool:
    required_keys = ("evCharging", "gridMode", "solarBand", "batteryMode")
    optional_keys = ("hvacMode", "envelopeMode", "blindBand")
    for key in required_keys:
        if row_signature.get(key) != current_signature.get(key):
            return False
    for key in optional_keys:
        row_value = row_signature.get(key)
        current_value = current_signature.get(key)
        if condition_value_known(row_value) and condition_value_known(current_value) and row_value != current_value:
            return False
    return True


def matching_condition_history(history: list[dict[str, Any]], signature: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in history if condition_signatures_match(history_row_condition_signature(row), signature)]


def alarm_components() -> dict[str, list[dict[str, Any]]]:
    alarm = load_alarm_com()
    systems = (alarm.get("alarmState") or {}).get("systems") or []
    if not systems:
        return {}
    components = systems[0].get("components") or {}
    return {key: value for key, value in components.items() if isinstance(value, list)}


def thermostat_energy_context(components: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    thermostats = components.get("thermostats") or []
    if not thermostats:
        return {"available": False}
    item = thermostats[0]
    state = item.get("state")
    desired = item.get("desiredState")
    state_text = str(item.get("stateText") or "")
    ambient = as_float(item.get("ambientTemp"))
    cool_setpoint = as_float(item.get("coolSetpoint"))
    heat_setpoint = as_float(item.get("heatSetpoint"))
    cooling = state == 3 or desired == 3 or state_text.lower() == "cooling"
    heating = state == 2 or desired == 2 or state_text.lower() == "heating"
    delta = ambient - cool_setpoint if ambient is not None and cool_setpoint is not None else None
    return {
        "available": True,
        "id": item.get("id"),
        "name": item.get("description") or "Thermostat",
        "stateText": state_text or None,
        "state": state,
        "desiredState": desired,
        "cooling": cooling,
        "heating": heating,
        "ambientF": ambient,
        "coolSetpointF": cool_setpoint,
        "heatSetpointF": heat_setpoint,
        "coolingDeltaF": delta,
        "humidity": as_float(item.get("humidityLevel")),
    }


def open_envelope_context(components: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    names: list[str] = []
    seen_count = 0
    envelope_terms = ("door", "window", "slider", "gate", "shed")
    for item in components.get("sensors") or []:
        name = str(item.get("description") or "")
        state_text = str(item.get("stateText") or "")
        if any(term in name.lower() for term in envelope_terms):
            seen_count += 1
            if state_text.lower() == "open":
                names.append(name)
    for item in components.get("garages") or []:
        seen_count += 1
        state_text = str(item.get("stateText") or "")
        if state_text.lower() == "open":
            names.append(str(item.get("description") or "Garage Door"))
    return {"available": seen_count > 0, "count": seen_count, "openCount": len(names), "openNames": sorted(set(names))}


def blind_energy_context(characteristics: dict[str, Any]) -> dict[str, Any]:
    positions: dict[str, float] = {}
    for item in characteristics.values():
        if item.get("service") != "WindowCovering" or item.get("characteristic") != "CurrentPosition":
            continue
        name = str(item.get("accessory") or "")
        position = as_float(item.get("value"))
        if name and position is not None:
            positions[name] = position
    open_names = sorted(name for name, position in positions.items() if position >= 60)
    partial_names = sorted(name for name, position in positions.items() if 20 < position < 60)
    closed_names = sorted(name for name, position in positions.items() if position <= 20)
    return {
        "available": bool(positions),
        "count": len(positions),
        "openCount": len(open_names),
        "partialCount": len(partial_names),
        "closedCount": len(closed_names),
        "openNames": open_names[:12],
        "partialNames": partial_names[:12],
        "averagePosition": round(sum(positions.values()) / len(positions), 1) if positions else None,
    }


def homekit_hvac_mode(characteristics: dict[str, Any]) -> str:
    modes: set[str] = set()
    for item in characteristics.values():
        if not isinstance(item, dict):
            continue
        if item.get("plugin") != ALARM_DOT_COM_PLUGIN:
            continue
        if item.get("characteristic") != "CurrentHeatingCoolingState":
            continue
        value = item.get("value")
        if value == 2:
            modes.add("cooling")
        elif value == 1:
            modes.add("heating")
        elif value == 0:
            modes.add("idle")
    if "cooling" in modes:
        return "cooling"
    if "heating" in modes:
        return "heating"
    if "idle" in modes:
        return "idle"
    return "unknown"


def homekit_envelope_context(characteristics: dict[str, Any]) -> dict[str, Any]:
    names: list[str] = []
    seen_count = 0
    envelope_terms = ("door", "window", "slider", "gate", "shed")
    for item in characteristics.values():
        if not isinstance(item, dict):
            continue
        if item.get("plugin") != ALARM_DOT_COM_PLUGIN:
            continue
        name = str(item.get("service") or item.get("accessory") or "")
        characteristic = str(item.get("characteristic") or "")
        name_lower = name.lower()
        if characteristic == "ContactSensorState":
            if not any(term in name_lower for term in envelope_terms):
                continue
            seen_count += 1
            if item.get("value") == 1:
                names.append(name)
        elif characteristic == "CurrentDoorState":
            seen_count += 1
            if item.get("value") != 1:
                names.append(name)
    return {"available": seen_count > 0, "count": seen_count, "openCount": len(names), "openNames": sorted(set(names))}


def homekit_condition_signature(characteristics: dict[str, Any]) -> dict[str, str]:
    blinds = blind_energy_context(characteristics)
    envelope = homekit_envelope_context(characteristics)
    return {
        "hvacMode": homekit_hvac_mode(characteristics),
        "envelopeMode": envelope_condition_mode(envelope),
        "blindBand": blind_condition_band(blinds),
    }


def peak_rate_active(characteristics: dict[str, Any]) -> bool:
    for item in characteristics.values():
        text = f"{item.get('accessory') or ''} {item.get('service') or ''}"
        if "Peak Rate" in text and item.get("characteristic") == "ContactSensorState":
            return item.get("value") == 1
    return False


def live_load_candidates(config: dict[str, Any], latest: dict[str, Any], sample_at: datetime) -> list[dict[str, Any]]:
    metrics = latest.get("homebridge", {}).get("logs", {}).get("latestMetrics", {})
    candidates: list[dict[str, Any]] = []
    envoy_load = as_float(metrics.get("enphase_consumption_total_kw"))
    if envoy_load is not None:
        candidates.append({"source": "Envoy", "kw": envoy_load})
    sense_now = load_sense_now()
    sense_watts = as_float(sense_now.get("watts"))
    sense_captured_at = parse_captured_at(sense_now.get("capturedAt"))
    sense_fresh_seconds = float(config["alerts"].get("sense_live_high_load_fresh_seconds", 180))
    if sense_watts is not None and sense_captured_at and abs((sample_at - sense_captured_at).total_seconds()) <= sense_fresh_seconds:
        candidates.append(
            {
                "source": "Sense",
                "kw": sense_watts / 1000.0,
                "capturedAt": sense_captured_at.isoformat(timespec="seconds"),
                "devices": [
                    {"name": item.get("name"), "watts": item.get("watts"), "id": item.get("id")}
                    for item in sense_now.get("devices") or []
                    if isinstance(item, dict)
                ],
            }
        )
    return candidates


def recommended_action_for_sense_device(name: str) -> str | None:
    normalized = name.lower()
    if any(token in normalized for token in ("ev", "jeep", "charger", "charging")):
        return "Pause or delay EV charging."
    if "dryer" in normalized:
        return "Pause the dryer if practical, or avoid starting another large load."
    if "washer" in normalized:
        return "Pause the washer if practical, or avoid starting another large load."
    if "hot tub" in normalized or "spa" in normalized:
        return "Delay hot tub heating."
    if "AC" in name.upper() or "air conditioner" in normalized:
        return "Turn AC off or raise the cooling setpoint."
    return None


def energy_high_context(config: dict[str, Any], latest: dict[str, Any]) -> dict[str, Any]:
    sample_at = parse_captured_at(latest.get("captured_at"))
    metrics = latest.get("homebridge", {}).get("logs", {}).get("latestMetrics", {})
    candidates = live_load_candidates(config, latest, sample_at)
    live_load_kw = max((item["kw"] for item in candidates), default=None)
    base_threshold = float(config["alerts"].get("energy_high_kw", config["alerts"].get("high_load_kw", 8)))
    min_threshold = float(config["alerts"].get("energy_high_min_kw", 2.5))
    max_threshold = float(config["alerts"].get("energy_high_max_kw", 5.5))
    threshold = base_threshold
    adjustments: list[dict[str, Any]] = []
    reasons: list[str] = []
    actions: list[str] = []

    components = alarm_components()
    thermostat = thermostat_energy_context(components)
    envelope = open_envelope_context(components)
    characteristics = load_latest_characteristics()
    blinds = blind_energy_context(characteristics)
    peak_rate = peak_rate_active(characteristics)
    production_kw = as_float(metrics.get("enphase_production_kw"))
    net_kw = as_float(metrics.get("enphase_consumption_net_kw"))
    battery_charging = metrics.get("enphase_battery_charging") is True
    battery_discharging = metrics.get("enphase_battery_discharging") is True
    sense_devices: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.get("source") == "Sense":
            sense_devices = [item for item in candidate.get("devices") or [] if isinstance(item.get("watts"), (int, float))]
    combined_states = {str(item) for item in (load_combined_energy().get("states") or [])}
    condition_states = combined_states | condition_states_from_sense_devices(sense_devices)
    current_condition = energy_condition_signature(
        states=condition_states,
        production_kw=production_kw,
        grid_kw=net_kw,
        battery_charging=battery_charging,
        battery_discharging=battery_discharging,
        hvac_mode=thermostat_condition_mode(thermostat),
        envelope_mode=envelope_condition_mode(envelope),
        blind_band=blind_condition_band(blinds),
    )

    history = same_time_history(
        sample_at,
        int(config["alerts"].get("energy_high_history_days", 21)),
        int(config["alerts"].get("energy_high_history_window_minutes", 60)),
    )
    condition_history = matching_condition_history(history, current_condition)
    normal_history_min_samples = int(config["alerts"].get("energy_high_normal_history_min_samples", 24))
    historical_loads = [item["loadKw"] for item in history if isinstance(item.get("loadKw"), (int, float))]
    condition_loads = [item["loadKw"] for item in condition_history if isinstance(item.get("loadKw"), (int, float))]
    normal_history_loads = condition_loads if len(condition_loads) >= normal_history_min_samples else historical_loads
    historical_solar = [item["solarKw"] for item in history if isinstance(item.get("solarKw"), (int, float))]
    load_p75 = percentile(historical_loads, 0.75)
    load_p90 = percentile(historical_loads, 0.90)
    normal_load_p90 = percentile(normal_history_loads, 0.90)
    solar_p75 = percentile(historical_solar, 0.75)
    historical_threshold = None
    normal_threshold = None
    if load_p75 is not None:
        historical_threshold = clamp(load_p75 + float(config["alerts"].get("energy_high_history_margin_kw", 0.35)), min_threshold, max_threshold)
        if historical_threshold < threshold:
            adjustments.append({"reason": "higher than usual for this time of day", "kw": round(historical_threshold - threshold, 3)})
            threshold = historical_threshold
    if normal_load_p90 is not None and len(normal_history_loads) >= normal_history_min_samples:
        normal_threshold = clamp(
            normal_load_p90 + float(config["alerts"].get("energy_high_normal_margin_kw", 0.75)),
            min_threshold,
            float(config["alerts"].get("energy_high_normal_max_kw", max_threshold)),
        )

    if thermostat.get("cooling"):
        delta = -0.35
        threshold += delta
        adjustments.append({"reason": "AC is cooling", "kw": delta})
        actions.append("Turn AC off or raise the cooling setpoint.")
    if thermostat.get("cooling") and isinstance(thermostat.get("coolingDeltaF"), (int, float)) and thermostat["coolingDeltaF"] >= 3:
        delta = 0.2
        threshold += delta
        adjustments.append({"reason": "comfort guard: room is still well above setpoint", "kw": delta})
    if envelope["openCount"] and (thermostat.get("cooling") or thermostat.get("heating")):
        delta = -min(0.8, 0.25 * envelope["openCount"])
        threshold += delta
        adjustments.append({"reason": f"{envelope['openCount']} door/window/garage opening(s) while HVAC is active", "kw": round(delta, 3)})
        actions.append("Close open doors, windows, sliders, gates, or garage doors.")
    if thermostat.get("cooling") and blinds.get("openCount") and isinstance(production_kw, (int, float)) and production_kw >= 0.5:
        delta = -min(0.5, 0.04 * int(blinds["openCount"]))
        threshold += delta
        adjustments.append({"reason": f"{blinds['openCount']} blind/shade covering(s) are open during cooling daylight", "kw": round(delta, 3)})
        actions.append("Close sun-facing blinds or shades.")
    if peak_rate:
        delta = -0.3
        threshold += delta
        adjustments.append({"reason": "peak-rate calendar is active", "kw": delta})
    if battery_discharging:
        delta = -0.25
        threshold += delta
        adjustments.append({"reason": "battery is discharging", "kw": delta})
    if isinstance(net_kw, (int, float)) and net_kw >= 0.5:
        delta = -0.25
        threshold += delta
        adjustments.append({"reason": "grid import is meaningful", "kw": delta})
    if battery_charging and isinstance(net_kw, (int, float)) and net_kw <= 0 and isinstance(production_kw, (int, float)) and production_kw >= 3:
        delta = 0.6
        threshold += delta
        adjustments.append({"reason": "solar is strong and battery is charging", "kw": delta})
    elif (
        isinstance(production_kw, (int, float))
        and solar_p75 is not None
        and solar_p75 >= 0.5
        and production_kw < solar_p75 * 0.55
    ):
        delta = -0.25
        threshold += delta
        adjustments.append({"reason": "solar is weak versus this time of day history", "kw": delta})

    context_threshold = clamp(threshold, min_threshold, max_threshold)
    threshold = context_threshold
    if normal_threshold is not None and normal_threshold > base_threshold and normal_threshold > threshold:
        adjustments.append(
            {
                "reason": "same-time history says this load can be normal",
                "kw": round(normal_threshold - threshold, 3),
            }
        )
        threshold = normal_threshold
    if live_load_kw is not None and live_load_kw >= threshold:
        reasons.append(f"live load {live_load_kw:.2f} kW is above dynamic threshold {threshold:.2f} kW")
    for item in sorted(sense_devices, key=lambda device: float(device.get("watts") or 0), reverse=True):
        name = str(item.get("name") or "")
        if str(item.get("id") or "").lower() == "solar" or name.lower() == "solar":
            continue
        if float(item.get("watts") or 0) >= 700:
            reasons.append(f"Sense sees {name} at {float(item.get('watts') or 0):.0f} W")
            device_action = recommended_action_for_sense_device(name)
            if device_action:
                actions.insert(0, device_action)
            break
    active = bool(live_load_kw is not None and live_load_kw >= threshold)
    if not active:
        reasons.append(
            f"live load {live_load_kw:.2f} kW is below dynamic threshold {threshold:.2f} kW"
            if live_load_kw is not None
            else "no fresh live load candidate is available"
        )
    return {
        "generatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "sampleAt": sample_at.isoformat(timespec="seconds"),
        "active": active,
        "liveLoadKw": round(live_load_kw, 3) if live_load_kw is not None else None,
        "thresholdKw": round(threshold, 3),
        "baseThresholdKw": round(base_threshold, 3),
        "candidates": candidates,
        "adjustments": adjustments,
        "reasons": reasons,
        "recommendedActions": list(dict.fromkeys(actions)) if active else [],
        "thermostat": thermostat,
        "envelope": envelope,
        "blinds": blinds,
        "peakRate": peak_rate,
        "solar": {"productionKw": production_kw, "sameTimeP75Kw": round(solar_p75, 3) if solar_p75 is not None else None},
        "grid": {"netKw": net_kw},
        "battery": {"charging": battery_charging, "discharging": battery_discharging},
        "history": {
            "sampleCount": len(historical_loads),
            "conditionSampleCount": len(condition_loads),
            "normalSampleCount": len(normal_history_loads),
            "normalSource": "matching conditions" if len(condition_loads) >= normal_history_min_samples else "same time",
            "conditionSignature": current_condition,
            "sameTimeLoadP75Kw": round(load_p75, 3) if load_p75 is not None else None,
            "sameTimeLoadP90Kw": round(load_p90, 3) if load_p90 is not None else None,
            "normalLoadP90Kw": round(normal_load_p90, 3) if normal_load_p90 is not None else None,
            "historicalThresholdKw": round(historical_threshold, 3) if historical_threshold is not None else None,
            "sameTimeNormalThresholdKw": round(normal_threshold, 3) if normal_threshold is not None else None,
        },
    }


def write_energy_high_context(context: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ENERGY_HIGH_CONTEXT_PATH.write_text(json.dumps(context, indent=2, sort_keys=True) + "\n")
    record_energy_high_transition(context)
    lines = [
        "# Energy High Context",
        "",
        f"- Generated: `{context.get('generatedAt')}`",
        f"- Sample: `{context.get('sampleAt')}`",
        f"- Active: `{context.get('active')}`",
        f"- Live load: `{context.get('liveLoadKw')}` kW",
        f"- Dynamic threshold: `{context.get('thresholdKw')}` kW",
        "",
        "## Why",
        "",
    ]
    for reason in context.get("reasons") or []:
        lines.append(f"- {reason}")
    lines.extend(["", "## Suggested Actions", ""])
    actions = context.get("recommendedActions") or []
    if actions:
        for action in actions:
            lines.append(f"- {action}")
    else:
        lines.append("- No specific action is recommended while the tile is clear.")
    lines.extend(["", "## Context", ""])
    for key in ("thermostat", "envelope", "blinds", "solar", "grid", "battery", "history"):
        lines.append(f"- `{key}`: `{json.dumps(context.get(key), sort_keys=True)}`")
    ENERGY_HIGH_CONTEXT_REPORT_PATH.write_text("\n".join(lines) + "\n")


def energy_high_primary_load(context: dict[str, Any]) -> dict[str, Any] | None:
    devices: list[dict[str, Any]] = []
    for candidate in context.get("candidates") or []:
        if not isinstance(candidate, dict) or candidate.get("source") != "Sense":
            continue
        for device in candidate.get("devices") or []:
            if not isinstance(device, dict):
                continue
            name = str(device.get("name") or "")
            if str(device.get("id") or "").lower() == "solar" or name.lower() == "solar":
                continue
            watts = as_float(device.get("watts"))
            if watts is None:
                continue
            devices.append({"id": device.get("id"), "name": name or "Unknown", "watts": watts})
    if not devices:
        return None
    return max(devices, key=lambda item: float(item.get("watts") or 0))


def load_energy_high_events(limit: int = 40) -> list[dict[str, Any]]:
    if not ENERGY_HIGH_EVENTS_PATH.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        lines = ENERGY_HIGH_EVENTS_PATH.read_text().splitlines()
    except OSError:
        return []
    for line in lines[-max(limit * 2, limit) :]:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events[-limit:]


def record_energy_high_transition(context: dict[str, Any]) -> dict[str, Any] | None:
    current_active = context.get("active")
    if not isinstance(current_active, bool):
        return None
    previous = next((event for event in reversed(load_energy_high_events()) if isinstance(event.get("active"), bool)), None)
    if previous and previous.get("active") == current_active:
        write_energy_high_events_report(load_energy_high_events(), context)
        return None
    if previous is None:
        event_type = "observed_on" if current_active else "observed_off"
    else:
        event_type = "turned_on" if current_active else "turned_off"
    event = {
        "eventType": event_type,
        "recordedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "sampleAt": context.get("sampleAt"),
        "generatedAt": context.get("generatedAt"),
        "active": current_active,
        "previousActive": previous.get("active") if previous else None,
        "liveLoadKw": context.get("liveLoadKw"),
        "thresholdKw": context.get("thresholdKw"),
        "reasons": context.get("reasons") or [],
        "recommendedActions": context.get("recommendedActions") or [],
        "primaryLoad": energy_high_primary_load(context),
        "thermostat": context.get("thermostat"),
        "envelope": context.get("envelope"),
        "blinds": context.get("blinds"),
        "battery": context.get("battery"),
    }
    ENERGY_HIGH_EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ENERGY_HIGH_EVENTS_PATH.open("a") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
    events = load_energy_high_events()
    write_energy_high_events_report(events, context)
    return event


def write_energy_high_events_report(events: list[dict[str, Any]], current_context: dict[str, Any] | None = None) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Energy High Events",
        "",
        f"- Generated: `{datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}`",
        f"- Events retained in this report: `{len(events)}`",
        "",
    ]
    if current_context:
        active_text = "on" if current_context.get("active") else "off"
        primary = energy_high_primary_load(current_context) or {}
        primary_text = (
            f"{primary.get('name')} {float(primary.get('watts')):.0f} W"
            if isinstance(primary.get("watts"), (int, float))
            else str(primary.get("name") or "")
        )
        reasons = "; ".join(str(item) for item in (current_context.get("reasons") or [])[:2]) or "none"
        actions = "; ".join(str(item) for item in (current_context.get("recommendedActions") or [])[:2]) or "none"
        lines.extend(
            [
                "## Current State",
                "",
                f"- State: `{active_text}`",
                f"- Sample: `{current_context.get('sampleAt') or ''}`",
                f"- Load: `{current_context.get('liveLoadKw'):.2f} kW`" if isinstance(current_context.get("liveLoadKw"), (int, float)) else "- Load: `unknown`",
                f"- Threshold: `{current_context.get('thresholdKw'):.2f} kW`" if isinstance(current_context.get("thresholdKw"), (int, float)) else "- Threshold: `unknown`",
                f"- Primary load: `{primary_text or 'none'}`",
                f"- Reason: {reasons}",
                f"- Action: {actions}",
                "",
            ]
        )
    if not events:
        lines.append("- No ENERGY HIGH observations or transitions recorded yet.")
    else:
        lines.extend(["| Time | Event | Load | Threshold | Primary load | Reason | Action |", "|---|---:|---:|---:|---|---|---|"])
        for event in reversed(events[-40:]):
            primary = event.get("primaryLoad") if isinstance(event.get("primaryLoad"), dict) else {}
            primary_text = (
                f"{primary.get('name')} {float(primary.get('watts')):.0f} W"
                if isinstance(primary.get("watts"), (int, float))
                else str(primary.get("name") or "")
            )
            reason = "; ".join(str(item) for item in (event.get("reasons") or [])[:2])
            action = "; ".join(str(item) for item in (event.get("recommendedActions") or [])[:2])
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(event.get("sampleAt") or event.get("recordedAt") or ""),
                        str(event.get("eventType") or ""),
                        f"{event.get('liveLoadKw'):.2f} kW" if isinstance(event.get("liveLoadKw"), (int, float)) else "",
                        f"{event.get('thresholdKw'):.2f} kW" if isinstance(event.get("thresholdKw"), (int, float)) else "",
                        primary_text,
                        reason,
                        action,
                    ]
                )
                + " |"
            )
    ENERGY_HIGH_EVENTS_REPORT_PATH.write_text("\n".join(lines) + "\n")


def load_display_awake_status() -> dict[str, Any]:
    data = load_json_file(DISPLAY_AWAKE_STATUS_PATH)
    return data if isinstance(data, dict) else {}


def load_latest_characteristics() -> dict[str, Any]:
    if not LATEST_CHARACTERISTICS_PATH.exists():
        return {}
    try:
        data = json.loads(LATEST_CHARACTERISTICS_PATH.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def load_json_file(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def load_alarm_state_comparison() -> dict[str, Any]:
    if not ALARM_STATE_COMPARISON_PATH.exists():
        return {}
    try:
        data = json.loads(ALARM_STATE_COMPARISON_PATH.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def load_action_status() -> dict[str, Any]:
    try:
        with urllib.request.urlopen(ACTION_STATUS_URL, timeout=3) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
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


def recent_smarthq_home_events(limit: int = 200) -> list[sqlite3.Row]:
    if not DB_PATH.exists():
        return []
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        return list(
            db.execute(
                """
                select captured_at, event_type, component, message
                from home_events
                where component = 'SmartHQ'
                   or lower(coalesce(message, '')) like '%smarthq%'
                order by captured_at desc
                limit ?
                """,
                (limit,),
            )
        )


def recent_component_home_events(components: list[str], limit: int = 200) -> list[sqlite3.Row]:
    if not DB_PATH.exists() or not components:
        return []
    placeholders = ",".join("?" for _ in components)
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        return list(
            db.execute(
                f"""
                select captured_at, event_type, component, message
                from home_events
                where component in ({placeholders})
                order by captured_at desc
                limit ?
                """,
                (*components, limit),
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
    if (
        "characteristic not in required or optional characteristic section" in lower
        or "has an invalid 'name' characteristic" in lower
    ):
        return "HomeKit compatibility"
    if "security system" in lower or "alarm.com" in lower or "websocket token fetch returned 403" in lower:
        return "Alarm.com auth/websocket"
    if "smarthq" in lower and is_smarthq_auth_failure_message(lower):
        return "SmartHQ auth"
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
    if "[office]" in lower or "tahoma" in lower or "192.168.0.164:8443" in lower or "192.168.0.90:8443" in lower:
        return "Office TaHoma"
    if "unifi" in lower or "occupancy" in lower:
        return "UniFi occupancy"
    if "mopar" in lower:
        return "Mopar"
    if "smarthq" in lower:
        return "SmartHQ"
    if "enphase" in lower or "envoy" in lower:
        if any(token in lower for token in ("timeout", "etimedout", "enetunreach", "econnrefused")):
            return "Enphase Envoy local communication"
        if "valid finite number" in lower or "nan" in lower:
            return "Enphase Envoy invalid characteristic"
        return "Enphase Envoy"
    return "Other"


def has_unifi_auth_warning(warnings: list[Any]) -> bool:
    for warning in warnings:
        text = str(warning)
        if "[homebridge-unifi-occupancy]" in text and "401" in text:
            return True
    return False


def has_unifi_api_warning(warnings: list[Any]) -> bool:
    for warning in warnings:
        text = str(warning).lower()
        if "homebridge-unifi-occupancy" not in text:
            continue
        if any(token in text for token in ("502", "504", "gateway timeout", "bad gateway", "timeout", "etimedout")):
            return True
    return False


def has_sense_live_auth_warning(warnings: list[Any]) -> bool:
    for warning in warnings:
        if warning_category(str(warning)) == "Sense live websocket auth":
            return True
    return False


def is_smarthq_auth_failure_message(message: str) -> bool:
    lower = message.lower()
    return is_integration_auth_failure_message(lower)


def is_integration_auth_failure_message(message: str) -> bool:
    lower = message.lower()
    return any(
        token in lower
        for token in (
            "failed to get access token",
            "failed to refresh access token",
            "failed to re-authenticate",
            "re-auth failed",
            "authentication failed",
            "authentication error",
            "unexpected server response: 401",
            "not authenticated",
            "unauthorized",
            "401 unauthorized",
            "no authorization code",
            "invalid_grant",
            "invalid refresh token",
            "login failed",
            "loginsession error",
            "no credentials found",
            "no username found",
            "no password found",
        )
    )


def is_smarthq_auth_success_message(message: str) -> bool:
    lower = message.lower()
    return any(
        token in lower
        for token in (
            "successfully re-authenticated with credentials",
            "restoring existing accessory from cache",
            "adding new accessory",
        )
    )


def is_integration_auth_success_message(message: str, success_tokens: tuple[str, ...]) -> bool:
    lower = message.lower()
    return any(token in lower for token in success_tokens)


def event_value(event: Any, key: str, default: Any = None) -> Any:
    try:
        return event[key]
    except Exception:
        if isinstance(event, dict):
            return event.get(key, default)
        return default


def integration_auth_status_from_events(
    events: list[Any],
    components: set[str],
    success_tokens: tuple[str, ...],
    now_raw: str | None = None,
) -> dict[str, Any]:
    last_failure: dict[str, Any] | None = None
    last_success: dict[str, Any] | None = None
    now = parse_captured_at(now_raw)
    for event in events:
        component = str(event_value(event, "component", "") or "")
        message = str(event_value(event, "message", "") or "")
        if component not in components:
            continue
        captured_at = str(event_value(event, "captured_at", "") or "")
        item = {"capturedAt": captured_at, "component": component, "message": message}
        if is_integration_auth_failure_message(message):
            if not last_failure or (parse_report_time(captured_at) or now) > (parse_report_time(last_failure["capturedAt"]) or now):
                last_failure = item
        elif is_integration_auth_success_message(message, success_tokens):
            if not last_success or (parse_report_time(captured_at) or now) > (parse_report_time(last_success["capturedAt"]) or now):
                last_success = item

    active = False
    if last_failure:
        failure_time = parse_report_time(last_failure["capturedAt"]) or now
        success_time = parse_report_time(last_success["capturedAt"]) if last_success else None
        active = success_time is None or failure_time > success_time

    return {
        "active": active,
        "lastFailure": last_failure,
        "lastSuccess": last_success,
    }


def smarthq_auth_status_from_events(events: list[Any], now_raw: str | None = None) -> dict[str, Any]:
    return integration_auth_status_from_events(
        events,
        {"SmartHQ"},
        (
            "successfully re-authenticated with credentials",
            "restoring existing accessory from cache",
            "adding new accessory",
        ),
        now_raw,
    )


def smarthq_platform_configured(latest: dict[str, Any]) -> bool:
    return any(
        item.get("platform") == "SmartHQ"
        for item in latest.get("homebridge", {}).get("config", {}).get("platforms", [])
    )


def homebridge_warning_captured_at(message: str, fallback: str | None) -> str | None:
    match = re.match(r"^\[(\d{1,2}/\d{1,2}/\d{4}, \d{1,2}:\d{2}:\d{2} [AP]M)\]", message)
    if not match:
        return fallback
    try:
        return datetime.strptime(match.group(1), "%m/%d/%Y, %I:%M:%S %p").replace(tzinfo=LOCAL_TZ).isoformat()
    except ValueError:
        return fallback


def smart_hq_auth_status(config: dict[str, Any], latest: dict[str, Any], current_warnings: list[Any]) -> dict[str, Any]:
    event_limit = int(config.get("alerts", {}).get("smarthq_auth_event_limit", 200))
    events: list[Any] = list(recent_smarthq_home_events(event_limit))
    captured_at = latest.get("captured_at")
    for warning in current_warnings:
        message = str(warning)
        if warning_category(message) == "SmartHQ auth":
            events.append({
                "captured_at": homebridge_warning_captured_at(message, captured_at),
                "component": "SmartHQ",
                "message": message,
            })
    return smarthq_auth_status_from_events(events, captured_at)


def configured_tahoma_components(latest: dict[str, Any]) -> list[str]:
    components: list[str] = []
    for item in latest.get("homebridge", {}).get("config", {}).get("platforms", []):
        if item.get("platform") == "Tahoma" and item.get("name"):
            components.append(str(item["name"]))
    return sorted(set(components))


def retained_integration_auth_alerts(config: dict[str, Any], latest: dict[str, Any], current_warnings: list[Any]) -> list[dict[str, str]]:
    event_limit_raw = config.get("alerts", {}).get("integration_auth_event_limit")
    if event_limit_raw is None:
        return []
    event_limit = int(event_limit_raw)
    definitions: list[dict[str, Any]] = [
        {
            "title": "Sense live websocket authentication is failing",
            "label": "Sense live meter",
            "components": ["Sense Energy Meter"],
            "successTokens": (
                "sense websocket open",
                "sense websocket connected",
                "authenticated with sense",
                "sense api capture succeeded",
                "received data.",
            ),
            "freshness": "Sense live watt readings may be cached or unavailable until a later websocket/auth success.",
        },
    ]
    if any(item.get("platform") == "Alarmdotcom" for item in latest.get("homebridge", {}).get("config", {}).get("platforms", [])):
        definitions.append(
            {
                "title": "Alarm.com child bridge authentication is failing",
                "label": "Alarm.com child bridge",
                "components": ["Security System"],
                "successTokens": (
                    "received 1 partitions from alarm.com",
                    "received 19 sensors from alarm.com",
                    "websocket connection established",
                ),
                "freshness": "Alarm.com Homebridge accessory state may be cached; portal capture remains the preferred current-state source.",
            }
        )
    tahoma_components = configured_tahoma_components(latest)
    if tahoma_components:
        definitions.append(
            {
                "title": "TaHoma authentication is failing",
                "label": "TaHoma child bridge",
                "components": tahoma_components,
                "successTokens": ("configure device", "devices discovered", "post /events/register", "get /setup/devices"),
                "freshness": "TaHoma shade/blind accessories may remain visible from cache while the affected bridge is not refreshing cloud state.",
            }
        )

    alerts: list[dict[str, str]] = []
    for definition in definitions:
        components = [str(item) for item in definition["components"]]
        events: list[Any] = list(recent_component_home_events(components, event_limit))
        if definition["title"] == "Sense live websocket authentication is failing":
            sense_trends = load_sense_trends()
            if sense_trends.get("capturedAt") and int(sense_trends.get("daysCaptured") or 0) > 0:
                events.append(
                    {
                        "captured_at": sense_trends["capturedAt"],
                        "component": "Sense Energy Meter",
                        "message": "Sense API capture succeeded",
                    }
                )
        for warning in current_warnings:
            message = str(warning)
            if any(component in message for component in components) and is_integration_auth_failure_message(message):
                events.append({
                    "captured_at": homebridge_warning_captured_at(message, latest.get("captured_at")),
                    "component": components[0],
                    "message": message,
                })
        status = integration_auth_status_from_events(
            events,
            set(components),
            tuple(str(item).lower() for item in definition["successTokens"]),
            latest.get("captured_at"),
        )
        if not status.get("active"):
            continue
        failure = status.get("lastFailure") or {}
        failure_at = failure.get("capturedAt") or "unknown"
        failure_component = failure.get("component") or definition["label"]
        failure_message = re.sub(r", Submit Bugs Here:.*$", "", str(failure.get("message") or "authentication failed"))
        alerts.append(
            {
                "severity": "warning",
                "title": str(definition["title"]),
                "detail": (
                    f"{definition['label']} last failed auth at `{failure_at}` on `{failure_component}`: "
                    f"`{failure_message[-220:]}`. {definition['freshness']}"
                ),
            }
        )
    return alerts


def has_envoy_local_comm_warning(warnings: list[Any]) -> bool:
    for warning in warnings:
        if warning_category(str(warning)) == "Enphase Envoy local communication":
            return True
    return False


def count_envoy_local_comm_warnings(warnings: list[Any]) -> int:
    total = 0
    for warning in warnings:
        message = str(warning)
        collapsed = re.search(r"Collapsed (\d+) Envoy warning lines", message)
        if warning_category(message) == "Enphase Envoy local communication":
            total += int(collapsed.group(1)) if collapsed else 1
    return total


def distinct_warning_messages(rows: list[sqlite3.Row], category: str, current_warnings: list[Any] | None = None) -> set[str]:
    messages = {str(item) for item in current_warnings or [] if warning_category(str(item)) == category}
    for row in rows:
        try:
            raw = json.loads(row["raw_json"])
        except Exception:
            continue
        for item in raw.get("homebridge", {}).get("logs", {}).get("recentWarnings", []) or []:
            message = str(item)
            if warning_category(message) == category:
                messages.add(message)
    return messages


def warning_trend(rows: list[sqlite3.Row], excluded_categories: set[str] | None = None) -> dict[str, Any]:
    excluded_categories = excluded_categories or set()
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
            if category in excluded_categories:
                continue
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


def diagnosed_warning_categories(active_titles: set[str]) -> set[str]:
    categories: set[str] = set()
    if active_titles & {"UniFi occupancy authentication is failing", "UniFi occupancy API is failing"}:
        categories.add("UniFi occupancy")
    if active_titles & {"SmartHQ authentication is failing"}:
        categories.add("SmartHQ auth")
    if active_titles & {"Sense live websocket authentication is failing", "Sense live websocket auth is noisy"}:
        categories.add("Sense live websocket auth")
    if active_titles & {"TaHoma authentication is failing"}:
        categories.add("Office TaHoma")
    if active_titles & {"Alarm.com child bridge authentication is failing"}:
        categories.add("Alarm.com auth/websocket")
    return categories


def severity_rank(severity: str) -> int:
    return {"critical": 0, "warning": 1, "info": 2}.get(severity, 3)


def recommended_action(alert: dict[str, str]) -> str | None:
    title = alert.get("title", "")
    detail = alert.get("detail", "")
    if title == "Alarm.com sensor-triggered media is missing":
        return "Trip Entry Door or Sideyard Gate once, wait 1-2 minutes, then refresh Alarm.com activity/media and confirm a new clip or image event."
    if title == "Alarm.com Sideyard Gate media validation failed":
        if "Video Device - Not Responding" in detail:
            return "Power-cycle Sideyard and Backyard cameras, verify they clear Alarm.com camera trouble, then run one fresh Sideyard Gate close/open media test."
        if "current portal state: `Open`" in detail:
            return "Close the Sideyard Gate and wait until Alarm.com shows Closed, then open it once to create a fresh open edge for the recording rule."
        return "Open the Sideyard Gate once, wait 1-2 minutes, then refresh Alarm.com activity/media and confirm a new Sideyard or Backyard clip event."
    if title == "Alarm.com video recording rules are missing":
        return "Recreate the missing Alarm.com Recording Rules for Entry Door, Sideyard Gate, and related cameras; then run a post-rule door or gate trip test."
    if title == "Office TaHoma child bridge is unreachable":
        return "Check the Office TaHoma power, Wi-Fi, and IP reservation; then rerun the Office child bridge check or restart."
    if title == "Enphase Envoy local communication is degraded":
        return "Check the Envoy gateway at 192.168.1.71, local network reachability, and Enphase child bridge logs; live power may recover before cached warning history clears."
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
    if title == "Alarm.com trouble conditions active":
        if "Video Device - Not Responding" in detail:
            return "Power-cycle the named Alarm.com camera(s), verify Wi-Fi/network connectivity, then recapture Alarm.com after the portal clears the camera trouble."
        return "Open Alarm.com Issues, resolve the listed trouble condition, then recapture Alarm.com."
    if title == "SCE interval data is stale":
        if "utilityapi_payment_required" in detail:
            return "Skip paid UtilityAPI collection; import a fresh SCE Green Button export, or wait for a no-cost UtilityAPI collection entitlement, then run Refresh SCE again."
        if "utilityapi_coverage_stale" in detail:
            return "Open SCE Data Sharing and download a current Green Button CSV/XML export, then run Refresh SCE; the downloaded file will be discovered and imported automatically."
        return "Run Refresh SCE to download already-available UtilityAPI intervals. If the data stays outdated, import a fresh SCE Green Button export; paid UtilityAPI collection should stay off unless explicitly approved."
    if title == "Sense data is stale":
        return "Fix the Sense auth/live websocket issue first, then rerun the Sense trend capture so Sense-vs-Envoy reconciliation uses fresh data."
    if title == "Sense monitor is offline":
        return "Check the Sense app for Monitor Offline. After the monitor reconnects to Sense, run Refresh Energy to restore live watts."
    if title == "Envoy data is stale":
        return "Refresh the local Envoy source and verify 192.168.1.71 is reachable before trusting live solar/load state."
    if title == "ChargePoint data is stale":
        return "Run Refresh ChargePoint, then rerun energy reconciliation so EV charging attribution catches up."
    if title == "Alarm.com data is stale":
        return "Refresh Alarm.com energy capture, then compare the updated Energy Clamp totals against Envoy and SCE."
    if title == "Energy costs data is stale":
        return "Rerun the energy cost model so import/export pricing and self-consumption values are current."
    if title.endswith("data is missing") or title.endswith("data is using fallback"):
        return "Refresh the named source, then rerun the combined energy monitor and alerts."
    if title in {"Alarm.com energy is stale", "Alarm.com energy totals disagree"}:
        return "Recapture Alarm.com energy and compare the updated Energy Clamp totals against Envoy and SCE in the combined report."
    if title == "SCE and home energy history do not overlap":
        return "Run Refresh SCE. If the histories still do not overlap, import a newer SCE Green Button export."
    if title == "Sense and Envoy readings disagree":
        return "Refresh Sense and Envoy data, then check the combined energy report to see whether the meter difference remains."
    if title == "Homebridge is not running":
        return "Restart Homebridge, then run the smart-home check again after accessories reconnect."
    if title == "Homebridge advertisements use the wrong IP":
        return "Confirm the Mac's reserved IP matches Homebridge mDNS configuration, restart Homebridge once, then verify every HAP hostname resolves to the active IP."
    if title == "Homebridge storage permissions are too open":
        return "Run the Homebridge permission hardening step and rerun the monitor to verify storage paths."
    if title == "UniFi occupancy authentication is failing":
        return "Refresh the UniFi occupancy credentials/session and verify the Homebridge UniFi plugin can load clients."
    if title == "UniFi occupancy API is failing":
        return "Check the UniFi Network application on the gateway. If the API recovers but occupancy stays stale, restart only the UniFi Occupancy child bridge."
    if title == "House load is high":
        return "Check the current large loads in Home/Envoy, then compare against Sense live load and ChargePoint charging state."
    if title == "Sense live websocket auth is noisy":
        return "Leave daily Sense trend capture alone; it is working. Restart or reauth the Homebridge Sense live meter only if live 401s keep recurring after the next Homebridge restart."
    if title == "Sense live websocket authentication is failing":
        return "Restart or reauth only the Homebridge Sense live meter; daily Sense trend capture is separate and should be checked before changing credentials."
    if title == "TaHoma authentication is failing":
        return "Open the affected TaHoma account/app once if needed, then restart only the affected TaHoma child bridge and verify shade/blind state refreshes."
    if title == "Alarm.com child bridge authentication is failing":
        return "Use Alarm.com portal capture as current truth, then refresh the Homebridge Alarm.com login/session and restart only the Alarm child bridge if cache drift remains."
    if title == "SmartHQ authentication is failing":
        return "Open SmartHQ/GE Appliances once to clear any account, terms, or MFA prompt, then restart only the SmartHQ child bridge and rerun the monitor."
    if title in {"Battery failed to recharge before peak", "Battery reserve is low before peak", "Battery backup is critically low", "Battery backup is low"}:
        return "Check Enphase battery status and operating mode, then verify solar production can recharge before peak pricing."
    if title in {"Energy projection exceeds goal", "Energy projection is high", "Energy projection is critical"}:
        return "Reduce discretionary load or shift it to solar hours, then watch the billing-period projection after the next Alarm.com refresh."
    if title in {"Energy balance mismatch is high", "Solar sources disagree"}:
        return "Check the latest complete-day Envoy, Sense, and SCE readings before treating the affected comparison as decision-grade."
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


def source_status_title(source: str, status: str) -> str:
    if status == "missing":
        return f"{source} data is missing"
    if status == "fallback":
        return f"{source} data is using fallback"
    return f"{source} data is stale"


def source_status_detail(source: str, status: str, detail: Any, age_hours: Any) -> str:
    age_part = ""
    if isinstance(age_hours, (int, float)):
        age_part = f"; age is `{age_hours:.1f}` hours"
    detail_part = f"; detail `{detail}`" if detail not in (None, "") else ""
    extra = {
        "Sense": " Sense-derived trend and reconciliation views may be stale even when Envoy and ChargePoint are fresh.",
        "Envoy": " Live solar/load state may be stale until the local Envoy source refreshes.",
        "ChargePoint": " EV charging attribution may be stale until ChargePoint refreshes.",
        "Alarm.com": " Alarm.com energy attribution may be stale until the portal capture refreshes.",
        "Energy costs": " Cost-aware energy views may use stale import/export pricing until rates refresh.",
    }.get(source, "")
    return f"{source} source status is `{status}`{detail_part}{age_part}.{extra}"


def source_freshness_alerts(combined_energy: dict[str, Any]) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    for source in combined_energy.get("sourceStatus") or []:
        name = str(source.get("source") or "").strip()
        status = str(source.get("status") or "").strip().lower()
        if not name or name == "SCE" or status not in {"stale", "missing", "fallback"}:
            continue
        alerts.append(
            {
                "severity": "warning",
                "title": source_status_title(name, status),
                "detail": source_status_detail(name, status, source.get("detail"), source.get("ageHours")),
            }
        )
    return alerts


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
            "accessoryId": item.get("accessoryId"),
        }
    return states


def comparable_alarm_state(homebridge_state: str, portal_state: str) -> bool:
    if homebridge_state == portal_state:
        return True
    equivalent = {
        ("Idle", "Closed"),
        ("Closed", "Idle"),
        ("Open", "Active"),
        ("Open", "Activated"),
        ("Active", "Open"),
        ("Activated", "Open"),
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
            "homebridgeService": hb_item.get("service"),
            "homebridgeCacheFile": hb_item.get("cacheFile"),
            "homebridgeAccessoryId": hb_item.get("accessoryId"),
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
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
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

    advertisements = hb.get("advertisements", {})
    advertisement_mismatches = advertisements.get("mismatches") or []
    if advertisements.get("status") == "mismatch" and advertisement_mismatches:
        examples = []
        for item in advertisement_mismatches[:4]:
            name = item.get("name") or item.get("hostname") or "Homebridge"
            resolved = ", ".join(item.get("resolvedIPv4") or []) or "none"
            examples.append(f"{name} -> {resolved}")
        alerts.append(
            {
                "severity": "warning",
                "title": "Homebridge advertisements use the wrong IP",
                "detail": (
                    f"Expected `{advertisements.get('configuredIPv4')}` but found "
                    f"`{len(advertisement_mismatches)}` mismatched advertisement(s): "
                    + "; ".join(examples)
                    + "."
                ),
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

    high_context = energy_high_context(config, latest)
    if high_context.get("active") is True:
        load_kw = high_context.get("liveLoadKw")
        threshold_kw = high_context.get("thresholdKw")
        reasons = "; ".join(str(item) for item in (high_context.get("reasons") or [])[:2])
        alerts.append(
            {
                "severity": "warning",
                "title": "House load is high",
                "detail": (
                    f"Live load is `{float(load_kw):.3f} kW` against dynamic threshold `{float(threshold_kw):.3f} kW`."
                    + (f" {reasons}." if reasons else "")
                ),
            }
        )

    recent_warning_items = logs.get("recentWarnings", [])
    recent_warnings = "\n".join(str(item) for item in recent_warning_items)
    if smarthq_platform_configured(latest):
        smarthq_auth = smart_hq_auth_status(config, latest, recent_warning_items)
        if smarthq_auth.get("active"):
            failure = smarthq_auth.get("lastFailure") or {}
            failure_at = failure.get("capturedAt") or "unknown"
            failure_message = str(failure.get("message") or "SmartHQ cloud login failed.")
            failure_message = re.sub(r", Submit Bugs Here:.*$", "", failure_message)
            alerts.append(
                {
                    "severity": "warning",
                    "title": "SmartHQ authentication is failing",
                    "detail": (
                        f"SmartHQ child bridge last failed cloud auth at `{failure_at}`: "
                        f"`{failure_message[-220:]}`. Cached appliance accessories may remain visible, "
                        "but SmartHQ washer/dryer/dishwasher/oven state is not fresh until a later device refresh succeeds."
                    ),
                }
            )
    active_titles = {alert.get("title", "") for alert in alerts}
    for alert in retained_integration_auth_alerts(config, latest, recent_warning_items):
        if alert.get("title") not in active_titles:
            alerts.append(alert)
            active_titles.add(alert.get("title", ""))
    sense_now = load_sense_now()
    if sense_now.get("online") is False:
        alerts.append(
            {
                "severity": "warning",
                "title": "Sense monitor is offline",
                "detail": (
                    f"Sense cloud reports the physical monitor as `{sense_now.get('connectionState') or 'OFFLINE'}` "
                    f"at `{sense_now.get('capturedAt') or 'unknown'}`. Account authentication is working, "
                    "but live watts will not resume until the monitor reconnects."
                ),
            }
        )
        active_titles.add("Sense monitor is offline")
    if has_unifi_auth_warning(recent_warning_items):
        alerts.append(
            {
                "severity": "warning",
                "title": "UniFi occupancy authentication is failing",
                "detail": "Homebridge UniFi occupancy is receiving `401 Unauthorized` while loading clients.",
            }
        )
    elif has_unifi_api_warning(recent_warning_items):
        alerts.append(
            {
                "severity": "warning",
                "title": "UniFi occupancy API is failing",
                "detail": "Homebridge UniFi occupancy is receiving gateway/timeout errors from the UniFi Network API.",
            }
        )

    display_unifi = (load_display_awake_status().get("unifi") or {})
    if display_unifi.get("ok") is False and not display_unifi.get("cached"):
        alerts.append(
            {
                "severity": "critical",
                "title": "UniFi display presence is unavailable",
                "detail": "Live UniFi presence is unavailable, so Arkadiy's floor cannot be confirmed from current network data.",
            }
        )

    office_endpoint = str(config["network"].get("known_tahoma_office", "192.168.0.90:8443"))
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

    if has_envoy_local_comm_warning(recent_warning_items):
        envoy_warning_count = count_envoy_local_comm_warnings(recent_warning_items)
        alerts.append(
            {
                "severity": "warning",
                "title": "Enphase Envoy local communication is degraded",
                "detail": (
                    f"Homebridge saw `{envoy_warning_count}` Envoy local update failures in the sampled log window; "
                    "timeouts/refusals to `192.168.1.71:443` point at Envoy or local-network reachability, not energy math."
                ),
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
        trouble = alarm_com.get("troubleConditions") or {}
        trouble_rows = trouble.get("rows") or []
        if trouble.get("ok") and trouble_rows:
            examples = ", ".join(
                " ".join(
                    part
                    for part in [
                        str(item.get("description") or item.get("id")),
                        f"({item.get('emberDeviceId') or item.get('deviceId') or 'n/a'})",
                        f"mac={item.get('macAddress')}" if item.get("macAddress") else "",
                    ]
                    if part
                )
                for item in trouble_rows[:4]
            )
            alerts.append(
                {
                    "severity": "warning",
                    "title": "Alarm.com trouble conditions active",
                    "detail": f"`{len(trouble_rows)}` Alarm.com trouble conditions: {examples}.",
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
        gate_validation = alarm_com.get("gateValidation") or {}
        if gate_validation.get("status") == "trip_seen_no_sideyard_media_seen":
            gate_device = gate_validation.get("device") or {}
            gate_state = gate_device.get("state") or "unknown"
            latest_sideyard_trip = gate_validation.get("latestSideyardTripAt") or "none"
            diagnosis = gate_validation.get("diagnosis")
            diagnosis_detail = f" {diagnosis}" if diagnosis else ""
            alerts.append(
                {
                    "severity": "warning",
                    "title": "Alarm.com Sideyard Gate media validation failed",
                    "detail": (
                        "Sideyard Gate activity validation saw a gate-open trip but no Sideyard/Backyard media event; "
                        f"latest Sideyard Gate trip: `{latest_sideyard_trip}`; "
                        f"current portal state: `{gate_state}`; "
                        f"rule: `{(gate_validation.get('videoRule') or {}).get('action') or 'missing'}`."
                        f"{diagnosis_detail}"
                    ),
                }
            )

    warning_window = rows[: int(config["alerts"]["warning_recent_window"])]
    trend = warning_trend(warning_window)
    sense_live_401_threshold = int(config["alerts"].get("sense_live_401_warning_min", 3))
    sense_live_401_distinct_count = len(
        distinct_warning_messages(warning_window, "Sense live websocket auth", recent_warning_items)
    )
    active_titles = {alert.get("title", "") for alert in alerts}
    if (
        "Sense live websocket authentication is failing" not in active_titles
        and has_sense_live_auth_warning(recent_warning_items)
        and sense_live_401_distinct_count >= sense_live_401_threshold
    ):
        alerts.append(
            {
                "severity": "warning",
                "title": "Sense live websocket auth is noisy",
                "detail": (
                    f"`{sense_live_401_distinct_count}` distinct recent Sense live-websocket auth warnings; "
                    "daily Sense trend capture is tracked separately and may still be healthy."
                ),
            }
        )

    current_warning_count = int(latest.get("homebridge", {}).get("logs", {}).get("warningCount", 0))
    warning_total = sum(int(row["warning_count"]) for row in warning_window)
    if current_warning_count > 0 and warning_total >= int(config["alerts"]["warning_high_count"]):
        active_titles = {alert.get("title", "") for alert in alerts}
        dedicated_categories = {
            "Enphase Envoy local communication",
            "Enphase Envoy invalid characteristic",
            "HomeKit compatibility",
            "Office TaHoma",
            "Sense live websocket auth",
            "SmartHQ auth",
            "SmartHQ remaining duration",
        }
        dedicated_categories.update(diagnosed_warning_categories(active_titles))
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

    sce_api_status = load_sce_api_status()
    combined_energy = load_combined_energy()
    active_titles = {alert.get("title", "") for alert in alerts}
    for item in source_freshness_alerts(combined_energy):
        if item.get("title") not in active_titles:
            alerts.append(item)
            active_titles.add(item.get("title", ""))
    for item in combined_energy.get("alerts", []):
        title = item.get("title")
        detail = item.get("detail")
        severity = item.get("severity", "warning")
        if title and detail:
            if title == "SCE interval data is stale" and sce_api_status.get("status") in {
                "utilityapi_payment_required",
                "utilityapi_coverage_stale",
            }:
                detail = f"{detail} UtilityAPI refresh status: `{sce_api_status.get('status')}`."
            alerts.append({"severity": severity, "title": title, "detail": detail})
    for item in load_energy_observability().get("alerts") or []:
        title = item.get("title")
        detail = item.get("detail")
        if title and detail and title not in active_titles:
            alerts.append(
                {
                    "category": "energy",
                    "severity": item.get("severity", "warning"),
                    "title": title,
                    "detail": detail,
                }
            )
            active_titles.add(title)

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
    live_energy_state_titles: set[str] = set()

    production_kw = metrics.get("enphase_production_kw")
    net_kw = metrics.get("enphase_consumption_net_kw")
    total_kw = metrics.get("enphase_consumption_total_kw")

    if not any(isinstance(value, (int, float)) for value in (production_kw, net_kw, total_kw)) and not homebridge_restart_grace_active(config, latest):
        states.add("Energy data stale")

    if isinstance(net_kw, (int, float)):
        if net_kw >= float(config["alerts"]["grid_import_kw"]):
            live_energy_state_titles.add("Grid importing")
        if net_kw <= float(config["alerts"]["grid_export_kw"]):
            live_energy_state_titles.add("Grid exporting")

    high_context = energy_high_context(config, latest)
    if running_from_runtime_root() and isinstance(latest.get("sourceConfig"), dict):
        write_energy_high_context(high_context)
    if high_context.get("active") is True:
        live_energy_state_titles.add("House load is high")
    elif high_context.get("active") is False and isinstance(high_context.get("liveLoadKw"), (int, float)):
        live_energy_state_titles.add("House load is normal")

    if isinstance(production_kw, (int, float)) and isinstance(total_kw, (int, float)):
        if production_kw >= total_kw + float(config["alerts"]["solar_surplus_margin_kw"]):
            live_energy_state_titles.add("Solar surplus")

    if metrics.get("enphase_battery_charging") is True:
        live_energy_state_titles.add("Battery charging")
    if metrics.get("enphase_battery_discharging") is True:
        live_energy_state_titles.add("Battery discharging")

    states.update(live_energy_state_titles)
    live_energy_titles = {
        "House load is high",
        "House load is normal",
        "Grid importing",
        "Grid exporting",
        "Solar surplus",
        "Battery charging",
        "Battery discharging",
    }
    for item in load_combined_energy().get("states", []):
        title = str(item)
        if live_energy_state_titles and title in live_energy_titles:
            continue
        states.add(title)
    for source in load_combined_energy().get("sourceStatus", []):
        if source.get("source") == "SCE" and source.get("status") == "fresh":
            states.add("SCE fresh")

    comparison = load_alarm_state_comparison()
    if comparison and int(comparison.get("staleCount") or 0) == 0:
        states.add("Alarm cache clean")

    action_status = load_action_status()
    if action_status.get("ok") is True and not action_status.get("degraded"):
        states.add("Actions online")

    return states


def write_reports(alerts: list[dict[str, str]], latest: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    alerts = enrich_alerts(alerts)
    config = load_config()
    warning_rows = recent_rows(int(config["alerts"]["warning_recent_window"]))
    active_titles = {alert.get("title") for alert in alerts}
    excluded_trend_categories = set()
    excluded_trend_categories.add("HomeKit compatibility")
    if "Sense live websocket auth is noisy" not in active_titles:
        excluded_trend_categories.add("Sense live websocket auth")
    excluded_trend_categories.update(diagnosed_warning_categories({str(title) for title in active_titles}))
    trend = warning_trend(warning_rows, excluded_categories=excluded_trend_categories)
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


def virtual_sensor_should_be_active(
    accessory: dict[str, Any],
    active_titles: set[str],
    state_titles: set[str],
    projection_stabilization: dict[str, Any] | None = None,
) -> bool:
    return (
        any(title in active_titles for title in accessory.get("alert_titles", []))
        or any(title in state_titles for title in accessory.get("state_titles", []))
    )


def update_homekit_virtual_sensors(
    config: dict[str, Any],
    alerts: list[dict[str, str]],
    projection_stabilization: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    sensor_config = config.get("homekit_virtual_sensors", {})
    if not sensor_config.get("enabled", False):
        return []
    if not running_from_runtime_root():
        return []
    webhook_url = str(sensor_config.get("webhook_url", "")).rstrip("/")
    if not webhook_url:
        return []
    active_titles = {alert["title"] for alert in alerts if alert.get("severity") != "info"}
    state_titles = active_state_titles(config, load_latest())
    updates: list[dict[str, Any]] = []
    for accessory in sensor_config.get("accessories", []):
        if accessory.get("externally_managed"):
            continue
        accessory_id = accessory["id"]
        should_be_active = virtual_sensor_should_be_active(
            accessory, active_titles, state_titles, projection_stabilization
        )
        set_query = urllib.parse.urlencode(
            {
                "id": accessory_id,
                "set": "On",
                "value": "true" if should_be_active else "false",
            }
        )
        set_url = f"{webhook_url}/?{set_query}"
        try:
            with urllib.request.urlopen(set_url, timeout=5) as response:
                body = response.read().decode("utf-8", errors="replace")
            readback_query = urllib.parse.urlencode({"id": accessory_id, "get": "On"})
            readback_url = f"{webhook_url}/?{readback_query}"
            with urllib.request.urlopen(readback_url, timeout=5) as response:
                readback_body = response.read().decode("utf-8", errors="replace")
            readback_value = json.loads(readback_body).get("value")
            verified = readback_value == should_be_active
            updates.append(
                {
                    "id": accessory_id,
                    "name": accessory.get("name"),
                    "active": should_be_active,
                    "ok": verified,
                    "response": body,
                    "readback": readback_value,
                    "verified": verified,
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


def visible_homekit_label(item: dict[str, Any]) -> str | None:
    service = item.get("service")
    accessory = item.get("accessory")
    if service in {"OccupancySensor", "MotionSensor", "ContactSensor", "Switch", "Outlet", "Lightbulb"}:
        return str(accessory) if accessory else None
    return str(service) if service else str(accessory) if accessory else None


def disabled_enphase_service_names(homebridge_config: dict[str, Any]) -> set[str]:
    disabled: set[str] = set()
    for platform in homebridge_config.get("platforms", []):
        if not isinstance(platform, dict) or platform.get("platform") != "enphaseEnvoy":
            continue
        for device in platform.get("devices", []):
            if not isinstance(device, dict):
                continue
            for value in device.values():
                if isinstance(value, list):
                    for entry in value:
                        if isinstance(entry, dict) and entry.get("displayType") == 0 and entry.get("name"):
                            disabled.add(str(entry["name"]))
                elif isinstance(value, dict) and value.get("displayType") == 0 and value.get("name"):
                    disabled.add(str(value["name"]))
    return disabled


def cached_enphase_service_names() -> set[str]:
    names: set[str] = set()
    for path in sorted((HOMEBRIDGE_DIR / "accessories").glob("cachedAccessories*")):
        data = load_json_file(path)
        if not isinstance(data, list):
            continue
        for accessory in data:
            if not isinstance(accessory, dict) or accessory.get("displayName") != "Envoy":
                continue
            for service in accessory.get("services", []):
                if isinstance(service, dict) and service.get("displayName"):
                    names.add(str(service["displayName"]))
    return names


def cached_homebridge_dummy_accessories() -> dict[str, str]:
    accessories: dict[str, str] = {}
    for path in sorted((HOMEBRIDGE_DIR / "accessories").glob("cachedAccessories*")):
        data = load_json_file(path)
        if not isinstance(data, list):
            continue
        for accessory in data:
            if not isinstance(accessory, dict):
                continue
            if accessory.get("platform") == "HomebridgeDummy" or accessory.get("plugin") == "homebridge-dummy":
                identifier = (accessory.get("context") or {}).get("identifier")
                if identifier and accessory.get("displayName"):
                    accessories[str(identifier)] = str(accessory["displayName"])
    return accessories


def configured_homebridge_dummy_accessories(homebridge_config: dict[str, Any]) -> dict[str, str]:
    for platform in homebridge_config.get("platforms", []):
        if isinstance(platform, dict) and platform.get("platform") == "HomebridgeDummy":
            return {
                str(item["id"]): str(item["name"])
                for item in platform.get("accessories", [])
                if isinstance(item, dict) and item.get("id") and item.get("name")
            }
    return {}


def homebridge_dummy_switch_cache(characteristics: dict[str, Any]) -> dict[str, Any]:
    states: dict[str, Any] = {}
    for item in characteristics.values():
        if not isinstance(item, dict):
            continue
        if (
            item.get("platform") == "HomebridgeDummy"
            and item.get("service") == "Switch"
            and item.get("characteristic") == "On"
            and item.get("accessory")
        ):
            states[str(item["accessory"])] = item.get("value")
    return states


def unifi_multi_active_clients(characteristics: dict[str, Any]) -> dict[str, list[str]]:
    prefixes = ("1588EThompson", "Express", "Extender", "Level 1", "Level 2")
    active_by_client: dict[str, list[str]] = {}
    for item in characteristics.values():
        if not isinstance(item, dict):
            continue
        if item.get("platform") != "UnifiOccupancy" or item.get("characteristic") != "OccupancyDetected":
            continue
        if int(item.get("value") or 0) != 1:
            continue
        name = str(item.get("accessory") or "")
        client = name
        for prefix in prefixes:
            marker = f"{prefix} "
            if client.startswith(marker):
                client = client[len(marker):]
                break
        active_by_client.setdefault(client, []).append(name)
    return {
        client: sorted(names)
        for client, names in sorted(active_by_client.items())
        if len(names) > 1
    }


def audit_homekit_surface(updates: list[dict[str, Any]]) -> dict[str, Any]:
    characteristics = load_latest_characteristics()
    homebridge_config = load_json_file(HOMEBRIDGE_CONFIG_PATH)
    homebridge_config = homebridge_config if isinstance(homebridge_config, dict) else {}

    duplicate_visible_labels: dict[str, list[dict[str, Any]]] = {}
    labels: dict[str, list[dict[str, Any]]] = {}
    visible_characteristics = {"OccupancyDetected", "MotionDetected", "ContactSensorState", "On"}
    for item in characteristics.values():
        if not isinstance(item, dict) or item.get("characteristic") not in visible_characteristics:
            continue
        label = visible_homekit_label(item)
        if not label:
            continue
        labels.setdefault(label, []).append(
            {
                "platform": item.get("platform"),
                "accessory": item.get("accessory"),
                "service": item.get("service"),
                "characteristic": item.get("characteristic"),
                "value": item.get("value"),
            }
        )

    for label, entries in labels.items():
        surfaces = {
            (entry.get("platform"), entry.get("accessory"), entry.get("service"))
            for entry in entries
        }
        platforms = {entry.get("platform") for entry in entries}
        if len(surfaces) > 1 and len(platforms) > 1:
            duplicate_visible_labels[label] = entries

    disabled = disabled_enphase_service_names(homebridge_config)
    cached_disabled = sorted(disabled.intersection(cached_enphase_service_names()))
    configured_dummy = configured_homebridge_dummy_accessories(homebridge_config)
    cached_dummy = cached_homebridge_dummy_accessories()
    missing_dummy_ids = configured_dummy.keys() - cached_dummy.keys()
    stale_dummy_ids = cached_dummy.keys() - configured_dummy.keys()
    dummy_cache_drift = {
        "missing": sorted(configured_dummy[item_id] for item_id in missing_dummy_ids),
        "stale": sorted(cached_dummy[item_id] for item_id in stale_dummy_ids),
    }
    switch_cache = homebridge_dummy_switch_cache(characteristics)
    virtual_cache_mismatches: list[dict[str, Any]] = []
    virtual_cache_pending: list[dict[str, Any]] = []
    for update in updates:
        name = str(update.get("name"))
        if "readback" not in update or name not in switch_cache:
            continue
        cache_value = switch_cache[name]
        readback_value = update.get("readback")
        if cache_value == readback_value:
            continue
        item = {
            "name": update.get("name"),
            "active": update.get("active"),
            "readback": readback_value,
            "cache": cache_value,
        }
        if readback_value == update.get("active"):
            virtual_cache_pending.append(item)
        else:
            virtual_cache_mismatches.append(item)
    webhook_mismatches = [
        {
            "name": update.get("name"),
            "active": update.get("active"),
            "readback": update.get("readback"),
            "error": update.get("error"),
        }
        for update in updates
        if not update.get("ok")
    ]

    return {
        "duplicateVisibleLabels": duplicate_visible_labels,
        "cachedDisabledEnphaseServices": cached_disabled,
        "homebridgeDummyCacheDrift": dummy_cache_drift,
        "unifiMultiActiveClients": unifi_multi_active_clients(characteristics),
        "virtualCacheMismatches": virtual_cache_mismatches,
        "virtualCachePendingRefresh": virtual_cache_pending,
        "webhookMismatches": webhook_mismatches,
    }


def write_homekit_report(
    updates: list[dict[str, Any]],
    projection_stabilization: dict[str, Any] | None = None,
    projection_delivery: dict[str, Any] | None = None,
    energy_ok_announcement: dict[str, Any] | None = None,
    bubbler_announcement: dict[str, Any] | None = None,
) -> None:
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
        "projectionAlertStabilization": projection_stabilization or {},
        "projectionAlertDelivery": projection_delivery or {},
        "energyOkOffAnnouncement": energy_ok_announcement or {},
        "bubblerOnAnnouncement": bubbler_announcement or {},
        "updates": updates,
        "surfaceAudit": audit_homekit_surface(updates),
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
            status = "ok" if update.get("ok") else "mismatch" if update.get("verified") is False else "failed"
            lines.append(
                f"- `{status}` `{update.get('name')}` active=`{update.get('active')}`"
                + (f" readback=`{update.get('readback')}`" if "readback" in update else "")
                + (f" error=`{update.get('error')}`" if update.get("error") else "")
            )
    audit = payload["surfaceAudit"]
    lines.extend(["", "## Surface Audit", ""])
    if not updates:
        lines.append("- `info` Virtual tile webhook readback was skipped outside the runtime root.")
    elif not audit["webhookMismatches"]:
        lines.append("- `ok` Virtual tile webhook readback matches requested state.")
    else:
        for item in audit["webhookMismatches"]:
            lines.append(
                f"- `mismatch` `{item.get('name')}` active=`{item.get('active')}` "
                f"readback=`{item.get('readback')}`"
                + (f" error=`{item.get('error')}`" if item.get("error") else "")
            )
    if not audit["cachedDisabledEnphaseServices"]:
        lines.append("- `ok` Disabled Enphase services are absent from the Homebridge cache.")
    else:
        lines.append(
            "- `warning` Disabled Enphase services still cached: "
            + ", ".join(f"`{name}`" for name in audit["cachedDisabledEnphaseServices"])
        )
    dummy_drift = audit["homebridgeDummyCacheDrift"]
    if not dummy_drift["missing"] and not dummy_drift["stale"]:
        lines.append("- `ok` HomebridgeDummy configured accessories match the child-bridge cache.")
    else:
        if dummy_drift["missing"]:
            lines.append(
                "- `warning` HomebridgeDummy configured accessories missing from cache: "
                + ", ".join(f"`{name}`" for name in dummy_drift["missing"])
            )
        if dummy_drift["stale"]:
            lines.append(
                "- `warning` HomebridgeDummy stale cached accessories: "
                + ", ".join(f"`{name}`" for name in dummy_drift["stale"])
            )
    if not audit["virtualCacheMismatches"] and not audit["virtualCachePendingRefresh"]:
        lines.append("- `ok` HomebridgeDummy switch cache matches virtual tile readback.")
    elif not audit["virtualCacheMismatches"]:
        lines.append("- `ok` HomebridgeDummy switch cache has no stale mismatches.")
    else:
        for item in audit["virtualCacheMismatches"]:
            lines.append(
                f"- `warning` `{item.get('name')}` cache=`{item.get('cache')}` readback=`{item.get('readback')}`"
            )
    if audit["virtualCachePendingRefresh"]:
        names = ", ".join(f"`{item.get('name')}`" for item in audit["virtualCachePendingRefresh"])
        lines.append(f"- `info` HomebridgeDummy cache is awaiting the next snapshot for: {names}.")
    if not audit["duplicateVisibleLabels"]:
        lines.append("- `ok` No cross-platform duplicate visible labels in current HomeKit characteristics.")
    else:
        for label, entries in sorted(audit["duplicateVisibleLabels"].items()):
            surfaces = sorted(
                {
                    f"{entry.get('platform')}:{entry.get('accessory')}:{entry.get('service')}"
                    for entry in entries
                }
            )
            lines.append(f"- `warning` `{label}` appears on " + ", ".join(f"`{surface}`" for surface in surfaces))
    if not audit["unifiMultiActiveClients"]:
        lines.append("- `ok` No UniFi client is active in multiple occupancy locations.")
    else:
        for client, names in sorted(audit["unifiMultiActiveClients"].items()):
            lines.append(
                f"- `warning` UniFi client `{client}` is active in multiple locations: "
                + ", ".join(f"`{name}`" for name in names)
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
    observability = load_energy_observability()
    projection_stabilization = update_projection_stabilization(
        list(observability.get("alerts") or []),
        observability,
        int(config["alerts"].get("energy_projection_clear_samples", 3)),
    )
    projection_delivery = (
        deliver_projection_notification(projection_stabilization, observability)
        if running_from_runtime_root()
        and bool(config["alerts"].get("energy_projection_local_notifications", False))
        else {}
    )
    write_reports(alerts, latest)
    updates = update_homekit_virtual_sensors(config, alerts, projection_stabilization)
    energy_ok_announcement = deliver_energy_ok_off_announcement(config, updates)
    bubbler_announcement = deliver_bubbler_on_announcement(config)
    write_homekit_report(
        updates,
        projection_stabilization,
        projection_delivery,
        energy_ok_announcement,
        bubbler_announcement,
    )
    print(REPORT_DIR / "alerts.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
