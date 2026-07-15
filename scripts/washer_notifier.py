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


def current_appliance_state(latest: dict[str, Any], config: dict[str, Any], now: datetime) -> dict[str, Any]:
    captured_at = parse_time(latest.get("captured_at"))
    max_age_minutes = int(config.get("max_snapshot_age_minutes", 10))
    fresh = bool(captured_at and timedelta(0) <= now - captured_at <= timedelta(minutes=max_age_minutes))
    accessory = str(config.get("accessory", "Washer"))
    cycle_service = str(config.get("cycle_service", accessory))
    door_service = str(config.get("door_service", f"{accessory} Door"))
    in_use = bool_value(find_characteristic(latest, accessory, cycle_service, "InUse"))
    if in_use is None:
        in_use = bool_value(find_characteristic(latest, accessory, "Cycle Status", "MotionDetected"))
    door_open = bool_value(find_characteristic(latest, accessory, door_service, "ContactSensorState"))
    return {
        "capturedAt": captured_at.isoformat(timespec="seconds") if captured_at else None,
        "fresh": fresh,
        "inUse": in_use,
        "doorOpen": door_open,
    }


def within_announcement_hours(now: datetime, config: dict[str, Any]) -> bool:
    start = int(config.get("announcement_start_hour", 8))
    end = int(config.get("announcement_end_hour", 21))
    if start <= end:
        return start <= now.hour < end
    return now.hour >= start or now.hour < end


def evolve_state(
    prior: dict[str, Any], current: dict[str, Any], now: datetime, config: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
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
        return state, actions

    in_use = bool(current["inUse"])
    door_open = current.get("doorOpen")
    previous_in_use = state.get("lastInUse")
    state["sourceFresh"] = True

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
                "awaitingUnload": not bool(door_open),
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


def mac_notification(message: str, title: str) -> dict[str, Any]:
    script = f'display notification {json.dumps(message)} with title {json.dumps(title)}'
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
    for action in actions:
        if dry_run:
            results.append({"action": action, "ok": True, "dryRun": True})
        elif action in {"finish_on", "finish_off", "reminder_on", "reminder_off"}:
            sensor_id = finish_id if action.startswith("finish") else reminder_id
            result = webhook_set(webhook_url, sensor_id, action.endswith("_on"))
            results.append({"action": action, **result})
        elif action == "notify_finish":
            results.append({"action": action, **mac_notification(f"The {appliance_lower} has finished.", f"{appliance_name} Finished")})
        elif action == "notify_reminder":
            minutes = int(config.get("reminder_minutes", 20))
            results.append({
                "action": action,
                **mac_notification(
                    f"The {appliance_lower} finished {minutes} minutes ago and the door is still closed.",
                    f"Unload {appliance_name}",
                ),
            })
        elif action == "announce_finish" and config.get("homepod_enabled", False):
            results.append({"action": action, **homepod_announcement(f"The {appliance_lower} has finished.", config)})
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
        f"- {appliance_name} running: `{current.get('inUse')}`",
        f"- {appliance_name} door open: `{current.get('doorOpen')}`",
        f"- Waiting to be unloaded: `{state.get('awaitingUnload', False)}`",
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
    parser.add_argument("--appliance", choices=("washer", "dryer"), default="washer")
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
    current = current_appliance_state(latest, config, now)
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
