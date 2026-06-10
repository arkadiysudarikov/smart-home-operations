#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
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
SCE_REFRESH_LOCK = threading.Lock()
ENERGY_RECONCILE_LOCK = threading.Lock()


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
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
