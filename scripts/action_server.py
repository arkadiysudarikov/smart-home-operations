#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "sources.json"
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
LOG_DIR = ROOT / "logs"
SCE_REFRESH_STATUS_PATH = DATA_DIR / "latest_sce_refresh.json"
SCE_API_STATUS_PATH = DATA_DIR / "latest_sce_api.json"
ENERGY_RECONCILE_STATUS_PATH = DATA_DIR / "latest_energy_reconcile.json"
GATE_TEST_STATUS_PATH = DATA_DIR / "latest_alarm_gate_test.json"
ALARM_CACHE_REFRESH_STATUS_PATH = DATA_DIR / "latest_alarm_cache_refresh.json"
GARAGE_LIGHT_HOLD_STATUS_PATH = DATA_DIR / "garage_light_hold.json"
SCE_REFRESH_LOCK = threading.Lock()
ENERGY_RECONCILE_LOCK = threading.Lock()
GATE_TEST_LOCK = threading.Lock()
ALARM_CACHE_REFRESH_LOCK = threading.Lock()
GARAGE_LIGHT_HOLD_LOCK = threading.Lock()
GARAGE_LIGHT_HOLD_TIMER: threading.Timer | None = None

GARAGE_LIGHT_ID = "104430779-1206"
GARAGE_LIGHT_HOLD_SECONDS = 300
GARAGE_LIGHT_CONTROLLER_BRIGHTNESS = 100
NODE_BIN = Path.home() / ".local/node-v24.16.0-darwin-arm64/bin/node"


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text())


def run(cmd: list[str], timeout: int = 45) -> dict[str, Any]:
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
        }
    except Exception as exc:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": str(exc)}


def json_run(cmd: list[str], timeout: int = 45) -> dict[str, Any]:
    result = run(cmd, timeout=timeout)
    payload: dict[str, Any] = {
        "ok": False,
        "returncode": result["returncode"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
    }
    text = result["stdout"].strip().splitlines()[-1:] or [""]
    try:
        parsed = json.loads(text[0])
    except json.JSONDecodeError:
        return payload
    if isinstance(parsed, dict):
        payload.update(parsed)
        payload["returncode"] = result["returncode"]
        payload["stderr"] = result["stderr"]
    return payload


def ps_rows() -> list[tuple[int, int, str]]:
    proc = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,command="],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    rows: list[tuple[int, int, str]] = []
    for line in proc.stdout.splitlines():
        match = re.match(r"\s*(\d+)\s+(\d+)\s+(.*)$", line)
        if match:
            rows.append((int(match.group(1)), int(match.group(2)), match.group(3).strip()))
    return rows


def launchd_pid(service: str) -> int | None:
    result = run(["launchctl", "print", service], timeout=10)
    match = re.search(r"\bpid = ([0-9]+)", result["stdout"])
    return int(match.group(1)) if match else None


def find_main_homebridge_pid() -> int | None:
    service = str(load_config()["homebridge"]["launchd_service"])
    parent_pid = launchd_pid(service)
    if parent_pid is None:
        return None
    for pid, ppid, command in ps_rows():
        if ppid == parent_pid and command == "homebridge":
            return pid
    return None


def listening_pid(port: int) -> int | None:
    result = run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], timeout=10)
    for line in result["stdout"].splitlines():
        if line.strip().isdigit():
            return int(line.strip())
    return None


def terminate(pid: int) -> dict[str, Any]:
    try:
        os.kill(pid, signal.SIGTERM)
        return {"ok": True, "pid": pid}
    except ProcessLookupError:
        return {"ok": False, "pid": pid, "error": "process is already gone"}
    except Exception as exc:
        return {"ok": False, "pid": pid, "error": str(exc)}


def monitor_command() -> list[str]:
    return [
        "/bin/zsh",
        "-lc",
        'export PATH="$HOME/.local/node-v24.16.0-darwin-arm64/bin:$PATH"; ./scripts/smart_home_snapshot.py && ./scripts/maintain_storage.py && ./scripts/analyze_patterns.py && ./scripts/analyze_energy_pairing.py && ./scripts/analyze_all_energy_readings.py && ./scripts/capture_sense_trends.js && ./scripts/fetch_chargepoint_sessions.py && ./scripts/analyze_chargepoint_pairing.py && ./scripts/analyze_meter_reconciliation.py && ./scripts/analyze_bill_home_pairing.py && ./scripts/analyze_energy_costs.py && ./scripts/analyze_combined_energy_monitor.py && ./scripts/generate_alerts.py',
    ]


def sce_refresh_command() -> list[str]:
    return [
        "/bin/zsh",
        "-lc",
        'export PATH="$HOME/.local/node-v24.16.0-darwin-arm64/bin:$PATH"; ./scripts/fetch_sce_green_button_connect.py && "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3" ./scripts/extract_sce_bills.py && ./scripts/analyze_all_energy_readings.py && ./scripts/capture_sense_trends.js && ./scripts/analyze_bill_home_pairing.py && ./scripts/analyze_meter_reconciliation.py && ./scripts/analyze_energy_costs.py && ./scripts/analyze_combined_energy_monitor.py && ./scripts/generate_alerts.py',
    ]


def energy_reconcile_command() -> list[str]:
    return [
        "/bin/zsh",
        "-lc",
        'export PATH="$HOME/.local/node-v24.16.0-darwin-arm64/bin:$PATH"; ./scripts/smart_home_snapshot.py && ./scripts/maintain_storage.py && ./scripts/analyze_patterns.py && ./scripts/analyze_energy_pairing.py && ./scripts/analyze_all_energy_readings.py && ./scripts/capture_sense_trends.js && ./scripts/fetch_chargepoint_sessions.py && ./scripts/analyze_chargepoint_pairing.py && ./scripts/analyze_meter_reconciliation.py && ./scripts/analyze_bill_home_pairing.py && ./scripts/analyze_energy_costs.py && ./scripts/analyze_combined_energy_monitor.py && ./scripts/generate_alerts.py && ./scripts/install_homekit_virtual_sensors.py',
    ]


def gate_test_command() -> list[str]:
    return [
        "/bin/zsh",
        "-lc",
        'export PATH="$HOME/.local/node-v24.16.0-darwin-arm64/bin:$PATH"; ./scripts/gate_test_mode.py --timeout 600 --interval 30',
    ]


def alarm_cache_refresh_command() -> list[str]:
    return [
        "/bin/zsh",
        "-lc",
        'export PATH="$HOME/.local/node-v24.16.0-darwin-arm64/bin:$PATH"; ./scripts/capture_alarm_com.js && ./scripts/smart_home_snapshot.py && ./scripts/generate_alerts.py',
    ]


def alarm_light_command(*args: str) -> list[str]:
    return [str(NODE_BIN), str(ROOT / "scripts" / "set_alarm_light.js"), "--light-id", GARAGE_LIGHT_ID, *args]


def run_smart_home_check() -> dict[str, Any]:
    result = run(monitor_command(), timeout=120)
    return {
        "ok": result["ok"],
        "returncode": result["returncode"],
        "report": str(REPORT_DIR / "latest.md"),
        "alerts": str(REPORT_DIR / "alerts.md"),
        "stdout": result["stdout"],
        "stderr": result["stderr"],
    }


def write_sce_refresh_status(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SCE_REFRESH_STATUS_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def run_sce_refresh_background(started_at: str) -> None:
    try:
        result = run(sce_refresh_command(), timeout=600)
        write_sce_refresh_status(
            {
                "ok": result["ok"],
                "startedAt": started_at,
                "finishedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "returncode": result["returncode"],
                "sceApi": str(SCE_API_STATUS_PATH),
                "sceBills": str(REPORT_DIR / "sce_bill_readings.md"),
                "allEnergy": str(REPORT_DIR / "all_energy_pairing.md"),
                "energyCosts": str(REPORT_DIR / "energy_costs.md"),
                "combinedEnergy": str(REPORT_DIR / "combined_energy_monitor.md"),
                "alerts": str(REPORT_DIR / "alerts.md"),
                "stdout": result["stdout"],
                "stderr": result["stderr"],
            }
        )
    finally:
        SCE_REFRESH_LOCK.release()


def write_energy_reconcile_status(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ENERGY_RECONCILE_STATUS_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def write_gate_test_status(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    GATE_TEST_STATUS_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def write_alarm_cache_refresh_status(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ALARM_CACHE_REFRESH_STATUS_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def run_energy_reconcile_background(started_at: str) -> None:
    try:
        result = run(energy_reconcile_command(), timeout=900)
        write_energy_reconcile_status(
            {
                "ok": result["ok"],
                "startedAt": started_at,
                "finishedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "returncode": result["returncode"],
                "latest": str(REPORT_DIR / "latest.md"),
                "patterns": str(REPORT_DIR / "patterns.md"),
                "allEnergy": str(REPORT_DIR / "all_energy_pairing.md"),
                "meterReconciliation": str(REPORT_DIR / "meter_reconciliation.md"),
                "billHomePairing": str(REPORT_DIR / "bill_home_pairing.md"),
                "energyCosts": str(REPORT_DIR / "energy_costs.md"),
                "chargepointPairing": str(REPORT_DIR / "chargepoint_pairing.md"),
                "chargepointRefresh": str(DATA_DIR / "latest_chargepoint_refresh.json"),
                "combinedEnergy": str(REPORT_DIR / "combined_energy_monitor.md"),
                "alerts": str(REPORT_DIR / "alerts.md"),
                "homekitVirtualSensors": str(REPORT_DIR / "homekit_virtual_sensors.md"),
                "stdout": result["stdout"],
                "stderr": result["stderr"],
            }
        )
    finally:
        ENERGY_RECONCILE_LOCK.release()


def run_gate_test_background(started_at: str) -> None:
    try:
        result = run(gate_test_command(), timeout=900)
        existing: dict[str, Any] = {}
        if GATE_TEST_STATUS_PATH.exists():
            try:
                existing = json.loads(GATE_TEST_STATUS_PATH.read_text())
            except json.JSONDecodeError:
                existing = {}
        write_gate_test_status(
            {
                **existing,
                "ok": result["ok"],
                "scheduled": False,
                "startedAt": existing.get("startedAt") or started_at,
                "finishedAt": existing.get("finishedAt") or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "returncode": result["returncode"],
                "report": str(REPORT_DIR / "alarm_gate_test.md"),
                "stdout": result["stdout"],
                "stderr": result["stderr"],
            }
        )
    finally:
        GATE_TEST_LOCK.release()


def alarm_cache_stale_count() -> int | None:
    path = DATA_DIR / "latest_alarm_homebridge_state.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    stale_count = payload.get("staleCount")
    return int(stale_count) if isinstance(stale_count, (int, float)) else None


def wait_for_alarm_child_bridge(port: int, previous_pid: int | None, timeout: int = 60) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pid = listening_pid(port)
        if pid is not None and pid != previous_pid:
            return {"ok": True, "pid": pid}
        time.sleep(2)
    pid = listening_pid(port)
    return {"ok": pid is not None, "pid": pid, "timedOut": True}


def run_alarm_cache_refresh_background(started_at: str) -> None:
    try:
        config = load_config()["actions"]
        port = int(config["alarm_child_bridge_port"])
        before_stale = alarm_cache_stale_count()
        before_pid = listening_pid(port)
        first_capture = run(alarm_cache_refresh_command(), timeout=180)
        restart_result: dict[str, Any]
        wait_result: dict[str, Any]
        if before_pid is None:
            restart_result = {"ok": False, "error": f"no Alarm child bridge is listening on port {port}"}
            wait_result = {"ok": False, "pid": None}
        else:
            restart_result = terminate(before_pid)
            wait_result = wait_for_alarm_child_bridge(port, before_pid)
        second_capture = run(alarm_cache_refresh_command(), timeout=180) if wait_result.get("ok") else {"ok": False, "returncode": None, "stdout": "", "stderr": "Alarm child bridge did not restart"}
        after_stale = alarm_cache_stale_count()
        ok = bool(first_capture["ok"] and restart_result.get("ok") and wait_result.get("ok") and second_capture["ok"])
        write_alarm_cache_refresh_status(
            {
                "ok": ok,
                "startedAt": started_at,
                "finishedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "returncode": second_capture["returncode"],
                "alarmChildBridgePort": port,
                "previousPid": before_pid,
                "currentPid": wait_result.get("pid"),
                "staleBefore": before_stale,
                "staleAfter": after_stale,
                "alarmHomebridgeState": str(REPORT_DIR / "alarm_homebridge_state.md"),
                "alerts": str(REPORT_DIR / "alerts.md"),
                "homekitVirtualSensors": str(REPORT_DIR / "homekit_virtual_sensors.md"),
                "firstCapture": {
                    "ok": first_capture["ok"],
                    "returncode": first_capture["returncode"],
                    "stderr": first_capture["stderr"],
                },
                "restart": restart_result,
                "waitForRestart": wait_result,
                "secondCapture": {
                    "ok": second_capture["ok"],
                    "returncode": second_capture["returncode"],
                    "stdout": second_capture["stdout"],
                    "stderr": second_capture["stderr"],
                },
            }
        )
    finally:
        ALARM_CACHE_REFRESH_LOCK.release()


def refresh_sce_data() -> dict[str, Any]:
    if not SCE_REFRESH_LOCK.acquire(blocking=False):
        return {
            "ok": True,
            "scheduled": False,
            "alreadyRunning": True,
            "status": str(SCE_REFRESH_STATUS_PATH),
        }
    started_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    write_sce_refresh_status(
        {
            "ok": None,
            "scheduled": True,
            "startedAt": started_at,
            "status": "running",
        }
    )
    threading.Thread(target=run_sce_refresh_background, args=(started_at,), daemon=True).start()
    return {
        "ok": True,
        "scheduled": True,
        "sceApi": str(SCE_API_STATUS_PATH),
        "sceBills": str(REPORT_DIR / "sce_bill_readings.md"),
        "allEnergy": str(REPORT_DIR / "all_energy_pairing.md"),
        "energyCosts": str(REPORT_DIR / "energy_costs.md"),
        "combinedEnergy": str(REPORT_DIR / "combined_energy_monitor.md"),
        "alerts": str(REPORT_DIR / "alerts.md"),
        "status": str(SCE_REFRESH_STATUS_PATH),
    }


def refresh_and_reconcile_energy() -> dict[str, Any]:
    if not ENERGY_RECONCILE_LOCK.acquire(blocking=False):
        return {
            "ok": True,
            "scheduled": False,
            "alreadyRunning": True,
            "status": str(ENERGY_RECONCILE_STATUS_PATH),
        }
    started_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    write_energy_reconcile_status(
        {
            "ok": None,
            "scheduled": True,
            "startedAt": started_at,
            "status": "running",
        }
    )
    threading.Thread(target=run_energy_reconcile_background, args=(started_at,), daemon=True).start()
    return {
        "ok": True,
        "scheduled": True,
        "energyCosts": str(REPORT_DIR / "energy_costs.md"),
        "combinedEnergy": str(REPORT_DIR / "combined_energy_monitor.md"),
        "alerts": str(REPORT_DIR / "alerts.md"),
        "homekitVirtualSensors": str(REPORT_DIR / "homekit_virtual_sensors.md"),
        "status": str(ENERGY_RECONCILE_STATUS_PATH),
    }


def start_gate_test() -> dict[str, Any]:
    if not GATE_TEST_LOCK.acquire(blocking=False):
        return {
            "ok": True,
            "scheduled": False,
            "alreadyRunning": True,
            "status": str(GATE_TEST_STATUS_PATH),
            "report": str(REPORT_DIR / "alarm_gate_test.md"),
        }
    started_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    write_gate_test_status(
        {
            "ok": None,
            "scheduled": True,
            "startedAt": started_at,
            "status": "running",
            "report": str(REPORT_DIR / "alarm_gate_test.md"),
        }
    )
    threading.Thread(target=run_gate_test_background, args=(started_at,), daemon=True).start()
    return {
        "ok": True,
        "scheduled": True,
        "status": str(GATE_TEST_STATUS_PATH),
        "report": str(REPORT_DIR / "alarm_gate_test.md"),
    }


def refresh_alarm_cache() -> dict[str, Any]:
    if not ALARM_CACHE_REFRESH_LOCK.acquire(blocking=False):
        return {
            "ok": True,
            "scheduled": False,
            "alreadyRunning": True,
            "status": str(ALARM_CACHE_REFRESH_STATUS_PATH),
            "report": str(REPORT_DIR / "alarm_homebridge_state.md"),
        }
    started_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    write_alarm_cache_refresh_status(
        {
            "ok": None,
            "scheduled": True,
            "startedAt": started_at,
            "status": "running",
            "report": str(REPORT_DIR / "alarm_homebridge_state.md"),
        }
    )
    threading.Thread(target=run_alarm_cache_refresh_background, args=(started_at,), daemon=True).start()
    return {
        "ok": True,
        "scheduled": True,
        "status": str(ALARM_CACHE_REFRESH_STATUS_PATH),
        "report": str(REPORT_DIR / "alarm_homebridge_state.md"),
    }


def local_now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def read_garage_light_hold_state() -> dict[str, Any]:
    if not GARAGE_LIGHT_HOLD_STATUS_PATH.exists():
        return {}
    try:
        payload = json.loads(GARAGE_LIGHT_HOLD_STATUS_PATH.read_text())
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_garage_light_hold_state(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    GARAGE_LIGHT_HOLD_STATUS_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def garage_light_status() -> dict[str, Any]:
    return json_run(alarm_light_command("--status"), timeout=90)


def set_garage_light_on_100() -> dict[str, Any]:
    return json_run(
        alarm_light_command("--on", "--brightness", str(GARAGE_LIGHT_CONTROLLER_BRIGHTNESS)),
        timeout=90,
    )


def set_garage_light_off() -> dict[str, Any]:
    return json_run(alarm_light_command("--off", "--brightness", str(GARAGE_LIGHT_CONTROLLER_BRIGHTNESS)), timeout=90)


def set_garage_light_brightness(brightness: int) -> dict[str, Any]:
    return json_run(alarm_light_command("--on", "--brightness", str(brightness)), timeout=90)


def garage_light_restore_started_state(started_state: dict[str, Any]) -> dict[str, Any]:
    if bool(started_state.get("on")):
        brightness = started_state.get("brightness")
        if isinstance(brightness, (int, float)) and brightness > 0:
            return set_garage_light_brightness(int(brightness))
        return set_garage_light_on_100()
    return set_garage_light_off()


def schedule_garage_light_hold_check(state: dict[str, Any] | None = None) -> None:
    global GARAGE_LIGHT_HOLD_TIMER
    if GARAGE_LIGHT_HOLD_TIMER is not None:
        GARAGE_LIGHT_HOLD_TIMER.cancel()
        GARAGE_LIGHT_HOLD_TIMER = None

    payload = state if state is not None else read_garage_light_hold_state()
    if not payload.get("active"):
        return

    last_activity = parse_dt(payload.get("lastActivityAt"))
    if last_activity is None:
        return
    delay = max(1.0, (last_activity + timedelta(seconds=GARAGE_LIGHT_HOLD_SECONDS) - local_now()).total_seconds())
    GARAGE_LIGHT_HOLD_TIMER = threading.Timer(delay, expire_garage_light_hold)
    GARAGE_LIGHT_HOLD_TIMER.daemon = True
    GARAGE_LIGHT_HOLD_TIMER.start()


def trigger_garage_light_activity() -> dict[str, Any]:
    now = local_now()
    with GARAGE_LIGHT_HOLD_LOCK:
        existing = read_garage_light_hold_state()
        started_state = existing.get("startedState") if existing.get("active") else None
        if not isinstance(started_state, dict):
            before = garage_light_status()
            if not before.get("ok"):
                write_garage_light_hold_state(
                    {
                        **existing,
                        "active": False,
                        "lastErrorAt": now.isoformat(timespec="seconds"),
                        "lastError": before.get("error") or before.get("stderr") or "failed to read Garage Light state",
                    }
                )
                return {
                    "ok": False,
                    "error": "failed to read Garage Light state",
                    "detail": {k: before.get(k) for k in ("returncode", "stderr", "error")},
                    "status": str(GARAGE_LIGHT_HOLD_STATUS_PATH),
                }
            started_state = before.get("light") if isinstance(before.get("light"), dict) else {}

        command = set_garage_light_on_100()
        if not command.get("ok"):
            write_garage_light_hold_state(
                {
                    **existing,
                    "active": False,
                    "lastErrorAt": now.isoformat(timespec="seconds"),
                    "lastError": command.get("error") or command.get("stderr") or "failed to set Garage Light",
                }
            )
            return {
                "ok": False,
                "error": "failed to set Garage Light",
                "detail": {k: command.get(k) for k in ("returncode", "stderr", "error")},
                "status": str(GARAGE_LIGHT_HOLD_STATUS_PATH),
            }

        state = {
            "active": True,
            "lastActivityAt": now.isoformat(timespec="seconds"),
            "holdSeconds": GARAGE_LIGHT_HOLD_SECONDS,
            "controllerBrightness": GARAGE_LIGHT_CONTROLLER_BRIGHTNESS,
            "lightId": GARAGE_LIGHT_ID,
            "startedState": started_state,
            "lastCommandAt": now.isoformat(timespec="seconds"),
            "lastCommand": "hold-on",
            "lastCommandResult": command.get("light"),
            "status": "holding",
        }
        write_garage_light_hold_state(state)
        schedule_garage_light_hold_check(state)
        return {
            "ok": True,
            "scheduled": True,
            "holdUntil": (now + timedelta(seconds=GARAGE_LIGHT_HOLD_SECONDS)).isoformat(timespec="seconds"),
            "status": str(GARAGE_LIGHT_HOLD_STATUS_PATH),
            "light": command.get("light"),
        }


def expire_garage_light_hold() -> None:
    with GARAGE_LIGHT_HOLD_LOCK:
        state = read_garage_light_hold_state()
        if not state.get("active"):
            return

        now = local_now()
        last_activity = parse_dt(state.get("lastActivityAt"))
        if last_activity is None:
            state.update({"active": False, "status": "invalid-last-activity", "finishedAt": now.isoformat(timespec="seconds")})
            write_garage_light_hold_state(state)
            return

        elapsed = (now - last_activity).total_seconds()
        if elapsed < GARAGE_LIGHT_HOLD_SECONDS:
            schedule_garage_light_hold_check(state)
            return

        current = garage_light_status()
        if not current.get("ok"):
            state.update(
                {
                    "status": "expiry-status-failed",
                    "lastErrorAt": now.isoformat(timespec="seconds"),
                    "lastError": current.get("error") or current.get("stderr") or "failed to read Garage Light state",
                }
            )
            write_garage_light_hold_state(state)
            schedule_garage_light_hold_check(state)
            return

        light = current.get("light") if isinstance(current.get("light"), dict) else {}
        if not light.get("on") or int(light.get("brightness") or 0) != GARAGE_LIGHT_CONTROLLER_BRIGHTNESS:
            state.update(
                {
                    "active": False,
                    "status": "manual-change-detected",
                    "finishedAt": now.isoformat(timespec="seconds"),
                    "currentState": light,
                }
            )
            write_garage_light_hold_state(state)
            return

        restore = garage_light_restore_started_state(state.get("startedState") or {})
        state.update(
            {
                "active": False,
                "status": "restored" if restore.get("ok") else "restore-failed",
                "finishedAt": now.isoformat(timespec="seconds"),
                "restoreResult": restore.get("light"),
                "lastError": None if restore.get("ok") else restore.get("error") or restore.get("stderr"),
            }
        )
        write_garage_light_hold_state(state)


def delayed_restart_homebridge() -> None:
    pid = find_main_homebridge_pid()
    if pid is not None:
        terminate(pid)


def delayed_restart_office_tahoma() -> None:
    port = int(load_config()["actions"]["office_tahoma_child_bridge_port"])
    pid = listening_pid(port)
    if pid is not None:
        terminate(pid)


def restart_homebridge() -> dict[str, Any]:
    pid = find_main_homebridge_pid()
    if pid is None:
        return {"ok": False, "error": "main Homebridge child process was not found"}
    threading.Timer(0.6, delayed_restart_homebridge).start()
    return {"ok": True, "scheduled": True, "targetPid": pid}


def restart_office_tahoma() -> dict[str, Any]:
    port = int(load_config()["actions"]["office_tahoma_child_bridge_port"])
    pid = listening_pid(port)
    if pid is None:
        return {"ok": False, "error": f"no child bridge is listening on port {port}"}
    threading.Timer(0.6, delayed_restart_office_tahoma).start()
    return {"ok": True, "scheduled": True, "targetPid": pid, "port": port}


def silence_alerts() -> dict[str, Any]:
    minutes = int(load_config()["actions"]["silence_warning_minutes"])
    until = datetime.now(timezone.utc).astimezone() + timedelta(minutes=minutes)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "alerts_silenced_until.json"
    path.write_text(json.dumps({"until": until.isoformat(timespec="seconds")}, indent=2) + "\n")
    result = run(monitor_command(), timeout=120)
    return {
        "ok": result["ok"],
        "silencedUntil": until.isoformat(timespec="seconds"),
        "alerts": str(REPORT_DIR / "alerts.md"),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "SmartHomeActions/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LOG_DIR / "actions.access.log").open("a") as log:
            log.write(f"{datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')} {self.address_string()} {format % args}\n")

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def route(self) -> tuple[int, dict[str, Any]]:
        if self.path == "/health":
            return 200, {"ok": True}
        if self.path == "/action/run-check":
            payload = run_smart_home_check()
            return (200 if payload["ok"] else 500), payload
        if self.path == "/action/refresh-sce":
            payload = refresh_sce_data()
            return (200 if payload["ok"] else 500), payload
        if self.path == "/action/reconcile-energy":
            payload = refresh_and_reconcile_energy()
            return (202 if payload["ok"] else 500), payload
        if self.path == "/action/gate-test":
            payload = start_gate_test()
            return (202 if payload["ok"] else 500), payload
        if self.path == "/action/refresh-alarm-cache":
            payload = refresh_alarm_cache()
            return (202 if payload["ok"] else 500), payload
        if self.path == "/action/garage-activity":
            payload = trigger_garage_light_activity()
            return (202 if payload["ok"] else 500), payload
        if self.path == "/action/restart-homebridge":
            payload = restart_homebridge()
            return (202 if payload["ok"] else 500), payload
        if self.path == "/action/restart-office-tahoma":
            payload = restart_office_tahoma()
            return (202 if payload["ok"] else 500), payload
        if self.path == "/action/silence-alerts":
            payload = silence_alerts()
            return (200 if payload["ok"] else 500), payload
        return 404, {"ok": False, "error": "unknown endpoint"}

    def do_GET(self) -> None:
        status, payload = self.route()
        self.send_json(status, payload)

    def do_POST(self) -> None:
        status, payload = self.route()
        self.send_json(status, payload)


def main() -> int:
    config = load_config()["actions"]
    host = str(config["bind_host"])
    port = int(config["port"])
    os.chdir(ROOT)
    server = ThreadingHTTPServer((host, port), Handler)
    schedule_garage_light_hold_check()
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
