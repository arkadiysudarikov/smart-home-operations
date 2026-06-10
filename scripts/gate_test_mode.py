#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
STATUS_PATH = DATA_DIR / "latest_alarm_gate_test.json"
REPORT_PATH = REPORT_DIR / "alarm_gate_test.md"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")


def now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone(LOCAL_TZ)


def parse_local_time(raw: Any) -> datetime | None:
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
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return parsed.astimezone(LOCAL_TZ)


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def run_step(command: str, timeout: int) -> dict[str, Any]:
    proc = subprocess.run(
        ["/bin/zsh", "-lc", command],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }


def refresh_once(timeout: int) -> dict[str, Any]:
    command = (
        'export PATH="$HOME/.local/node-v24.16.0-darwin-arm64/bin:$PATH"; '
        "./scripts/smart_home_snapshot.py && "
        "./scripts/capture_alarm_com.js && "
        "./scripts/generate_alerts.py"
    )
    return run_step(command, timeout)


def sideyard_hb_state() -> dict[str, Any]:
    characteristics = load_json(DATA_DIR / "latest_characteristics.json")
    rows = characteristics.values() if isinstance(characteristics, dict) else []
    for row in rows:
        if (
            isinstance(row, dict)
            and row.get("accessory") == "Sideyard Gate"
            and row.get("characteristic") == "ContactSensorState"
        ):
            value = row.get("value")
            return {
                "available": True,
                "rawValue": value,
                "state": "Open" if value == 1 else "Closed" if value == 0 else "Unknown",
                "cacheFile": row.get("cacheFile"),
            }
    return {"available": False, "state": "Unknown"}


def sideyard_portal_state(alarm_com: dict[str, Any]) -> dict[str, Any]:
    systems = ((alarm_com.get("alarmState") or {}).get("systems") or [])
    for system in systems:
        sensors = ((system.get("components") or {}).get("sensors") or [])
        for sensor in sensors:
            name = sensor.get("description") or sensor.get("name")
            if name == "Sideyard Gate":
                return {
                    "available": True,
                    "id": sensor.get("id"),
                    "state": sensor.get("stateText") or sensor.get("state"),
                    "remoteCommandsEnabled": sensor.get("remoteCommandsEnabled"),
                }
    return {"available": False, "state": "Unknown"}


def events_after(events: list[dict[str, Any]], started_at: datetime) -> list[dict[str, Any]]:
    fresh: list[dict[str, Any]] = []
    for event in events:
        captured_at = parse_local_time(event.get("localTime") or event.get("eventDate"))
        if captured_at and captured_at >= started_at:
            fresh.append(event)
    return fresh


def evaluate(started_at: datetime) -> dict[str, Any]:
    alarm_com = load_json(DATA_DIR / "latest_alarm_com.json")
    gate = load_json(DATA_DIR / "alarm_com_gate_validation.json")
    activity = alarm_com.get("activity") or {}
    media = activity.get("mediaTriggerHealth") or {}
    trips = events_after(gate.get("recentSideyardTrips") or [], started_at)
    media_events = events_after(gate.get("recentSideyardMedia") or [], started_at)
    hb_state = sideyard_hb_state()
    portal_state = sideyard_portal_state(alarm_com)
    homebridge_matches = (
        hb_state.get("available")
        and portal_state.get("available")
        and str(hb_state.get("state")) == str(portal_state.get("state"))
    )
    return {
        "generatedAt": now_local().isoformat(timespec="seconds"),
        "alarmGeneratedAt": alarm_com.get("generatedAt"),
        "activitySource": activity.get("source") or ("historyEvents" if activity.get("refreshOk") else "unknown"),
        "activityRefreshOk": activity.get("refreshOk"),
        "activityRefreshStatus": activity.get("refreshStatus"),
        "gateStatus": gate.get("status"),
        "hardwarePresent": gate.get("hardwarePresent"),
        "videoRule": gate.get("videoRule"),
        "homebridgeState": hb_state,
        "portalState": portal_state,
        "homebridgeMatchesPortal": bool(homebridge_matches),
        "tripsAfterStart": trips,
        "mediaAfterStart": media_events,
        "tripSeen": bool(trips),
        "mediaSeen": bool(media_events),
        "sensorTriggeredMediaEvents": media.get("sensorTriggeredMediaEvents"),
        "validationTargetTripEvents": media.get("validationTargetTripEvents"),
        "passed": bool(trips and media_events and homebridge_matches),
    }


def write_report(payload: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    result = payload.get("result") or {}
    lines = [
        "# Alarm.com Sideyard Gate Test",
        "",
        f"- Started: `{payload.get('startedAt')}`",
        f"- Finished: `{payload.get('finishedAt') or 'running'}`",
        f"- Status: `{payload.get('status')}`",
        f"- Passed: `{payload.get('passed')}`",
        f"- Attempts: `{payload.get('attempts')}`",
        f"- Activity source: `{result.get('activitySource')}`",
        f"- Activity refresh: `{result.get('activityRefreshOk')}` status=`{result.get('activityRefreshStatus')}`",
        f"- Sideyard Gate portal state: `{(result.get('portalState') or {}).get('state')}`",
        f"- Sideyard Gate Homebridge state: `{(result.get('homebridgeState') or {}).get('state')}`",
        f"- Homebridge matches portal: `{result.get('homebridgeMatchesPortal')}`",
        f"- Trip seen after start: `{result.get('tripSeen')}`",
        f"- Media seen after start: `{result.get('mediaSeen')}`",
        "",
        "## Trip Evidence",
        "",
        "| Time | Device | Event |",
        "|---|---|---|",
    ]
    trips = result.get("tripsAfterStart") or []
    if trips:
        for event in trips[:12]:
            lines.append(f"| {event.get('localTime')} | {event.get('deviceDescription')} | {event.get('description')} |")
    else:
        lines.append("| none |  |  |")
    lines.extend(["", "## Media Evidence", "", "| Time | Device | Event |", "|---|---|---|"])
    media = result.get("mediaAfterStart") or []
    if media:
        for event in media[:12]:
            lines.append(f"| {event.get('localTime')} | {event.get('deviceDescription')} | {event.get('description')} |")
    else:
        lines.append("| none |  |  |")
    REPORT_PATH.write_text("\n".join(lines) + "\n")


def run_test(timeout_seconds: int, interval_seconds: int, refresh_timeout: int) -> dict[str, Any]:
    started_at = now_local()
    deadline = time.monotonic() + timeout_seconds
    payload: dict[str, Any] = {
        "ok": None,
        "passed": False,
        "status": "running",
        "startedAt": started_at.isoformat(timespec="seconds"),
        "timeoutSeconds": timeout_seconds,
        "intervalSeconds": interval_seconds,
        "attempts": 0,
        "report": str(REPORT_PATH),
    }
    write_json(STATUS_PATH, payload)
    write_report(payload)

    last_refresh: dict[str, Any] = {}
    while True:
        payload["attempts"] = int(payload.get("attempts") or 0) + 1
        last_refresh = refresh_once(refresh_timeout)
        result = evaluate(started_at)
        payload.update(
            {
                "ok": last_refresh.get("ok"),
                "status": "passed" if result.get("passed") else "waiting",
                "passed": result.get("passed"),
                "lastRefresh": last_refresh,
                "result": result,
            }
        )
        if result.get("passed"):
            payload["finishedAt"] = now_local().isoformat(timespec="seconds")
            write_json(STATUS_PATH, payload)
            write_report(payload)
            return payload
        if time.monotonic() >= deadline:
            payload["status"] = "timeout"
            payload["ok"] = False
            payload["finishedAt"] = now_local().isoformat(timespec="seconds")
            write_json(STATUS_PATH, payload)
            write_report(payload)
            return payload
        write_json(STATUS_PATH, payload)
        write_report(payload)
        time.sleep(interval_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a passive Sideyard Gate activity/media validation window.")
    parser.add_argument("--timeout", type=int, default=600, help="Seconds to wait for gate trip/media evidence.")
    parser.add_argument("--interval", type=int, default=30, help="Seconds between refresh attempts.")
    parser.add_argument("--refresh-timeout", type=int, default=90, help="Seconds allowed for each monitor refresh.")
    args = parser.parse_args()
    payload = run_test(args.timeout, args.interval, args.refresh_timeout)
    print(REPORT_PATH)
    print(STATUS_PATH)
    return 0 if payload.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
