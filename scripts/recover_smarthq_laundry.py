#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path.home() / "Library" / "Application Support" / "SmartHomeMonitor"
CONFIG_PATH = ROOT / "config" / "sources.json"
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
CAPTURE_PATH = ROOT / "scripts" / "capture_smarthq_laundry_state.js"
LATEST_PATH = DATA_DIR / "latest_smarthq_laundry_state.json"
STATUS_PATH = DATA_DIR / "latest_smarthq_laundry_recovery.json"
APPLIANCES = ("washer", "dryer", "combo")


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone()


def now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def is_armed(state: dict[str, Any]) -> bool:
    return any(bool(state.get(field)) for field in ("armed", "primaryArmed", "ventingArmed"))


def assess_heartbeats(
    latest: dict[str, Any], states: dict[str, dict[str, Any]], now: datetime, stale_minutes: float
) -> dict[str, Any]:
    devices = latest.get("devices") if isinstance(latest.get("devices"), dict) else {}
    monitored: list[str] = []
    stale: list[str] = []
    heartbeats: dict[str, Any] = {}
    for appliance in APPLIANCES:
        device = devices.get(appliance) if isinstance(devices.get(appliance), dict) else {}
        active = bool(device.get("inUse") or device.get("cycleActive"))
        if not active and not is_armed(states.get(appliance, {})):
            continue
        monitored.append(appliance)
        heartbeat = parse_time(device.get("apiLastSuccessAt"))
        age = now - heartbeat if heartbeat else None
        fresh = bool(age is not None and timedelta(0) <= age <= timedelta(minutes=stale_minutes))
        heartbeats[appliance] = {
            "lastSuccessAt": heartbeat.isoformat(timespec="seconds") if heartbeat else None,
            "ageSeconds": round(age.total_seconds(), 1) if age is not None else None,
            "fresh": fresh,
        }
        if latest.get("ok") is not True or not fresh:
            stale.append(appliance)
    return {
        "monitoredAppliances": monitored,
        "staleAppliances": stale,
        "heartbeats": heartbeats,
        "stale": bool(stale),
    }


def consecutive_stale_checks(
    previous: dict[str, Any], stale: bool, now: datetime, maximum_gap_minutes: float
) -> int:
    if not stale:
        return 0
    previous_check = parse_time(previous.get("checkedAt"))
    close_enough = bool(
        previous_check
        and timedelta(0) <= now - previous_check <= timedelta(minutes=maximum_gap_minutes)
    )
    if previous.get("stale") and close_enough:
        return int(previous.get("consecutiveStaleChecks", 0)) + 1
    return 1


def cooldown_active(previous: dict[str, Any], now: datetime, cooldown_minutes: float) -> bool:
    restarted_at = parse_time(previous.get("lastRestartAt"))
    return bool(restarted_at and timedelta(0) <= now - restarted_at < timedelta(minutes=cooldown_minutes))


def listening_pid(port: int) -> int | None:
    command = ["/usr/sbin/lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"]
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=10, check=False)
    except Exception:
        return None
    return next((int(line) for line in result.stdout.splitlines() if line.strip().isdigit()), None)


def process_command(pid: int) -> str:
    try:
        result = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "command="],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return ""
    return result.stdout.strip()


def wait_for_new_smarthq_pid(port: int, old_pid: int, timeout: int = 45) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pid = listening_pid(port)
        if pid is not None and pid != old_pid and "homebridge-smarthq" in process_command(pid):
            return {"ok": True, "pid": pid}
        time.sleep(2)
    return {"ok": False, "pid": listening_pid(port), "timedOut": True}


def restart_smarthq_child(port: int) -> dict[str, Any]:
    old_pid = listening_pid(port)
    if old_pid is None:
        return {"ok": False, "port": port, "error": "no child bridge is listening on the configured port"}
    command = process_command(old_pid)
    if "homebridge-smarthq" not in command:
        return {
            "ok": False,
            "port": port,
            "previousPid": old_pid,
            "error": "listener is not the SmartHQ child bridge",
        }
    try:
        os.kill(old_pid, signal.SIGTERM)
    except Exception as exc:
        return {"ok": False, "port": port, "previousPid": old_pid, "error": str(exc)}
    wait = wait_for_new_smarthq_pid(port, old_pid)
    return {
        "ok": bool(wait.get("ok")),
        "port": port,
        "previousPid": old_pid,
        "currentPid": wait.get("pid"),
        "waitForRestart": wait,
    }


def node_path() -> str | None:
    candidates = sorted((Path.home() / ".local").glob("node-*/bin/node"), reverse=True)
    candidates.extend(sorted((Path.home() / ".cache/codex-runtimes").glob("*/dependencies/node/bin/node")))
    return str(next((path for path in candidates if path.exists()), "")) or None


def recapture_after_restart() -> dict[str, Any]:
    node = node_path()
    if not node:
        return {"ok": False, "error": "Node.js runtime was not found"}
    try:
        result = subprocess.run(
            [node, str(CAPTURE_PATH)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout[-2000:],
        "stderr": result.stderr[-2000:],
    }


def mac_notification(title: str, message: str, sound: str) -> dict[str, Any]:
    script = (
        f"display notification {json.dumps(message)} with title {json.dumps(title)} "
        f"sound name {json.dumps(sound)}"
    )
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": result.returncode == 0, "returncode": result.returncode, "error": result.stderr.strip() or None}


def write_status(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATUS_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(STATUS_PATH)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# SmartHQ Laundry Recovery",
        "",
        f"- Checked: `{payload.get('checkedAt')}`",
        f"- Classification: `{payload.get('classification')}`",
        f"- Consecutive stale checks: `{payload.get('consecutiveStaleChecks', 0)}`",
        f"- Monitored appliances: `{', '.join(payload.get('monitoredAppliances', [])) or 'none'}`",
        f"- Stale appliances: `{', '.join(payload.get('staleAppliances', [])) or 'none'}`",
        f"- Action: `{payload.get('action', 'none')}`",
        f"- Last restart: `{payload.get('lastRestartAt')}`",
    ]
    (REPORT_DIR / "smarthq_laundry_recovery.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Restart only the SmartHQ child bridge after repeated stale laundry reads.")
    parser.add_argument("--force-outside-runtime", action="store_true")
    args = parser.parse_args()
    if ROOT.resolve() != RUNTIME_ROOT.resolve() and not args.force_outside_runtime:
        print(json.dumps({"ok": False, "error": "refusing live recovery outside the deployed runtime root"}, indent=2))
        return 1

    full_config = load_json(CONFIG_PATH)
    config = full_config.get("smarthq_laundry_recovery", {})
    now = now_local()
    previous = load_json(STATUS_PATH)
    if not isinstance(config, dict) or config.get("enabled", False) is not True:
        status = {"ok": True, "enabled": False, "checkedAt": now.isoformat(timespec="seconds"), "action": "none"}
        write_status(status)
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0

    states = {appliance: load_json(DATA_DIR / f"{appliance}_notifier_state.json") for appliance in APPLIANCES}
    assessment = assess_heartbeats(
        load_json(LATEST_PATH), states, now, float(config.get("heartbeat_stale_minutes", 5))
    )
    count = consecutive_stale_checks(
        previous, bool(assessment["stale"]), now, float(config.get("maximum_check_gap_minutes", 12))
    )
    status: dict[str, Any] = {
        "ok": True,
        "enabled": True,
        "checkedAt": now.isoformat(timespec="seconds"),
        "action": "none",
        "classification": "healthy",
        "consecutiveStaleChecks": count,
        "awaitingRecovery": bool(previous.get("awaitingRecovery", False)),
        "lastRestartAt": previous.get("lastRestartAt"),
        **assessment,
    }
    sound = str(config.get("mac_sound", "Glass"))

    if not assessment["stale"]:
        status["consecutiveStaleChecks"] = 0
        status["awaitingRecovery"] = False
        if previous.get("awaitingRecovery"):
            status["classification"] = "recovered"
            status["notification"] = mac_notification(
                "SmartHQ Laundry Recovered",
                "Fresh SmartHQ data is back. Laundry finish alerts are active again.",
                sound,
            )
        write_status(status)
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0

    threshold = max(2, int(config.get("consecutive_stale_checks", 2)))
    if count < threshold:
        status["classification"] = "stale_observing"
        write_status(status)
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0

    if cooldown_active(previous, now, float(config.get("cooldown_minutes", 20))):
        status["classification"] = "stale_cooldown"
        write_status(status)
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0

    restart = restart_smarthq_child(int(config.get("child_bridge_port", 40893)))
    status.update(
        {
            "action": "restart_child_bridge",
            "classification": "restart_started" if restart.get("ok") else "restart_failed",
            "restart": restart,
            "awaitingRecovery": bool(restart.get("ok")),
            "consecutiveStaleChecks": 0 if restart.get("ok") else count,
        }
    )
    if restart.get("ok"):
        status["lastRestartAt"] = now.isoformat(timespec="seconds")
        status["postRestartCapture"] = recapture_after_restart()
        status["notification"] = mac_notification(
            "SmartHQ Laundry Restarted",
            "SmartHQ laundry data stayed stale twice, so its child bridge was restarted. Finish alerts remain armed.",
            sound,
        )
    else:
        status["notification"] = mac_notification(
            "SmartHQ Laundry Recovery Failed",
            "SmartHQ laundry data is stale and the targeted child-bridge restart failed.",
            sound,
        )
    write_status(status)
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
