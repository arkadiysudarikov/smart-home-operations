#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "sources.json"
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
LATEST_PATH = DATA_DIR / "latest.json"
DIRECT_SMARTHQ_PATH = DATA_DIR / "latest_smarthq_laundry_state.json"
SENSE_NOW_PATH = DATA_DIR / "sense_now_latest.json"
ENVOY_PATH = DATA_DIR / "latest_envoy_direct.json"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return parsed.astimezone(LOCAL_TZ)


def bool_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "on", "yes"}:
        return True
    if text in {"0", "false", "off", "no"}:
        return False
    return None


def find_characteristic(latest: dict[str, Any], accessory: str, service: str, characteristic: str) -> Any:
    values = latest.get("homeEvents", {}).get("currentCharacteristics", {})
    if not isinstance(values, dict):
        return None
    for item in values.values():
        if not isinstance(item, dict):
            continue
        if (
            item.get("accessory") == accessory
            and item.get("service") == service
            and item.get("characteristic") == characteristic
        ):
            return item.get("value")
    return None


def direct_appliance_state(
    direct_latest: dict[str, Any], config: dict[str, Any], now: datetime
) -> dict[str, Any] | None:
    captured_at = parse_time(direct_latest.get("capturedAt"))
    max_age_minutes = int(config.get("max_snapshot_age_minutes", 10))
    capture_fresh = bool(captured_at and timedelta(0) <= now - captured_at <= timedelta(minutes=max_age_minutes))
    appliance_id = str(config.get("id") or config.get("accessory", "Washer")).lower()
    device = direct_latest.get("devices", {}).get(appliance_id)
    if direct_latest.get("ok") is not True or not capture_fresh or not isinstance(device, dict):
        return None
    if device.get("inUse") is None or device.get("cycleActive") is None:
        return None
    heartbeat_at = parse_time(device.get("apiLastSuccessAt"))
    heartbeat_minutes = int(config.get("smarthq_heartbeat_stale_minutes", 5))
    heartbeat_fresh = bool(
        heartbeat_at and timedelta(0) <= now - heartbeat_at <= timedelta(minutes=heartbeat_minutes)
    )
    heartbeat_required = bool(config.get("require_smarthq_heartbeat", False))
    return {
        "capturedAt": captured_at.isoformat(timespec="seconds") if captured_at else None,
        "fresh": capture_fresh and (heartbeat_fresh or not heartbeat_required),
        "inUse": bool(device["inUse"]),
        "cycleActive": bool(device["cycleActive"]),
        "doorOpen": device.get("doorOpen"),
        "apiLastSuccessAt": heartbeat_at.isoformat(timespec="seconds") if heartbeat_at else None,
        "apiLastChangedAt": device.get("apiLastChangedAt"),
        "heartbeatFresh": heartbeat_fresh,
        "source": direct_latest.get("source", "homebridge-hap-live"),
    }


def current_appliance_state(
    latest: dict[str, Any], config: dict[str, Any], now: datetime, direct_latest: dict[str, Any] | None = None
) -> dict[str, Any]:
    direct = direct_appliance_state(direct_latest or {}, config, now)
    if direct:
        return direct
    captured_at = parse_time(latest.get("captured_at"))
    max_age_minutes = int(config.get("max_snapshot_age_minutes", 10))
    fresh = bool(captured_at and timedelta(0) <= now - captured_at <= timedelta(minutes=max_age_minutes))
    accessory = str(config.get("accessory", "Washer"))
    cycle_service = str(config.get("cycle_service", accessory))
    door_service = str(config.get("door_service", f"{accessory} Door"))
    in_use = bool_value(find_characteristic(latest, accessory, cycle_service, "InUse"))
    cycle_active = bool_value(find_characteristic(latest, accessory, "Cycle Status", "MotionDetected"))
    if in_use is None:
        in_use = cycle_active
    door_open = bool_value(find_characteristic(latest, accessory, door_service, "ContactSensorState"))
    return {
        "capturedAt": captured_at.isoformat(timespec="seconds") if captured_at else None,
        "fresh": fresh,
        "inUse": in_use,
        "cycleActive": cycle_active,
        "doorOpen": door_open,
        "source": "homebridge-cache",
    }


def within_announcement_hours(now: datetime, config: dict[str, Any]) -> bool:
    start = int(config.get("announcement_start_hour", 8))
    end = int(config.get("announcement_end_hour", 21))
    if start <= end:
        return start <= now.hour < end
    return now.hour >= start or now.hour < end


def evolve_washer_state(
    prior: dict[str, Any], current: dict[str, Any], now: datetime, config: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    state = dict(prior)
    actions: list[str] = []
    now_text = now.isoformat(timespec="seconds")
    pulse_seconds = int(config.get("pulse_seconds", 120))

    for field, action in (
        ("finishPulseUntil", "finish_off"),
        ("reminderPulseUntil", "reminder_off"),
        ("ventingPulseUntil", "venting_off"),
    ):
        deadline = parse_time(state.get(field))
        if deadline and now >= deadline:
            state[field] = None
            actions.append(action)

    if not current.get("fresh") or current.get("inUse") is None or current.get("cycleActive") is None:
        state["lastCheckedAt"] = now_text
        state["sourceFresh"] = False
        if (
            config.get("source_stale_alert_enabled", False)
            and (state.get("primaryArmed") or state.get("ventingArmed") or state.get("armed"))
            and not state.get("sourceStaleAlertSent")
        ):
            state["sourceStaleAlertSent"] = True
            state["sourceStaleAlertedAt"] = now_text
            actions.append("notify_source_stale")
        return state, list(dict.fromkeys(actions))

    in_use = bool(current["inUse"])
    cycle_active = bool(current["cycleActive"])
    door_open = current.get("doorOpen")
    previous_in_use = state.get("lastInUse")
    previous_cycle_active = state.get("lastCycleActive")
    state["sourceFresh"] = True
    state["sourceStaleAlertSent"] = False

    if previous_cycle_active is None:
        previous_in_use = state.get("lastInUse")
        legacy_armed = bool(state.get("armed"))
        state["lastCycleActive"] = cycle_active
        state["lastInUse"] = in_use
        state["lastDoorOpen"] = door_open
        state["lastCheckedAt"] = now_text
        state["primaryArmed"] = bool(cycle_active and legacy_armed)
        state["ventingArmed"] = bool(in_use and not cycle_active and legacy_armed)
        if state["ventingArmed"]:
            state["ventingStartedAt"] = state.get("ventingStartedAt") or state.get("cycleStartedAt") or now_text
            state["ventingStaleAlertSent"] = bool(state.get("ventingStaleAlertSent", False))
        return state, actions

    if cycle_active:
        if not previous_cycle_active:
            state.update({
                "primaryArmed": True,
                "ventingArmed": False,
                "ventingStartedAt": None,
                "ventingStaleAlertSent": False,
                "ventingStaleAlertedAt": None,
                "washStartedAt": now_text,
                "runningSamples": 1,
                "awaitingUnload": False,
                "washFinishedAt": None,
                "reminderSent": False,
            })
            actions.extend(["finish_off", "reminder_off", "venting_off"])
        else:
            state["runningSamples"] = int(state.get("runningSamples", 0)) + 1
            state["primaryArmed"] = True
    elif previous_cycle_active:
        started_at = parse_time(state.get("washStartedAt") or state.get("cycleStartedAt"))
        duration_ok = bool(started_at and now - started_at >= timedelta(minutes=int(config.get("minimum_cycle_minutes", 10))))
        samples_ok = int(state.get("runningSamples", 0)) >= int(config.get("minimum_running_samples", 2))
        if state.get("primaryArmed") and (duration_ok or samples_ok):
            state.update({
                "primaryArmed": False,
                "ventingArmed": in_use,
                "ventingStartedAt": now_text if in_use else None,
                "ventingStaleAlertSent": False,
                "ventingStaleAlertedAt": None,
                "awaitingUnload": door_open is False,
                "washFinishedAt": now_text,
                "reminderSent": False,
                "finishPulseUntil": (now + timedelta(seconds=pulse_seconds)).isoformat(timespec="seconds"),
                "completedCycles": int(state.get("completedCycles", 0)) + 1,
            })
            actions.extend(["finish_on", "notify_finish"])
            if within_announcement_hours(now, config):
                actions.append("announce_finish")

    if previous_in_use and not in_use and state.get("ventingArmed"):
        state["ventingArmed"] = False
        state["ventingFinishedAt"] = now_text
        state["ventingPulseUntil"] = (now + timedelta(seconds=pulse_seconds)).isoformat(timespec="seconds")
        actions.extend(["venting_on", "notify_venting"])
        if within_announcement_hours(now, config):
            actions.append("announce_venting")

    if state.get("ventingArmed") and in_use and not cycle_active:
        venting_started_at = parse_time(
            state.get("ventingStartedAt") or state.get("washFinishedAt") or state.get("cycleStartedAt")
        )
        if not venting_started_at:
            venting_started_at = now
            state["ventingStartedAt"] = now_text
        maximum_venting_hours = int(config.get("maximum_venting_hours", 8))
        if (
            maximum_venting_hours > 0
            and not state.get("ventingStaleAlertSent")
            and now >= venting_started_at + timedelta(hours=maximum_venting_hours)
        ):
            state["ventingStaleAlertSent"] = True
            state["ventingStaleAlertedAt"] = now_text
            actions.append("notify_venting_stale")
            if within_announcement_hours(now, config):
                actions.append("announce_venting_stale")

    finished_at = parse_time(state.get("washFinishedAt"))
    if state.get("awaitingUnload") and door_open:
        state.update({"awaitingUnload": False, "unloadedAt": now_text})
        actions.extend(["finish_off", "reminder_off"])
    elif (
        state.get("awaitingUnload")
        and door_open is False
        and not state.get("reminderSent")
        and finished_at
        and now >= finished_at + timedelta(minutes=int(config.get("reminder_minutes", 20)))
    ):
        state["reminderSent"] = True
        state["reminderPulseUntil"] = (now + timedelta(seconds=pulse_seconds)).isoformat(timespec="seconds")
        actions.extend(["reminder_on", "notify_reminder"])

    state.update({
        "lastCycleActive": cycle_active,
        "lastInUse": in_use,
        "lastDoorOpen": door_open,
        "lastCheckedAt": now_text,
    })
    return state, list(dict.fromkeys(actions))


def evolve_state(
    prior: dict[str, Any], current: dict[str, Any], now: datetime, config: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    if config.get("finish_signal") == "cycleActive":
        return evolve_washer_state(prior, current, now, config)

    state = dict(prior)
    actions: list[str] = []
    now_text = now.isoformat(timespec="seconds")
    pulse_seconds = int(config.get("pulse_seconds", 120))

    for field, action in (("finishPulseUntil", "finish_off"), ("reminderPulseUntil", "reminder_off")):
        deadline = parse_time(state.get(field))
        if deadline and now >= deadline:
            state[field] = None
            actions.append(action)

    if not current.get("fresh") or current.get("inUse") is None:
        state["lastCheckedAt"] = now_text
        state["sourceFresh"] = False
        if (
            config.get("source_stale_alert_enabled", False)
            and state.get("armed")
            and not state.get("sourceStaleAlertSent")
        ):
            state["sourceStaleAlertSent"] = True
            state["sourceStaleAlertedAt"] = now_text
            actions.append("notify_source_stale")
        return state, list(dict.fromkeys(actions))

    in_use = bool(current["inUse"])
    door_open = current.get("doorOpen")
    previous_in_use = state.get("lastInUse")
    state["sourceFresh"] = True
    state["sourceStaleAlertSent"] = False

    if previous_in_use is None:
        state["lastInUse"] = in_use
        state["lastDoorOpen"] = door_open
        state["lastCheckedAt"] = now_text
        if in_use:
            state.update({"armed": True, "cycleStartedAt": now_text, "runningSamples": 1})
        return state, actions

    if in_use:
        if not previous_in_use:
            state.update({
                "armed": True,
                "cycleStartedAt": now_text,
                "runningSamples": 1,
                "awaitingUnload": False,
                "finishedAt": None,
                "reminderSent": False,
            })
            actions.extend(["finish_off", "reminder_off"])
        else:
            state["runningSamples"] = int(state.get("runningSamples", 0)) + 1
            state["armed"] = True
    elif previous_in_use:
        started_at = parse_time(state.get("cycleStartedAt"))
        duration_ok = bool(started_at and now - started_at >= timedelta(minutes=int(config.get("minimum_cycle_minutes", 10))))
        samples_ok = int(state.get("runningSamples", 0)) >= int(config.get("minimum_running_samples", 2))
        if state.get("armed") and (duration_ok or samples_ok):
            state.update({
                "armed": False,
                "awaitingUnload": door_open is False,
                "finishedAt": now_text,
                "reminderSent": False,
                "finishPulseUntil": (now + timedelta(seconds=pulse_seconds)).isoformat(timespec="seconds"),
                "completedCycles": int(state.get("completedCycles", 0)) + 1,
            })
            actions.extend(["finish_on", "notify_finish"])
            if within_announcement_hours(now, config):
                actions.append("announce_finish")

    finished_at = parse_time(state.get("finishedAt"))
    if state.get("awaitingUnload") and door_open:
        state.update({"awaitingUnload": False, "unloadedAt": now_text})
        actions.extend(["finish_off", "reminder_off"])
    elif (
        state.get("awaitingUnload")
        and door_open is False
        and not state.get("reminderSent")
        and finished_at
        and now >= finished_at + timedelta(minutes=int(config.get("reminder_minutes", 20)))
    ):
        state["reminderSent"] = True
        state["reminderPulseUntil"] = (now + timedelta(seconds=pulse_seconds)).isoformat(timespec="seconds")
        actions.extend(["reminder_on", "notify_reminder"])

    state.update({"lastInUse": in_use, "lastDoorOpen": door_open, "lastCheckedAt": now_text})
    return state, list(dict.fromkeys(actions))


def webhook_set(webhook_url: str, accessory_id: str, active: bool) -> dict[str, Any]:
    base = webhook_url.rstrip("/") + "/"
    query = urllib.parse.urlencode({"id": accessory_id, "set": "On", "value": str(active).lower()})
    try:
        with urllib.request.urlopen(f"{base}?{query}", timeout=5) as response:
            response.read()
        read_query = urllib.parse.urlencode({"id": accessory_id, "get": "On"})
        with urllib.request.urlopen(f"{base}?{read_query}", timeout=5) as response:
            readback = json.loads(response.read().decode()).get("value")
        return {"ok": readback == active, "active": active, "readback": readback}
    except Exception as exc:
        return {"ok": False, "active": active, "error": str(exc)}


def mac_notification(message: str, title: str, sound_name: str | None = None) -> dict[str, Any]:
    script = f'display notification {json.dumps(message)} with title {json.dumps(title)}'
    if sound_name:
        script += f' sound name {json.dumps(sound_name)}'
    proc = subprocess.run(["osascript", "-e", script], text=True, capture_output=True, timeout=10, check=False)
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "error": proc.stderr.strip() or None}


def homepod_announcement(message: str, config: dict[str, Any]) -> dict[str, Any]:
    targets = [str(item) for item in config.get("homepod_targets", []) if str(item).strip()]
    if not targets:
        return {"ok": False, "skipped": True, "error": "no HomePod targets configured"}
    appliance_id = str(config.get("id", "washer"))
    audio_path = DATA_DIR / f"{appliance_id}_finished.aiff"
    speech = subprocess.run(
        ["say", "-o", str(audio_path), message],
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    if speech.returncode != 0:
        return {"ok": False, "returncode": speech.returncode, "error": speech.stderr.strip() or "say failed"}

    target_list = "{" + ", ".join(json.dumps(name) for name in targets) + "}"
    volume = max(0, min(100, int(config.get("homepod_volume", 45))))
    delay_seconds = max(2, min(30, int(config.get("homepod_clip_seconds", 5))))
    script = f'''tell application "Music"
set originalDevices to current AirPlay devices
set targetNames to {target_list}
set targetDevices to {{}}
repeat with deviceItem in every AirPlay device
    if (name of deviceItem is in targetNames) and (available of deviceItem) then set end of targetDevices to deviceItem
end repeat
if (count of targetDevices) is 0 then error "No configured HomePod is available"
try
    set current AirPlay devices to targetDevices
    delay 1
    repeat with deviceItem in targetDevices
        set sound volume of deviceItem to {volume}
    end repeat
    play POSIX file {json.dumps(str(audio_path))} once true
    delay {delay_seconds}
    stop
    set current AirPlay devices to originalDevices
on error errorMessage number errorNumber
    try
        stop
        set current AirPlay devices to originalDevices
    end try
    error errorMessage number errorNumber
end try
end tell'''
    proc = subprocess.run(["osascript", "-e", script], text=True, capture_output=True, timeout=45, check=False)
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "targets": targets,
        "error": proc.stderr.strip() or None,
    }


def power_observation(current: dict[str, Any], now: datetime, appliance_id: str) -> dict[str, Any]:
    sense = load_json(SENSE_NOW_PATH, {})
    envoy = load_json(ENVOY_PATH, {})
    devices = sense.get("devices", []) if isinstance(sense, dict) else []
    other = next((item.get("watts") for item in devices if item.get("name") == "Other"), None)
    consumption = ((envoy.get("probes") or [{}])[0].get("production") or {}).get("consumption", [])
    total = next((item.get("wNow") for item in consumption if item.get("measurementType") == "total-consumption"), None)
    return {
        "capturedAt": now.isoformat(timespec="seconds"),
        "appliance": appliance_id,
        "sourceSnapshotAt": current.get("capturedAt"),
        "inUse": current.get("inUse"),
        "doorOpen": current.get("doorOpen"),
        "senseTotalWatts": sense.get("watts") if isinstance(sense, dict) else None,
        "senseOtherWatts": other,
        "envoyTotalConsumptionWatts": total,
        "mode": "shadow",
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def execute_actions(actions: list[str], config: dict[str, Any], dry_run: bool) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    webhook_url = str(config.get("webhook_url", "http://127.0.0.1:63743"))
    appliance_name = str(config.get("display_name", config.get("accessory", "Washer")))
    appliance_lower = appliance_name.lower()
    finish_id = str(config["finish_sensor_id"])
    reminder_id = str(config["reminder_sensor_id"])
    venting_id = str(config.get("venting_sensor_id", ""))
    finish_message = str(config.get("finish_message", f"The {appliance_lower} has finished."))
    finish_title = str(config.get("finish_title", f"{appliance_name} Finished"))
    announcement_message = str(config.get("announcement_message", finish_message))
    for action in actions:
        if dry_run:
            results.append({"action": action, "ok": True, "dryRun": True})
        elif action in {"finish_on", "finish_off", "reminder_on", "reminder_off", "venting_on", "venting_off"}:
            if action.startswith("finish"):
                sensor_id = finish_id
            elif action.startswith("reminder"):
                sensor_id = reminder_id
            else:
                sensor_id = venting_id
            result = webhook_set(webhook_url, sensor_id, action.endswith("_on"))
            results.append({"action": action, **result})
        elif action == "notify_finish":
            results.append({
                "action": action,
                **mac_notification(finish_message, finish_title, str(config.get("mac_sound", "Glass"))),
            })
        elif action == "notify_reminder":
            minutes = int(config.get("reminder_minutes", 20))
            results.append({
                "action": action,
                **mac_notification(
                    f"The {appliance_lower} finished {minutes} minutes ago and the door is still closed.",
                    f"Unload {appliance_name}",
                    str(config.get("mac_sound", "Glass")),
                ),
            })
        elif action == "announce_finish" and config.get("homepod_enabled", False):
            results.append({"action": action, **homepod_announcement(announcement_message, config)})
        elif action == "notify_venting":
            message = str(config.get("venting_message", "Washer venting has finished. Turn off the laundry-room fan."))
            title = str(config.get("venting_title", "Venting Finished"))
            results.append({
                "action": action,
                **mac_notification(message, title, str(config.get("mac_sound", "Glass"))),
            })
        elif action == "announce_venting" and config.get("homepod_enabled", False):
            message = str(config.get("venting_announcement", "Washer venting has finished. Turn off the laundry-room fan."))
            results.append({"action": action, **homepod_announcement(message, config)})
        elif action == "notify_venting_stale":
            hours = int(config.get("maximum_venting_hours", 8))
            message = str(
                config.get(
                    "venting_stale_message",
                    f"Washer still reports venting after {hours} hours. Check the washer and turn off the laundry-room fan if appropriate.",
                )
            )
            title = str(config.get("venting_stale_title", "Check Washer Venting"))
            results.append({
                "action": action,
                **mac_notification(message, title, str(config.get("mac_sound", "Glass"))),
            })
        elif action == "announce_venting_stale" and config.get("homepod_enabled", False):
            hours = int(config.get("maximum_venting_hours", 8))
            message = str(
                config.get(
                    "venting_stale_announcement",
                    f"Washer still reports venting after {hours} hours. Check the washer and turn off the laundry-room fan if appropriate.",
                )
            )
            results.append({"action": action, **homepod_announcement(message, config)})
        elif action == "notify_source_stale":
            message = str(
                config.get(
                    "source_stale_message",
                    f"SmartHQ has not returned fresh data for the {appliance_lower}. Finish alerts are paused.",
                )
            )
            title = str(config.get("source_stale_title", "SmartHQ Laundry Data Stale"))
            results.append({
                "action": action,
                **mac_notification(message, title, str(config.get("mac_sound", "Glass"))),
            })
    return results


def write_report(payload: dict[str, Any], report_path: Path, appliance_name: str) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    current = payload["current"]
    state = payload["state"]
    lines = [
        f"# {appliance_name} Notifications",
        "",
        f"- Checked: `{payload['generatedAt']}`",
        f"- SmartHQ snapshot fresh: `{current.get('fresh')}`",
        f"- SmartHQ API heartbeat fresh: `{current.get('heartbeatFresh')}`",
        f"- Last successful SmartHQ API read: `{current.get('apiLastSuccessAt')}`",
        f"- State source: `{current.get('source', 'homebridge-cache')}`",
        f"- {appliance_name} running: `{current.get('inUse')}`",
        f"- {appliance_name} primary cycle active: `{current.get('cycleActive')}`",
        f"- {appliance_name} door open: `{current.get('doorOpen')}`",
        f"- Waiting to be unloaded: `{state.get('awaitingUnload', False)}`",
        f"- Waiting for venting completion: `{state.get('ventingArmed', False)}`",
        f"- Stale venting alert sent: `{state.get('ventingStaleAlertSent', False)}`",
        f"- Completed cycles observed: `{state.get('completedCycles', 0)}`",
        f"- Power fallback: `shadow` (collecting labeled samples; not allowed to alert yet)",
        "",
        "## Last Actions",
        "",
    ]
    if payload["results"]:
        lines.extend(f"- `{item['action']}`: `{'ok' if item.get('ok') else 'failed'}`" for item in payload["results"])
    else:
        lines.append("- No notification action was needed.")
    report_path.write_text("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Send laundry-finished and unload-reminder notifications.")
    parser.add_argument("--appliance", choices=("washer", "dryer", "combo"), default="washer")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--now", help="override the current local time for testing")
    args = parser.parse_args()

    full_config = load_json(CONFIG_PATH, {})
    appliance_id = args.appliance
    appliance_name = appliance_id.title()
    config = full_config.get(f"{appliance_id}_notifications", {})
    if not config.get("enabled", False):
        print(f"{appliance_name} notifications are disabled.")
        return 0
    config = {"id": appliance_id, "display_name": appliance_name, **config}
    state_path = DATA_DIR / f"{appliance_id}_notifier_state.json"
    status_path = DATA_DIR / f"latest_{appliance_id}_notifier.json"
    power_log_path = DATA_DIR / f"{appliance_id}_power_shadow.jsonl"
    report_path = REPORT_DIR / f"{appliance_id}_notifications.md"
    now = parse_time(args.now) if args.now else datetime.now(timezone.utc).astimezone(LOCAL_TZ)
    assert now is not None
    latest = load_json(LATEST_PATH, {})
    direct_latest = load_json(DIRECT_SMARTHQ_PATH, {})
    current = current_appliance_state(latest, config, now, direct_latest)
    prior = load_json(state_path, {})
    state, actions = evolve_state(prior, current, now, config)
    results = execute_actions(actions, config, args.dry_run)
    observation = power_observation(current, now, appliance_id)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with power_log_path.open("a") as handle:
        handle.write(json.dumps(observation, sort_keys=True) + "\n")
    payload = {
        "generatedAt": now.isoformat(timespec="seconds"),
        "appliance": appliance_id,
        "ok": all(item.get("ok") for item in results),
        "current": current,
        "state": state,
        "actions": actions,
        "results": results,
        "powerFallback": {"mode": "shadow", "observation": observation},
    }
    if not args.dry_run:
        write_json(state_path, state)
        write_json(status_path, payload)
        write_report(payload, report_path, appliance_name)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
