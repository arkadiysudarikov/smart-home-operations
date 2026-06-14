#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
STATUS_PATH = DATA_DIR / "latest_energy_refresh.json"
BUNDLED_PYTHON = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def node_path() -> str | None:
    candidates = [
        os.environ.get("NODE"),
        shutil.which("node"),
        str(Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"),
        str(Path.home() / ".local/node-v24.16.0-darwin-arm64/bin/node"),
        "/opt/homebrew/bin/node",
        "/usr/local/bin/node",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def python_path() -> str:
    if BUNDLED_PYTHON.exists():
        return str(BUNDLED_PYTHON)
    return sys.executable


def run_step(name: str, cmd: list[str], timeout: int = 300, optional: bool = False) -> dict[str, Any]:
    started_at = now()
    try:
        proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False)
        ok = proc.returncode == 0
        return {
            "name": name,
            "ok": ok,
            "optional": optional,
            "startedAt": started_at,
            "finishedAt": now(),
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
        }
    except Exception as exc:
        return {
            "name": name,
            "ok": False,
            "optional": optional,
            "startedAt": started_at,
            "finishedAt": now(),
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
        }


def run_node_step(name: str, script: str, timeout: int = 300, optional: bool = False) -> dict[str, Any]:
    node = node_path()
    if not node:
        return {
            "name": name,
            "ok": False,
            "optional": optional,
            "skipped": True,
            "startedAt": now(),
            "finishedAt": now(),
            "returncode": None,
            "stdout": "",
            "stderr": "node binary was not found",
        }
    return run_step(name, [node, script], timeout=timeout, optional=optional)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_status(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fast", action="store_true", help="refresh live source status without full historical reconciliation")
    parser.add_argument("--with-bills", action="store_true", help="also rescan local SCE bill PDFs")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    py = python_path()
    payload: dict[str, Any] = {
        "ok": None,
        "status": "running",
        "startedAt": now(),
        "python": py,
        "node": node_path(),
        "steps": [],
    }
    write_status(payload)

    if args.fast:
        plan: list[tuple[str, list[str] | str, int, bool, bool]] = [
            ("snapshot", [py, "scripts/smart_home_snapshot.py"], 120, True, False),
            ("fetch_sce", [py, "scripts/fetch_sce_green_button_connect.py"], 600, False, False),
            ("capture_envoy_direct", [py, "scripts/capture_envoy_direct.py"], 60, True, False),
            ("capture_alarm_com", "scripts/capture_alarm_com.js", 300, True, True),
            ("fetch_chargepoint", [py, "scripts/fetch_chargepoint_sessions.py"], 300, True, False),
            ("generate_alerts", [py, "scripts/generate_alerts.py"], 300, True, False),
        ]
    else:
        plan = [
        ("snapshot", [py, "scripts/smart_home_snapshot.py"], 120, True, False),
        ("fetch_sce", [py, "scripts/fetch_sce_green_button_connect.py"], 600, False, False),
        ]
        if args.with_bills:
            plan.append(("extract_sce_bills", [py, "scripts/extract_sce_bills.py"], 300, True, False))
        plan.extend(
            [
                ("analyze_all_energy", [py, "scripts/analyze_all_energy_readings.py"], 300, False, False),
                ("capture_envoy_direct", [py, "scripts/capture_envoy_direct.py"], 60, True, False),
                ("capture_alarm_com", "scripts/capture_alarm_com.js", 300, True, True),
                ("capture_sense_trends", "scripts/capture_sense_trends.js", 300, True, True),
                ("capture_sense_now", "scripts/capture_sense_now.js", 120, True, True),
                ("fetch_chargepoint", [py, "scripts/fetch_chargepoint_sessions.py"], 300, True, False),
                ("analyze_chargepoint", [py, "scripts/analyze_chargepoint_pairing.py"], 300, True, False),
                ("analyze_meter_reconciliation", [py, "scripts/analyze_meter_reconciliation.py"], 300, True, False),
                ("analyze_bill_home_pairing", [py, "scripts/analyze_bill_home_pairing.py"], 300, True, False),
                ("analyze_energy_costs", [py, "scripts/analyze_energy_costs.py"], 300, False, False),
                ("analyze_combined_energy", [py, "scripts/analyze_combined_energy_monitor.py"], 300, False, False),
                ("generate_alerts", [py, "scripts/generate_alerts.py"], 300, True, False),
                ("install_homekit_virtual_sensors", [py, "scripts/install_homekit_virtual_sensors.py"], 120, True, False),
            ]
        )

    steps = []
    for name, command, timeout, optional, is_node in plan:
        if is_node:
            step = run_node_step(name, str(command), timeout=timeout, optional=optional)
        else:
            step = run_step(name, command if isinstance(command, list) else [command], timeout=timeout, optional=optional)
        steps.append(step)
        payload.update({"steps": steps, "currentStep": None if step.get("ok") else name})
        write_status(payload)

    required_failed = [step for step in steps if not step.get("ok") and not step.get("optional")]
    sce_api = load_json(DATA_DIR / "latest_sce_api.json")
    combined = load_json(DATA_DIR / "latest_combined_energy_monitor.json")
    payload.update(
        {
            "ok": not required_failed,
            "status": "failed" if required_failed else "complete",
            "mode": "fast" if args.fast else "full",
            "currentStep": None,
            "finishedAt": now(),
            "steps": steps,
            "requiredFailures": [step["name"] for step in required_failed],
            "sceCoverageEnd": sce_api.get("coverageEnd"),
            "sceIntervalRows": sce_api.get("intervalRows"),
            "combinedEnergyGeneratedAt": combined.get("generatedAt"),
            "combinedEnergy": str(REPORT_DIR / "combined_energy_monitor.md"),
            "energyCosts": str(REPORT_DIR / "energy_costs.md"),
            "alerts": str(REPORT_DIR / "alerts.md"),
        }
    )
    write_status(payload)
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
