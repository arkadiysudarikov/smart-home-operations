#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import html
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path.home() / "Library" / "Application Support" / "SmartHomeMonitor"
CONFIG_PATH = ROOT / "config" / "sources.json"
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
LOG_DIR = ROOT / "logs"
SCE_REFRESH_STATUS_PATH = DATA_DIR / "latest_sce_refresh.json"
SCE_API_STATUS_PATH = DATA_DIR / "latest_sce_api.json"
ENERGY_RECONCILE_STATUS_PATH = DATA_DIR / "latest_energy_reconcile.json"
GATE_TEST_STATUS_PATH = DATA_DIR / "latest_alarm_gate_test.json"
ALARM_CACHE_REFRESH_STATUS_PATH = DATA_DIR / "latest_alarm_cache_refresh.json"
UNIFI_OCCUPANCY_RECOVERY_STATUS_PATH = DATA_DIR / "latest_unifi_occupancy_recovery.json"
GARAGE_LIGHT_HOLD_STATUS_PATH = DATA_DIR / "garage_light_hold.json"
GARAGE_ACTIVITY_EVENTS_PATH = DATA_DIR / "garage_activity_events.jsonl"
ENERGY_REFRESH_STATUS_PATH = DATA_DIR / "latest_energy_refresh.json"
ACTION_STATUS_PATHS = {
    "check": DATA_DIR / "latest.json",
    "refreshEnergy": ENERGY_REFRESH_STATUS_PATH,
    "refreshSce": SCE_REFRESH_STATUS_PATH,
    "sceApi": SCE_API_STATUS_PATH,
    "reconcileEnergy": ENERGY_RECONCILE_STATUS_PATH,
    "alarmRefresh": ALARM_CACHE_REFRESH_STATUS_PATH,
    "unifiOccupancyRecovery": UNIFI_OCCUPANCY_RECOVERY_STATUS_PATH,
    "garageActivity": GARAGE_LIGHT_HOLD_STATUS_PATH,
}
SCE_REFRESH_LOCK = threading.Lock()
ENERGY_RECONCILE_LOCK = threading.Lock()
GATE_TEST_LOCK = threading.Lock()
ALARM_CACHE_REFRESH_LOCK = threading.Lock()
GARAGE_LIGHT_HOLD_LOCK = threading.Lock()
GARAGE_LIGHT_HOLD_TIMER: threading.Timer | None = None

GARAGE_LIGHT_ID = "104430779-1206"
GARAGE_LIGHT_HOLD_SECONDS = 300
GARAGE_LIGHT_CONTROLLER_BRIGHTNESS = 100
GARAGE_ACTIVITY_RECENT_LIMIT = 20
GARAGE_ACTIVITY_KNOWN_TRIGGERS = [
    "When Motion Detected in Garage",
    "Garage Door Contact Opens",
    "Garage Door Lock Unlocks",
    "Garage Door Opener 2207 Opens",
    "Garage Door Opener 2210 Opens",
    "When The First Person Arrives Home",
]
NODE_BIN = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
BUNDLED_PYTHON = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"


def running_from_runtime_root() -> bool:
    return ROOT.resolve() == RUNTIME_ROOT.resolve()


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


def python_bin() -> str:
    return str(BUNDLED_PYTHON if BUNDLED_PYTHON.exists() else Path(sys.executable))


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


def read_json_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path)}
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return {"exists": True, "path": str(path), "ok": False, "error": f"invalid JSON: {exc}"}

    if not isinstance(payload, dict):
        return {"exists": True, "path": str(path), "ok": False, "error": "status file is not a JSON object"}

    status = {
        "exists": True,
        "path": str(path),
        "ok": payload.get("ok"),
        "startedAt": payload.get("startedAt") or payload.get("checkedAt") or payload.get("timestamp"),
        "finishedAt": payload.get("finishedAt") or payload.get("generatedAt") or payload.get("captured_at") or payload.get("capturedAt") or payload.get("checkedAt"),
        "returncode": payload.get("returncode"),
        "status": payload.get("status"),
        "error": payload.get("error"),
    }
    if status["ok"] is None and isinstance(payload.get("homebridge"), dict):
        launchd = payload["homebridge"].get("launchd")
        if isinstance(launchd, dict) and "ok" in launchd:
            status["ok"] = bool(launchd["ok"])
    if status["ok"] is None and payload.get("status") in {"restored", "manual-change-detected"}:
        status["ok"] = True
    if status["ok"] is None and isinstance(payload.get("lastError"), str) and payload.get("lastError"):
        status["ok"] = False
    passthrough_keys = (
        "active",
        "staleBefore",
        "staleAfter",
        "coverageStart",
        "coverageEnd",
        "holdSeconds",
        "holdUntil",
        "lastActivityAt",
        "lastActivationAt",
        "activationCount",
        "requestedEnd",
        "intervalRows",
        "file",
        "mode",
        "currentStep",
        "optionalFailures",
        "requiredFailures",
        "stepSummary",
        "sceCoverageEnd",
        "sceIntervalRows",
        "combinedEnergyGeneratedAt",
        "combinedEnergy",
        "energyCosts",
        "alerts",
        "energyAutomationOpportunities",
    )
    for key in passthrough_keys:
        if key in payload:
            status[key] = payload[key]
    return status


def read_jsonl_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    for line in lines[-limit:]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def append_garage_activity_event(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    event = {
        "timestamp": local_now().isoformat(timespec="seconds"),
        **payload,
    }
    with GARAGE_ACTIVITY_EVENTS_PATH.open("a") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def garage_activity_report(status: dict[str, Any]) -> dict[str, Any]:
    recent = read_jsonl_tail(GARAGE_ACTIVITY_EVENTS_PATH, GARAGE_ACTIVITY_RECENT_LIMIT)
    activations = [event for event in recent if event.get("type") == "activation"]
    expirations = [event for event in recent if event.get("type") == "expiry"]
    active = bool(status.get("active"))
    last_activity = status.get("lastActivityAt")
    hold_until = status.get("holdUntil")
    if active and not hold_until:
        parsed = parse_status_dt(last_activity)
        if parsed is not None:
            hold_until = (parsed + timedelta(seconds=GARAGE_LIGHT_HOLD_SECONDS)).isoformat(timespec="seconds")

    last_expiry = expirations[-1] if expirations else None
    lights_turned_off: bool | None = None
    if not active and last_expiry is not None:
        final_light = last_expiry.get("restoreResult") if last_expiry.get("status") == "restored" else last_expiry.get("currentState")
        lights_turned_off = isinstance(final_light, dict) and final_light.get("on") is False
    return {
        "knownTriggers": GARAGE_ACTIVITY_KNOWN_TRIGGERS,
        "triggerAttribution": "HomeKit action-switch activations do not identify the upstream automation unless a caller passes ?trigger=...",
        "eventLog": str(GARAGE_ACTIVITY_EVENTS_PATH),
        "recentEvents": recent,
        "recentActivationCount": len(activations),
        "lastActivation": activations[-1] if activations else None,
        "activeHold": active,
        "holdSeconds": status.get("holdSeconds") or GARAGE_LIGHT_HOLD_SECONDS,
        "lastActivityAt": last_activity,
        "holdUntil": hold_until,
        "lastExpiry": last_expiry,
        "lightsTurnedOffAfterLastActivity": lights_turned_off,
    }


def status_is_failure(status: dict[str, Any]) -> bool:
    if status.get("ok") is False:
        return True
    summary = status.get("stepSummary")
    if isinstance(summary, dict) and status.get("ok") is not True:
        return int(summary.get("failed") or 0) > 0
    return False


def status_is_degraded(status: dict[str, Any]) -> bool:
    if status_is_failure(status):
        return True
    if status.get("optionalFailures"):
        return True
    summary = status.get("stepSummary")
    return isinstance(summary, dict) and int(summary.get("failed") or 0) > 0


def status_is_action_degraded(status: dict[str, Any]) -> bool:
    return status_is_failure(status)


def reconcile_was_superseded_by_refresh(reconcile: dict[str, Any], refresh: dict[str, Any]) -> bool:
    if reconcile.get("ok") is not False:
        return False
    if refresh.get("ok") is False:
        return False
    reconcile_finished = parse_status_dt(reconcile.get("finishedAt"))
    refresh_at = parse_status_dt(refresh.get("finishedAt")) or parse_status_dt(refresh.get("startedAt"))
    if reconcile_finished is None or refresh_at is None:
        return False
    return refresh_at >= reconcile_finished


def normalize_action_statuses(actions: dict[str, dict[str, Any]]) -> None:
    reconcile = actions.get("reconcileEnergy")
    refresh = actions.get("refreshEnergy")
    if not isinstance(reconcile, dict) or not isinstance(refresh, dict):
        return
    if not reconcile_was_superseded_by_refresh(reconcile, refresh):
        return
    reconcile["ok"] = True
    reconcile["status"] = "superseded"
    reconcile["supersededBy"] = "refreshEnergy"


def parse_status_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    raw = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone()


def source_age_hours(value: Any) -> float | None:
    parsed = parse_status_dt(value)
    if not parsed:
        return None
    return (datetime.now(timezone.utc).astimezone() - parsed).total_seconds() / 3600


def operational_source_status() -> list[dict[str, Any]]:
    sce = load_json_file(SCE_API_STATUS_PATH)
    chargepoint = load_json_file(DATA_DIR / "latest_chargepoint_refresh.json")
    alarm = load_json_file(ROOT / "config" / "alarm_energy_readings.json") or load_json_file(DATA_DIR / "latest_alarm_com.json")
    sense_trends = load_json_file(DATA_DIR / "sense_trends_latest.json")
    sense_now = load_json_file(DATA_DIR / "sense_now_latest.json")
    envoy = load_json_file(DATA_DIR / "latest_envoy_direct.json")
    refresh = load_json_file(ENERGY_REFRESH_STATUS_PATH)

    cp_status = str(chargepoint.get("status") or "missing")
    if chargepoint.get("ok") is not False and cp_status in {"downloaded", "fresh_enough"}:
        cp_row_status = "fresh"
    elif cp_status:
        cp_row_status = cp_status
    else:
        cp_row_status = "missing"

    sense_capture = sense_now.get("capturedAt") or sense_trends.get("capturedAt")
    sense_age = source_age_hours(sense_capture)
    sense_row_status = "missing"
    sense_detail = sense_capture or "credentials missing or not captured"
    if sense_capture:
        sense_row_status = "stale" if sense_age is not None and sense_age >= 24 else "fresh"
    for step in refresh.get("steps") or []:
        if step.get("name") == "capture_sense_now" and not step.get("ok"):
            stderr = str(step.get("stderr") or "")
            if "credentials were not found" in stderr:
                sense_row_status = "credentials_missing"
                sense_detail = "SENSE_USERNAME/SENSE_PASSWORD or config/sense.json Keychain entry required"
            else:
                sense_row_status = "failed"
                sense_detail = stderr.splitlines()[-1] if stderr else "Sense realtime capture failed"

    envoy_finished = envoy.get("finishedAt")
    envoy_status = str(envoy.get("status") or "missing")
    envoy_row_status = envoy_status if envoy_status in {"live", "auth_required", "reachable", "unreachable"} else ("missing" if not envoy.get("exists") else envoy_status)

    return [
        {
            "source": "Envoy",
            "status": envoy_row_status,
            "ageHours": source_age_hours(envoy_finished),
            "detail": f"{envoy.get('host') or 'n/a'} {envoy.get('serialNumber') or ''}".strip(),
        },
        {
            "source": "Sense",
            "status": sense_row_status,
            "ageHours": sense_age,
            "detail": sense_detail,
        },
        {
            "source": "SCE",
            "status": "fresh" if sce.get("ok") and sce.get("coverageEnd") else ("missing" if not sce else "stale"),
            "ageHours": source_age_hours(sce.get("coverageEnd")),
            "detail": sce.get("coverageEnd"),
        },
        {
            "source": "ChargePoint",
            "status": cp_row_status,
            "ageHours": source_age_hours(chargepoint.get("finishedAt")),
            "detail": chargepoint.get("mode") or chargepoint.get("fallbackReason") or cp_status,
        },
        {
            "source": "Alarm.com",
            "status": "fresh" if alarm.get("capturedAtLocal") and (source_age_hours(alarm.get("capturedAtLocal")) or 999) < 24 else "stale",
            "ageHours": source_age_hours(alarm.get("capturedAtLocal")),
            "detail": alarm.get("capturedAtLocal"),
        },
    ]


def action_status() -> dict[str, Any]:
    actions = {name: read_json_status(path) for name, path in ACTION_STATUS_PATHS.items()}
    normalize_action_statuses(actions)
    if "garageActivity" in actions:
        actions["garageActivity"]["activityReport"] = garage_activity_report(actions["garageActivity"])
    failed = [name for name, status in actions.items() if status_is_failure(status)]
    degraded = [name for name, status in actions.items() if status_is_action_degraded(status)]
    return {
        "ok": not failed,
        "status": "failed" if failed else "degraded" if degraded else "ok",
        "degraded": bool(degraded),
        "failedActions": failed,
        "degradedActions": degraded,
        "generatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "actions": actions,
    }


def energy_status() -> dict[str, Any]:
    combined = read_json_status(DATA_DIR / "latest_combined_energy_monitor.json")
    refresh = read_json_status(ENERGY_REFRESH_STATUS_PATH)
    sce = read_json_status(SCE_API_STATUS_PATH)
    chargepoint = read_json_status(DATA_DIR / "latest_chargepoint_refresh.json")
    alarm = read_json_status(DATA_DIR / "latest_alarm_com.json")
    alarm_energy = read_json_status(ROOT / "config" / "alarm_energy_readings.json")
    sense = read_json_status(DATA_DIR / "sense_trends_latest.json")
    sense_now = read_json_status(DATA_DIR / "sense_now_latest.json")
    envoy = read_json_status(DATA_DIR / "latest_envoy_direct.json")
    automation = read_json_status(DATA_DIR / "latest_energy_automation_opportunities.json")
    statuses = {
        "refresh": refresh,
        "sce": sce,
        "chargepoint": chargepoint,
        "alarm": alarm,
        "alarmEnergy": alarm_energy,
        "sense": sense,
        "senseNow": sense_now,
        "envoy": envoy,
        "automationOpportunities": automation,
        "combined": combined,
    }
    failed = [name for name, status in statuses.items() if status_is_failure(status)]
    degraded = [name for name, status in statuses.items() if status_is_degraded(status)]
    payload = {
        "ok": not failed,
        "status": "failed" if failed else "degraded" if degraded else "ok",
        "degraded": bool(degraded),
        "failedSources": failed,
        "degradedSources": degraded,
        "generatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "refresh": refresh,
        "sce": sce,
        "chargepoint": chargepoint,
        "alarm": alarm,
        "alarmEnergy": alarm_energy,
        "sense": sense,
        "senseNow": sense_now,
        "envoy": envoy,
        "automationOpportunities": automation,
        "combined": combined,
        "operationalSourceStatus": operational_source_status(),
    }
    combined_payload = load_json_file(DATA_DIR / "latest_combined_energy_monitor.json")
    if combined_payload:
        payload["sourceStatus"] = combined_payload.get("sourceStatus", [])
        payload["alerts"] = combined_payload.get("alerts", [])
        payload["dailySummary"] = combined_payload.get("dailySummary", [])[-10:]
    automation_payload = load_json_file(DATA_DIR / "latest_energy_automation_opportunities.json")
    if automation_payload:
        payload["opportunities"] = automation_payload.get("opportunities", [])
    return payload


def load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def html_escape(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def render_energy_page() -> bytes:
    status = energy_status()
    sources = status.get("operationalSourceStatus") or status.get("sourceStatus") or []
    alerts = status.get("alerts") or []
    opportunities = status.get("opportunities") or []
    refresh = status.get("refresh") or {}
    source_rows = "\n".join(
        "<tr>"
        f"<td>{html_escape(item.get('source'))}</td>"
        f"<td><span class='pill {html_escape(item.get('status'))}'>{html_escape(item.get('status'))}</span></td>"
        f"<td>{html_escape(item.get('detail'))}</td>"
        f"<td>{html_escape(round(float(item.get('ageHours')), 2) if isinstance(item.get('ageHours'), (int, float)) else item.get('ageDays'))}</td>"
        "</tr>"
        for item in sources
    )
    alert_rows = "\n".join(
        f"<li><strong>{html_escape(item.get('severity'))}</strong> {html_escape(item.get('title'))}: {html_escape(item.get('detail'))}</li>"
        for item in alerts
    ) or "<li>No combined-energy alerts.</li>"
    opportunity_rows = "\n".join(
        "<tr>"
        f"<td><span class='pill {html_escape(item.get('priority'))}'>{html_escape(item.get('priority'))}</span></td>"
        f"<td>{html_escape(item.get('area'))}</td>"
        f"<td>{html_escape(item.get('recommendation'))}</td>"
        f"<td>{html_escape(item.get('automation'))}</td>"
        "</tr>"
        for item in opportunities
    ) or "<tr><td colspan='4'>No automation recommendations generated yet.</td></tr>"
    body = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Smart Home Energy</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 32px; color: #1f2933; }}
    h1 {{ font-size: 28px; margin-bottom: 4px; }}
    .muted {{ color: #64748b; }}
    .actions {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 24px 0; }}
    button {{ border: 1px solid #0f766e; background: #0f766e; color: white; border-radius: 6px; padding: 10px 14px; font-size: 14px; cursor: pointer; }}
    button.secondary {{ background: white; color: #0f766e; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 12px; }}
    th, td {{ border-bottom: 1px solid #e2e8f0; text-align: left; padding: 10px 8px; vertical-align: top; }}
    th {{ font-size: 12px; text-transform: uppercase; color: #64748b; }}
    .pill {{ border-radius: 999px; padding: 3px 8px; background: #e2e8f0; }}
    .pill.fresh, .pill.complete {{ background: #dcfce7; color: #166534; }}
    .pill.stale, .pill.failed, .pill.missing, .pill.auth_required, .pill.unreachable, .pill.credentials_missing {{ background: #fee2e2; color: #991b1b; }}
    .pill.downloaded, .pill.fallback, .pill.reachable {{ background: #fef3c7; color: #92400e; }}
    .pill.high {{ background: #fee2e2; color: #991b1b; }}
    .pill.medium {{ background: #fef3c7; color: #92400e; }}
    .pill.low {{ background: #e0f2fe; color: #075985; }}
    .panel {{ margin-top: 24px; }}
    code {{ background: #f1f5f9; padding: 2px 4px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>Smart Home Energy</h1>
  <div class="muted">Generated {html_escape(status.get('generatedAt'))}</div>
  <div class="panel">
    <strong>Last refresh:</strong>
    <span class="pill {html_escape(refresh.get('status'))}">{html_escape(refresh.get('status') or 'unknown')}</span>
    <span class="muted">finished {html_escape(refresh.get('finishedAt'))}</span>
  </div>
  <div class="actions">
    <button data-action="/action/reconcile-energy">Refresh All Energy</button>
    <button class="secondary" data-action="/action/refresh-sce">Refresh SCE</button>
    <button class="secondary" data-action="/action/refresh-alarm-cache">Refresh Alarm.com</button>
  </div>
  <div id="result" class="muted"></div>
  <div class="panel">
    <h2>Source Status</h2>
    <table>
      <thead><tr><th>Source</th><th>Status</th><th>Detail</th><th>Age</th></tr></thead>
      <tbody>{source_rows}</tbody>
    </table>
  </div>
  <div class="panel">
    <h2>Alerts</h2>
    <ul>{alert_rows}</ul>
  </div>
  <div class="panel">
    <h2>Automation Opportunities</h2>
    <table>
      <thead><tr><th>Priority</th><th>Area</th><th>Recommendation</th><th>Automation Path</th></tr></thead>
      <tbody>{opportunity_rows}</tbody>
    </table>
  </div>
  <p class="muted">JSON: <code>/status/energy</code></p>
  <script>
    document.querySelectorAll('button[data-action]').forEach((button) => {{
      button.addEventListener('click', async () => {{
        button.disabled = true;
        document.getElementById('result').textContent = 'Requesting ' + button.textContent + '...';
        try {{
          const response = await fetch(button.dataset.action, {{ method: 'POST' }});
          const payload = await response.json();
          document.getElementById('result').textContent = JSON.stringify(payload, null, 2);
        }} catch (error) {{
          document.getElementById('result').textContent = String(error);
        }} finally {{
          button.disabled = false;
        }}
      }});
    }});
  </script>
</body>
</html>
"""
    return body.encode()


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
        f'"{python_bin()}" ./scripts/refresh_energy.py',
    ]


def sce_refresh_command() -> list[str]:
    return [
        "/bin/zsh",
        "-lc",
        (
            'export SMART_HOME_SCAN_EXTERNAL_FILES=true; '
            f'"{python_bin()}" ./scripts/fetch_sce_green_button_connect.py && '
            f'"{python_bin()}" ./scripts/refresh_energy.py --fast'
        ),
    ]


def energy_reconcile_command() -> list[str]:
    return [
        "/bin/zsh",
        "-lc",
        f'"{python_bin()}" ./scripts/refresh_energy.py',
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


def alarm_cache_repair_command() -> list[str]:
    return [python_bin(), "scripts/repair_alarm_homebridge_cache.py"]


def alarm_light_command(*args: str) -> list[str]:
    return [str(NODE_BIN), str(ROOT / "scripts" / "set_alarm_light.js"), "--light-id", GARAGE_LIGHT_ID, *args]


def alarm_panel_command(mode: str) -> list[str]:
    return [str(NODE_BIN), str(ROOT / "scripts" / "set_alarm_panel.js"), "--mode", mode]


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


def latest_energy_refresh_status() -> dict[str, Any]:
    payload = load_json_file(ENERGY_REFRESH_STATUS_PATH)
    return payload if isinstance(payload, dict) else {}


def latest_sce_api_status() -> dict[str, Any]:
    payload = load_json_file(SCE_API_STATUS_PATH)
    return payload if isinstance(payload, dict) else {}


def energy_refresh_summary() -> dict[str, Any]:
    latest = latest_energy_refresh_status()
    return {
        "energyRefresh": str(ENERGY_REFRESH_STATUS_PATH),
        "energyRefreshOk": latest.get("ok"),
        "energyRefreshStatus": latest.get("status"),
        "energyRefreshMode": latest.get("mode"),
        "energyRefreshRequiredFailures": latest.get("requiredFailures"),
        "sceCoverageEnd": latest.get("sceCoverageEnd"),
        "sceIntervalRows": latest.get("sceIntervalRows"),
        "combinedEnergyGeneratedAt": latest.get("combinedEnergyGeneratedAt"),
        "energyAutomationOpportunities": latest.get("energyAutomationOpportunities"),
    }


def run_sce_refresh_background(started_at: str) -> None:
    try:
        result = run(sce_refresh_command(), timeout=600)
        latest = latest_energy_refresh_status()
        sce_api = latest_sce_api_status()
        ok = bool(result["ok"] and sce_api.get("ok"))
        write_sce_refresh_status(
            {
                "ok": ok,
                "startedAt": started_at,
                "finishedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "returncode": result["returncode"],
                "sceApi": str(SCE_API_STATUS_PATH),
                "sceBills": str(REPORT_DIR / "sce_bill_readings.md"),
                "allEnergy": str(REPORT_DIR / "all_energy_pairing.md"),
                "energyCosts": str(REPORT_DIR / "energy_costs.md"),
                "combinedEnergy": str(REPORT_DIR / "combined_energy_monitor.md"),
                "alerts": str(REPORT_DIR / "alerts.md"),
                "energyAutomationOpportunities": str(REPORT_DIR / "energy_automation_opportunities.md"),
                "stdout": result["stdout"],
                "stderr": result["stderr"],
                "sceApiOk": sce_api.get("ok"),
                "sceApiStatus": sce_api.get("status"),
                "sceApiFinishedAt": sce_api.get("finishedAt") or sce_api.get("generatedAt"),
                **energy_refresh_summary(),
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
        latest = latest_energy_refresh_status()
        ok = bool(result["ok"] and (latest.get("ok") if latest else True))
        write_energy_reconcile_status(
            {
                "ok": ok,
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
                "energyAutomationOpportunities": str(REPORT_DIR / "energy_automation_opportunities.md"),
                "homekitVirtualSensors": str(REPORT_DIR / "homekit_virtual_sensors.md"),
                "stdout": result["stdout"],
                "stderr": result["stderr"],
                **energy_refresh_summary(),
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
        producer_finished = bool(existing.get("finishedAt"))
        producer_ok = existing.get("ok") if isinstance(existing.get("ok"), bool) else result["ok"]
        write_gate_test_status(
            {
                **existing,
                "ok": producer_ok,
                "scheduled": False,
                "startedAt": existing.get("startedAt") or started_at,
                "finishedAt": existing.get("finishedAt")
                or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "status": existing.get("status") if producer_finished else ("complete" if result["ok"] else "failed"),
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
        repair_result: dict[str, Any] = {"ok": True, "returncode": None, "stdout": "", "stderr": "", "skipped": True}
        repair_restart_result: dict[str, Any] = {"ok": True, "skipped": True}
        repair_wait_result: dict[str, Any] = {"ok": True, "skipped": True}
        third_capture: dict[str, Any] = {"ok": True, "returncode": None, "stdout": "", "stderr": "", "skipped": True}
        if second_capture["ok"] and after_stale and after_stale > 0:
            repair_result = run(alarm_cache_repair_command(), timeout=30)
            try:
                parsed_repair = json.loads(repair_result.get("stdout") or "{}")
            except json.JSONDecodeError:
                parsed_repair = {}
            if repair_result["ok"] and parsed_repair.get("changedCount", 0) > 0:
                repair_pid = listening_pid(port)
                if repair_pid is None:
                    repair_restart_result = {"ok": False, "error": f"no Alarm child bridge is listening on port {port}"}
                    repair_wait_result = {"ok": False, "pid": None}
                else:
                    repair_restart_result = terminate(repair_pid)
                    repair_wait_result = wait_for_alarm_child_bridge(port, repair_pid)
                third_capture = run(alarm_cache_refresh_command(), timeout=180) if repair_wait_result.get("ok") else {"ok": False, "returncode": None, "stdout": "", "stderr": "Alarm child bridge did not restart after cache repair"}
                after_stale = alarm_cache_stale_count()
        ok = bool(
            first_capture["ok"]
            and restart_result.get("ok")
            and wait_result.get("ok")
            and second_capture["ok"]
            and repair_result.get("ok")
            and repair_restart_result.get("ok")
            and repair_wait_result.get("ok")
            and third_capture.get("ok")
            and (after_stale in (0, None) or after_stale == 0)
        )
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
                "repair": {
                    "ok": repair_result.get("ok"),
                    "returncode": repair_result.get("returncode"),
                    "stdout": repair_result.get("stdout"),
                    "stderr": repair_result.get("stderr"),
                },
                "repairRestart": repair_restart_result,
                "repairWaitForRestart": repair_wait_result,
                "thirdCapture": {
                    "ok": third_capture.get("ok"),
                    "returncode": third_capture.get("returncode"),
                    "stdout": third_capture.get("stdout"),
                    "stderr": third_capture.get("stderr"),
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
        "energyAutomationOpportunities": str(REPORT_DIR / "energy_automation_opportunities.md"),
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
        "energyAutomationOpportunities": str(REPORT_DIR / "energy_automation_opportunities.md"),
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


def set_alarm_panel(mode: str) -> dict[str, Any]:
    result = json_run(alarm_panel_command(mode), timeout=120)
    return {
        "ok": bool(result.get("ok")),
        "mode": mode,
        "partition": result.get("partition"),
        "responseStatus": result.get("responseStatus"),
        "returncode": result.get("returncode"),
        "error": result.get("error"),
        "stderr": result.get("stderr"),
    }


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


def trigger_garage_light_activity(trigger: str | None = None, source: str | None = None, remote_addr: str | None = None) -> dict[str, Any]:
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
                append_garage_activity_event(
                    {
                        "type": "activation",
                        "ok": False,
                        "trigger": trigger,
                        "source": source,
                        "remoteAddr": remote_addr,
                        "error": before.get("error") or before.get("stderr") or "failed to read Garage Light state",
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
            append_garage_activity_event(
                {
                    "type": "activation",
                    "ok": False,
                    "trigger": trigger,
                    "source": source,
                    "remoteAddr": remote_addr,
                    "error": command.get("error") or command.get("stderr") or "failed to set Garage Light",
                }
            )
            return {
                "ok": False,
                "error": "failed to set Garage Light",
                "detail": {k: command.get(k) for k in ("returncode", "stderr", "error")},
                "status": str(GARAGE_LIGHT_HOLD_STATUS_PATH),
            }

        hold_until = now + timedelta(seconds=GARAGE_LIGHT_HOLD_SECONDS)
        state = {
            "active": True,
            "lastActivityAt": now.isoformat(timespec="seconds"),
            "lastActivationAt": now.isoformat(timespec="seconds"),
            "holdSeconds": GARAGE_LIGHT_HOLD_SECONDS,
            "holdUntil": hold_until.isoformat(timespec="seconds"),
            "controllerBrightness": GARAGE_LIGHT_CONTROLLER_BRIGHTNESS,
            "lightId": GARAGE_LIGHT_ID,
            "startedState": started_state,
            "lastCommandAt": now.isoformat(timespec="seconds"),
            "lastCommand": "hold-on",
            "lastCommandResult": command.get("light"),
            "status": "holding",
            "activationCount": int(existing.get("activationCount") or 0) + 1,
            "lastTrigger": trigger,
            "lastSource": source,
        }
        write_garage_light_hold_state(state)
        append_garage_activity_event(
            {
                "type": "activation",
                "ok": True,
                "trigger": trigger,
                "source": source,
                "remoteAddr": remote_addr,
                "activationCount": state["activationCount"],
                "holdUntil": state["holdUntil"],
                "light": command.get("light"),
            }
        )
        schedule_garage_light_hold_check(state)
        return {
            "ok": True,
            "scheduled": True,
            "holdUntil": hold_until.isoformat(timespec="seconds"),
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
            append_garage_activity_event({"type": "expiry", "ok": False, "status": "invalid-last-activity"})
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
            append_garage_activity_event(
                {
                    "type": "expiry",
                    "ok": False,
                    "status": "expiry-status-failed",
                    "lastActivityAt": state.get("lastActivityAt"),
                    "error": state.get("lastError"),
                }
            )
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
            append_garage_activity_event(
                {
                    "type": "expiry",
                    "ok": True,
                    "status": "manual-change-detected",
                    "lastActivityAt": state.get("lastActivityAt"),
                    "finishedAt": state.get("finishedAt"),
                    "currentState": light,
                }
            )
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
        append_garage_activity_event(
            {
                "type": "expiry",
                "ok": bool(restore.get("ok")),
                "status": state.get("status"),
                "lastActivityAt": state.get("lastActivityAt"),
                "finishedAt": state.get("finishedAt"),
                "restoreResult": restore.get("light"),
                "error": None if restore.get("ok") else restore.get("error") or restore.get("stderr"),
            }
        )


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
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def send_html(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def route(self) -> tuple[int, dict[str, Any]]:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/health":
            return 200, {"ok": True}
        if path == "/status":
            return 200, action_status()
        if path == "/status/energy":
            return 200, energy_status()
        if path == "/action/run-check":
            payload = run_smart_home_check()
            return (200 if payload["ok"] else 500), payload
        if path == "/action/refresh-sce":
            payload = refresh_sce_data()
            return (200 if payload["ok"] else 500), payload
        if path == "/action/reconcile-energy":
            payload = refresh_and_reconcile_energy()
            return (202 if payload["ok"] else 500), payload
        if path == "/action/gate-test":
            payload = start_gate_test()
            return (202 if payload["ok"] else 500), payload
        if path == "/action/refresh-alarm-cache":
            payload = refresh_alarm_cache()
            return (202 if payload["ok"] else 500), payload
        if path == "/action/garage-activity":
            payload = trigger_garage_light_activity(
                trigger=(query.get("trigger") or [None])[0],
                source=(query.get("source") or [None])[0],
                remote_addr=self.client_address[0] if self.client_address else None,
            )
            return (202 if payload["ok"] else 500), payload
        if path == "/action/panel-home":
            payload = set_alarm_panel("home")
            return (202 if payload["ok"] else 500), payload
        if path == "/action/panel-stay":
            payload = set_alarm_panel("stay")
            return (202 if payload["ok"] else 500), payload
        if path == "/action/panel-off":
            payload = set_alarm_panel("off")
            return (202 if payload["ok"] else 500), payload
        if path == "/action/restart-homebridge":
            payload = restart_homebridge()
            return (202 if payload["ok"] else 500), payload
        if path == "/action/restart-office-tahoma":
            payload = restart_office_tahoma()
            return (202 if payload["ok"] else 500), payload
        if path == "/action/silence-alerts":
            payload = silence_alerts()
            return (200 if payload["ok"] else 500), payload
        return 404, {"ok": False, "error": "unknown endpoint"}

    def do_GET(self) -> None:
        if self.path in {"/", "/energy"}:
            self.send_html(200, render_energy_page())
            return
        status, payload = self.route()
        self.send_json(status, payload)

    def do_POST(self) -> None:
        status, payload = self.route()
        self.send_json(status, payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Smart Home action server.")
    parser.add_argument(
        "--force-outside-runtime",
        action="store_true",
        help="allow the action server to expose live actions outside the runtime root",
    )
    args = parser.parse_args()
    if not args.force_outside_runtime and not running_from_runtime_root():
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "refusing to expose live Smart Home actions outside the runtime root",
                    "sourceRoot": str(ROOT),
                    "runtimeRoot": str(RUNTIME_ROOT),
                },
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1

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
