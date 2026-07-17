#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import signal
import shutil
import subprocess
import sys
import argparse
import fcntl
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
STATUS_PATH = DATA_DIR / "latest_energy_refresh.json"
LOCK_PATH = DATA_DIR / "refresh_energy.lock"
BUNDLED_PYTHON = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
FAST_SCE_MIN_AGE_SECONDS = 3600
FAST_SCE_MAX_COVERAGE_AGE_SECONDS = 36 * 3600
FAST_ALARM_MIN_AGE_SECONDS = 900
FAST_SENSE_NOW_MIN_AGE_SECONDS = 300
FAST_CHARGEPOINT_MIN_AGE_SECONDS = 3600
ALARM_CACHE_AUTO_REFRESH_MIN_AGE_SECONDS = 900
ACTION_SERVER_BASE_URL = os.environ.get("SMART_HOME_ACTION_SERVER_URL", "http://127.0.0.1:18765").rstrip("/")


class RefreshInterrupted(Exception):
    pass


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
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                stdout, stderr = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout, stderr = proc.communicate()
            return {
                "name": name,
                "ok": False,
                "optional": optional,
                "startedAt": started_at,
                "finishedAt": now(),
                "returncode": proc.returncode,
                "stdout": stdout[-4000:],
                "stderr": f"timed out after {timeout} seconds\n{stderr[-4000:]}",
            }
        ok = proc.returncode == 0
        return {
            "name": name,
            "ok": ok,
            "optional": optional,
            "startedAt": started_at,
            "finishedAt": now(),
            "returncode": proc.returncode,
            "stdout": stdout[-4000:],
            "stderr": stderr[-4000:],
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


def skipped_step(name: str, reason: str, optional: bool = False) -> dict[str, Any]:
    timestamp = now()
    return {
        "name": name,
        "ok": True,
        "optional": optional,
        "skipped": True,
        "reason": reason,
        "startedAt": timestamp,
        "finishedAt": timestamp,
        "returncode": 0,
        "stdout": reason,
        "stderr": "",
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


def analyzer_cmd(py: str) -> list[str]:
    script = ROOT / "scripts/analyze_all_energy_readings.py"
    cmd = [py, str(script)]
    try:
        proc = subprocess.run(
            [py, str(script), "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return cmd
    help_text = f"{proc.stdout}\n{proc.stderr}"
    if "--scan-external-files" in help_text:
        cmd.append("--scan-external-files")
    return cmd


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    raw = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone()


def age_seconds(value: Any) -> float | None:
    parsed = parse_dt(value)
    if not parsed:
        return None
    return (datetime.now(timezone.utc).astimezone() - parsed).total_seconds()


def nested_value(payload: dict[str, Any], key: str) -> Any:
    current: Any = payload
    for part in key.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def is_recent_status(path: Path, max_age_seconds: int, *timestamp_keys: str) -> bool:
    payload = load_json(path)
    if "ok" in payload and payload.get("ok") is not True:
        return False
    for key in timestamp_keys:
        age = age_seconds(nested_value(payload, key))
        if age is not None:
            return age < max_age_seconds
    return False


def is_fresh_sce_api_status(path: Path, max_status_age_seconds: int = FAST_SCE_MIN_AGE_SECONDS) -> bool:
    payload = load_json(path)
    status_age = None
    for key in ("finishedAt", "generatedAt"):
        status_age = age_seconds(payload.get(key))
        if status_age is not None:
            break
    if status_age is None or status_age >= max_status_age_seconds:
        return False

    if "ok" in payload and payload.get("ok") is not True:
        return payload.get("status") in {
            "utilityapi_payment_required",
            "utilityapi_no_intervals",
            "utilityapi_coverage_stale",
            "registration_required",
        }

    api_coverage_end = parse_dt(payload.get("coverageEnd"))
    if api_coverage_end is None:
        return False
    api_coverage_age = (datetime.now(timezone.utc).astimezone() - api_coverage_end).total_seconds()
    if api_coverage_age >= FAST_SCE_MAX_COVERAGE_AGE_SECONDS:
        return False

    combined = load_json(DATA_DIR / "latest_combined_energy_monitor.json")
    local_coverage_end = parse_dt(((combined.get("sources") or {}).get("sce") or {}).get("coverageEnd"))
    if local_coverage_end is not None and api_coverage_end < local_coverage_end:
        return False
    return True


def write_status(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def finalize_status(
    payload: dict[str, Any],
    steps: list[dict[str, Any]],
    mode: str,
    status: str | None = None,
) -> dict[str, Any]:
    required_failed = [step for step in steps if not step.get("ok") and not step.get("optional")]
    optional_failed = [step for step in steps if not step.get("ok") and step.get("optional")]
    sce_api = load_json(DATA_DIR / "latest_sce_api.json")
    combined = load_json(DATA_DIR / "latest_combined_energy_monitor.json")
    sce_summary = (combined.get("sources") or {}).get("sce") or sce_api
    if status == "interrupted":
        ok: bool | None = None
        final_status = "interrupted"
    else:
        ok = not required_failed
        final_status = "failed" if required_failed else "complete"
    payload.update(
        {
            "ok": ok,
            "status": final_status,
            "mode": mode,
            "currentStep": None,
            "finishedAt": now(),
            "steps": steps,
            "stepSummary": summarize_steps(steps),
            "requiredFailures": [step["name"] for step in required_failed],
            "optionalFailures": [step["name"] for step in optional_failed],
            "sceCoverageEnd": sce_summary.get("coverageEnd"),
            "sceIntervalRows": sce_summary.get("intervalCount") or sce_api.get("intervalRows"),
            "combinedEnergyGeneratedAt": combined.get("generatedAt"),
            "combinedEnergy": str(REPORT_DIR / "combined_energy_monitor.md"),
            "energyCosts": str(REPORT_DIR / "energy_costs.md"),
            "alerts": str(REPORT_DIR / "alerts.md"),
            "energyAutomationOpportunities": str(REPORT_DIR / "energy_automation_opportunities.md"),
        }
    )
    write_status(payload)
    return payload


def acquire_refresh_lock() -> Any | None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = LOCK_PATH.open("w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None
    lock_file.write(f"{os.getpid()} {now()}\n")
    lock_file.flush()
    return lock_file


def release_refresh_lock(lock_file: Any | None) -> None:
    if lock_file is None:
        return
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()
    try:
        LOCK_PATH.unlink()
    except FileNotFoundError:
        pass


def summarize_steps(steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total": len(steps),
        "complete": sum(1 for step in steps if step.get("ok") is True),
        "skipped": sum(1 for step in steps if step.get("skipped") is True),
        "failed": sum(1 for step in steps if step.get("ok") is not True),
    }


def alarm_cache_stale_count() -> int | None:
    payload = load_json(DATA_DIR / "latest_alarm_homebridge_state.json")
    stale_count = payload.get("staleCount")
    return int(stale_count) if isinstance(stale_count, (int, float)) else None


def alarm_cache_refresh_is_recent_or_running(max_age_seconds: int = ALARM_CACHE_AUTO_REFRESH_MIN_AGE_SECONDS) -> bool:
    payload = load_json(DATA_DIR / "latest_alarm_cache_refresh.json")
    if payload.get("status") == "running":
        return True
    if payload.get("ok") is None and payload.get("startedAt") and not payload.get("finishedAt"):
        age = age_seconds(payload.get("startedAt"))
        if age is not None and age < max_age_seconds:
            return True
    for key in ("finishedAt", "startedAt"):
        age = age_seconds(payload.get(key))
        if age is not None and age < max_age_seconds:
            return True
    return False


def maybe_auto_refresh_alarm_cache() -> dict[str, Any]:
    started_at = now()
    stale_count = alarm_cache_stale_count()
    base = {
        "name": "auto_refresh_alarm_cache",
        "optional": True,
        "startedAt": started_at,
        "finishedAt": now(),
        "staleCount": stale_count,
    }
    if stale_count is None:
        return {**base, "ok": True, "skipped": True, "reason": "Alarm.com/Homebridge comparison is not available yet"}
    if stale_count <= 0:
        return {**base, "ok": True, "skipped": True, "reason": "Alarm.com/Homebridge cache is already clean"}
    if alarm_cache_refresh_is_recent_or_running():
        return {**base, "ok": True, "skipped": True, "reason": "Alarm cache refresh is already running or was triggered recently"}

    url = f"{ACTION_SERVER_BASE_URL}/action/refresh-alarm-cache"
    request = urllib.request.Request(url, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read().decode("utf-8", errors="replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {
            **base,
            "ok": False,
            "finishedAt": now(),
            "returncode": exc.code,
            "stdout": body[-4000:],
            "stderr": f"HTTP {exc.code}",
            "url": url,
        }
    except Exception as exc:
        return {
            **base,
            "ok": False,
            "finishedAt": now(),
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
            "url": url,
        }

    return {
        **base,
        "ok": 200 <= status < 300,
        "finishedAt": now(),
        "returncode": status,
        "stdout": body[-4000:],
        "stderr": "",
        "url": url,
    }


def main() -> int:
    def handle_interrupt(signum: int, _frame: Any) -> None:
        raise RefreshInterrupted(f"received signal {signum}")

    signal.signal(signal.SIGTERM, handle_interrupt)
    signal.signal(signal.SIGINT, handle_interrupt)

    parser = argparse.ArgumentParser()
    parser.add_argument("--fast", action="store_true", help="refresh live source status without full historical reconciliation")
    parser.add_argument("--with-bills", action="store_true", help="also rescan local SCE bill PDFs")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = acquire_refresh_lock()
    if lock_file is None:
        print("refresh_energy already running; skipping overlapping launch")
        return 0
    py = python_path()
    steps: list[dict[str, Any]] = []
    mode = "fast" if args.fast else "full"
    payload: dict[str, Any] = {
        "ok": None,
        "status": "running",
        "startedAt": now(),
        "python": py,
        "node": node_path(),
        "steps": [],
    }
    write_status(payload)
    try:
        if args.fast:
            plan: list[tuple[str, list[str] | str | None, int, bool, bool, str | None]] = [
                ("snapshot", [py, "scripts/smart_home_snapshot.py"], 45, True, False, None),
                (
                    "fetch_sce",
                    None
                    if is_fresh_sce_api_status(DATA_DIR / "latest_sce_api.json")
                    else [py, "scripts/fetch_sce_green_button_connect.py"],
                    600,
                    False,
                    False,
                    "recent SCE API capture is still fresh",
                ),
                (
                    "analyze_all_energy",
                    analyzer_cmd(py),
                    120,
                    True,
                    False,
                    None,
                ),
                ("capture_envoy_direct", [py, "scripts/capture_envoy_direct.py"], 60, True, False, None),
                (
                    "capture_sense_now",
                    None
                    if is_recent_status(DATA_DIR / "sense_now_latest.json", FAST_SENSE_NOW_MIN_AGE_SECONDS, "capturedAt", "generatedAt")
                    else "scripts/capture_sense_now.js",
                    120,
                    True,
                    True,
                    "recent Sense realtime capture is still fresh",
                ),
                (
                    "capture_alarm_com",
                    None
                    if is_recent_status(DATA_DIR / "latest_alarm_com.json", FAST_ALARM_MIN_AGE_SECONDS, "capturedAtLocal", "energy.capturedAtLocal", "finishedAt", "generatedAt")
                    else "scripts/capture_alarm_com.js",
                    300,
                    True,
                    True,
                    "recent Alarm.com capture is still fresh",
                ),
                (
                    "fetch_chargepoint",
                    None
                    if is_recent_status(DATA_DIR / "latest_chargepoint_refresh.json", FAST_CHARGEPOINT_MIN_AGE_SECONDS, "finishedAt", "generatedAt")
                    else [py, "scripts/fetch_chargepoint_sessions.py"],
                    300,
                    True,
                    False,
                    "recent ChargePoint capture is still fresh",
                ),
                ("snapshot_post_capture", [py, "scripts/smart_home_snapshot.py"], 45, True, False, None),
                ("analyze_combined_energy", [py, "scripts/analyze_combined_energy_monitor.py"], 300, True, False, None),
                ("analyze_energy_observability", [py, "scripts/analyze_energy_observability.py"], 120, True, False, None),
                ("generate_alerts", [py, "scripts/generate_alerts.py"], 300, True, False, None),
                ("analyze_energy_automation", [py, "scripts/analyze_energy_automation_opportunities.py"], 120, True, False, None),
                ("maintain_storage", [py, "scripts/maintain_storage.py"], 300, True, False, None),
            ]
        else:
            plan = [
                ("snapshot", [py, "scripts/smart_home_snapshot.py"], 120, True, False, None),
                ("fetch_sce", [py, "scripts/fetch_sce_green_button_connect.py"], 600, False, False, None),
            ]
            if args.with_bills:
                plan.append(("extract_sce_bills", [py, "scripts/extract_sce_bills.py"], 300, True, False, None))
            plan.extend(
                [
                    (
                        "analyze_all_energy",
                        analyzer_cmd(py),
                        300,
                        False,
                        False,
                        None,
                    ),
                    ("capture_envoy_direct", [py, "scripts/capture_envoy_direct.py"], 60, True, False, None),
                    ("capture_alarm_com", "scripts/capture_alarm_com.js", 300, True, True, None),
                    ("capture_sense_trends", "scripts/capture_sense_trends.js", 300, True, True, None),
                    ("capture_sense_now", "scripts/capture_sense_now.js", 120, True, True, None),
                    ("fetch_chargepoint", [py, "scripts/fetch_chargepoint_sessions.py"], 300, True, False, None),
                    ("snapshot_post_capture", [py, "scripts/smart_home_snapshot.py"], 120, True, False, None),
                    ("analyze_chargepoint", [py, "scripts/analyze_chargepoint_pairing.py"], 300, True, False, None),
                    ("analyze_meter_reconciliation", [py, "scripts/analyze_meter_reconciliation.py"], 300, True, False, None),
                    ("analyze_bill_home_pairing", [py, "scripts/analyze_bill_home_pairing.py"], 300, True, False, None),
                    ("analyze_energy_costs", [py, "scripts/analyze_energy_costs.py"], 300, False, False, None),
                    ("analyze_combined_energy", [py, "scripts/analyze_combined_energy_monitor.py"], 300, False, False, None),
                    ("analyze_energy_observability", [py, "scripts/analyze_energy_observability.py"], 120, True, False, None),
                    ("generate_alerts", [py, "scripts/generate_alerts.py"], 300, True, False, None),
                    ("analyze_energy_automation", [py, "scripts/analyze_energy_automation_opportunities.py"], 120, True, False, None),
                    ("install_homekit_virtual_sensors", [py, "scripts/install_homekit_virtual_sensors.py"], 120, True, False, None),
                    ("maintain_storage", [py, "scripts/maintain_storage.py"], 300, True, False, None),
                ]
            )

        for name, command, timeout, optional, is_node, skip_reason in plan:
            if command is None:
                step = skipped_step(name, skip_reason or "recent capture is still fresh", optional=optional)
            elif is_node:
                step = run_node_step(name, str(command), timeout=timeout, optional=optional)
            else:
                step = run_step(name, command if isinstance(command, list) else [command], timeout=timeout, optional=optional)
            steps.append(step)
            payload.update({"steps": steps, "currentStep": None if step.get("ok") else name})
            write_status(payload)
            if name == "generate_alerts":
                auto_step = maybe_auto_refresh_alarm_cache()
                steps.append(auto_step)
                payload.update({"steps": steps, "currentStep": None if auto_step.get("ok") else auto_step["name"]})
                write_status(payload)

        payload = finalize_status(payload, steps, mode)
        return 0 if payload["ok"] else 1
    except RefreshInterrupted as exc:
        payload["interruptReason"] = str(exc)
        finalize_status(payload, steps, mode, status="interrupted")
        return 130
    finally:
        release_refresh_lock(lock_file)


if __name__ == "__main__":
    raise SystemExit(main())
