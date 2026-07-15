#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import html
import os
import re
import signal
import sqlite3
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
DB_PATH = DATA_DIR / "smart_home.sqlite"
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
DISPLAY_AWAKE_STATUS_PATH = DATA_DIR / "latest_display_awake.json"
DISPLAY_AWAKE_SUMMARY_PATH = DATA_DIR / "latest_display_awake_summary.json"
DISPLAY_AWAKE_EVENTS_PATH = DATA_DIR / "display_awake_events.jsonl"
DISPLAY_AWAKE_OVERRIDE_PATH = DATA_DIR / "display_awake_override.json"
ACTION_AUDIT_PATH = LOG_DIR / "actions.audit.jsonl"
ENERGY_REFRESH_STATUS_PATH = DATA_DIR / "latest_energy_refresh.json"
ENERGY_REFRESH_LOCK_PATH = DATA_DIR / "refresh_energy.lock"
ACTION_STATUS_PATHS = {
    "check": DATA_DIR / "latest.json",
    "refreshEnergy": ENERGY_REFRESH_STATUS_PATH,
    "refreshSce": SCE_REFRESH_STATUS_PATH,
    "sceApi": SCE_API_STATUS_PATH,
    "reconcileEnergy": ENERGY_RECONCILE_STATUS_PATH,
    "alarmRefresh": ALARM_CACHE_REFRESH_STATUS_PATH,
    "unifiOccupancyRecovery": UNIFI_OCCUPANCY_RECOVERY_STATUS_PATH,
    "garageActivity": GARAGE_LIGHT_HOLD_STATUS_PATH,
    "displayAwake": DISPLAY_AWAKE_STATUS_PATH,
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
SCE_REFRESH_ENERGY_WAIT_SECONDS = 180
SCE_REFRESH_ENERGY_WAIT_POLL_SECONDS = 5


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


def process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_refresh_lock_pid() -> int | None:
    if not ENERGY_REFRESH_LOCK_PATH.exists():
        return None
    raw = ENERGY_REFRESH_LOCK_PATH.read_text().strip().split()
    if not raw:
        return None
    try:
        return int(raw[0])
    except ValueError:
        return None


def summarize_energy_refresh_steps(steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total": len(steps),
        "complete": sum(1 for step in steps if step.get("ok") is True),
        "skipped": sum(1 for step in steps if step.get("skipped") is True),
        "failed": sum(1 for step in steps if step.get("ok") is not True),
    }


def terminal_recorded_energy_refresh_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("status") != "running" or payload.get("finishedAt"):
        return None
    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        return None
    if not all(isinstance(step, dict) and step.get("finishedAt") for step in steps):
        return None
    if steps[-1].get("name") not in {"analyze_energy_automation", "install_homekit_virtual_sensors"}:
        return None

    required_failed = [step for step in steps if not step.get("ok") and not step.get("optional")]
    optional_failed = [step for step in steps if not step.get("ok") and step.get("optional")]
    updated = dict(payload)
    updated.update(
        {
            "ok": not required_failed,
            "status": "failed" if required_failed else "complete",
            "currentStep": None,
            "finishedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "stepSummary": summarize_energy_refresh_steps(steps),
            "requiredFailures": [step["name"] for step in required_failed],
            "optionalFailures": [step["name"] for step in optional_failed],
            "staleRunningRecovered": True,
            "staleRunningRecoveryReason": "terminal_steps_recorded",
        }
    )
    return updated


def recover_stale_energy_refresh_payload(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if path != ENERGY_REFRESH_STATUS_PATH:
        return payload
    if payload.get("status") != "running" or payload.get("finishedAt"):
        return payload
    pid = read_refresh_lock_pid()
    if pid is not None and process_is_running(pid):
        return payload
    updated = terminal_recorded_energy_refresh_payload(payload)
    if updated is None:
        updated = dict(payload)
        updated.update(
            {
                "ok": None,
                "status": "interrupted",
                "currentStep": None,
                "finishedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "staleRunningRecovered": True,
            }
        )
    if pid is not None:
        updated["staleRefreshPid"] = pid
    path.write_text(json.dumps(updated, indent=2, sort_keys=True) + "\n")
    try:
        ENERGY_REFRESH_LOCK_PATH.unlink()
    except FileNotFoundError:
        pass
    return updated


def read_json_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path)}
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return {"exists": True, "path": str(path), "ok": False, "error": f"invalid JSON: {exc}"}

    if not isinstance(payload, dict):
        return {"exists": True, "path": str(path), "ok": False, "error": "status file is not a JSON object"}
    payload = recover_stale_energy_refresh_payload(path, payload)

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
        "staleRunningRecovered",
        "staleRefreshPid",
        "supersededBy",
        "currentStaleCount",
        "action",
        "classification",
        "reason",
        "blockedBy",
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


def current_alarm_cache_stale_count() -> int | None:
    return alarm_cache_stale_count()


def normalize_action_statuses(actions: dict[str, dict[str, Any]]) -> None:
    reconcile = actions.get("reconcileEnergy")
    refresh = actions.get("refreshEnergy")
    if isinstance(reconcile, dict) and isinstance(refresh, dict) and reconcile_was_superseded_by_refresh(reconcile, refresh):
        reconcile["ok"] = True
        reconcile["status"] = "superseded"
        reconcile["supersededBy"] = "refreshEnergy"

    unifi_recovery = actions.get("unifiOccupancyRecovery")
    if (
        isinstance(unifi_recovery, dict)
        and unifi_recovery.get("ok") is False
        and unifi_recovery.get("action") == "none"
        and unifi_recovery.get("classification") in {"api", "auth", "login_unavailable"}
    ):
        unifi_recovery["ok"] = True
        unifi_recovery["status"] = "blocked"
        unifi_recovery["blockedBy"] = "unifi_api"

    alarm_refresh = actions.get("alarmRefresh")
    if not isinstance(alarm_refresh, dict):
        return
    failed = alarm_refresh.get("ok") is False
    stale_running = (
        alarm_refresh.get("status") == "running"
        and (source_age_hours(alarm_refresh.get("startedAt")) or 0) >= 0.5
    )
    if not failed and not stale_running:
        return
    current_stale = current_alarm_cache_stale_count()
    if current_stale != 0:
        return
    alarm_refresh["ok"] = True
    alarm_refresh["status"] = "superseded"
    alarm_refresh["supersededBy"] = "currentAlarmCacheComparison"
    alarm_refresh["currentStaleCount"] = current_stale


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


def alarm_energy_capture_at(alarm: dict[str, Any]) -> str | None:
    energy = alarm.get("energy") if isinstance(alarm.get("energy"), dict) else {}
    return alarm.get("capturedAtLocal") or energy.get("capturedAtLocal")


def operational_source_status() -> list[dict[str, Any]]:
    sce = load_json_file(SCE_API_STATUS_PATH)
    chargepoint = load_json_file(DATA_DIR / "latest_chargepoint_refresh.json")
    alarm = load_json_file(ROOT / "config" / "alarm_energy_readings.json") or load_json_file(DATA_DIR / "latest_alarm_com.json")
    alarm_capture = alarm_energy_capture_at(alarm)
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
            "status": "fresh" if alarm_capture and (source_age_hours(alarm_capture) or 999) < 24 else "stale",
            "ageHours": source_age_hours(alarm_capture),
            "detail": alarm_capture,
        },
    ]


def action_status() -> dict[str, Any]:
    actions = {name: read_json_status(path) for name, path in ACTION_STATUS_PATHS.items()}
    normalize_action_statuses(actions)
    if "garageActivity" in actions:
        actions["garageActivity"]["activityReport"] = garage_activity_report(actions["garageActivity"])
    if "displayAwake" in actions:
        display_detail = display_awake_observability()
        actions["displayAwake"]["detail"] = display_detail
        actions["displayAwake"]["ok"] = display_detail.get("ok")
        actions["displayAwake"]["status"] = display_detail.get("status")
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


def normalized_energy_history_days(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 7
    return parsed if parsed in {1, 7, 30, 90} else 7


def energy_observation_history(days: int = 7, max_points: int = 420) -> list[dict[str, Any]]:
    if not DB_PATH.exists():
        return []
    cutoff = (datetime.now(timezone.utc).astimezone() - timedelta(days=days)).isoformat(timespec="seconds")
    try:
        with sqlite3.connect(DB_PATH) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                select captured_at, envoy_production_kw, envoy_site_load_kw, envoy_grid_net_kw,
                       envoy_storage_kw, battery_percent, battery_charging, battery_discharging,
                       sense_load_kw, sense_solar_kw, alarm_mtd_kwh, alarm_projected_kwh,
                       energy_alert_count, active_states_json
                  from energy_observations
                 where captured_at >= ?
                 order by captured_at asc
                """,
                (cutoff,),
            ).fetchall()
    except (sqlite3.Error, OSError):
        return []
    if len(rows) > max_points:
        step = max(1, len(rows) // max_points)
        sampled = list(rows[::step])
        if sampled[-1]["captured_at"] != rows[-1]["captured_at"]:
            sampled.append(rows[-1])
        rows = sampled
    result: list[dict[str, Any]] = []
    for row in rows:
        try:
            states = json.loads(row["active_states_json"] or "[]")
        except json.JSONDecodeError:
            states = []
        result.append(
            {
                "capturedAt": row["captured_at"],
                "envoyProductionKw": row["envoy_production_kw"],
                "envoySiteLoadKw": row["envoy_site_load_kw"],
                "envoyGridNetKw": row["envoy_grid_net_kw"],
                "envoyStorageKw": row["envoy_storage_kw"],
                "batteryPercent": row["battery_percent"],
                "batteryCharging": bool(row["battery_charging"]),
                "batteryDischarging": bool(row["battery_discharging"]),
                "senseLoadKw": row["sense_load_kw"],
                "senseSolarKw": row["sense_solar_kw"],
                "alarmMonthToDateKwh": row["alarm_mtd_kwh"],
                "alarmProjectedKwh": row["alarm_projected_kwh"],
                "energyAlertCount": row["energy_alert_count"],
                "states": states if isinstance(states, list) else [],
            }
        )
    return result


def energy_status(history_days: int = 7) -> dict[str, Any]:
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
    observability = read_json_status(DATA_DIR / "latest_energy_observability.json")
    if isinstance(observability, dict):
        observability = dict(observability)
        observability["dailyComparison"] = filter_daily_energy_rows(
            observability.get("dailyComparison") or [], history_days
        )
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
        "observability": observability,
        "combined": combined,
        "operationalSourceStatus": operational_source_status(),
        "historyDays": history_days,
        "observationHistory": energy_observation_history(history_days),
    }
    combined_payload = load_json_file(DATA_DIR / "latest_combined_energy_monitor.json")
    if combined_payload:
        payload["sourceStatus"] = combined_payload.get("sourceStatus", [])
        payload["alerts"] = combined_payload.get("alerts", [])
        payload["dailySummary"] = combined_payload.get("dailySummary", [])[-10:]
    automation_payload = load_json_file(DATA_DIR / "latest_energy_automation_opportunities.json")
    if automation_payload:
        payload["opportunities"] = automation_payload.get("opportunities", [])
    observability_payload = load_json_file(DATA_DIR / "latest_energy_observability.json")
    if observability_payload:
        observability_payload = dict(observability_payload)
        observability_payload["dailyComparison"] = filter_daily_energy_rows(
            observability_payload.get("dailyComparison") or [], history_days
        )
        payload["observability"] = observability_payload
    return payload


def load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def display_launchd_status() -> dict[str, Any]:
    service = f"gui/{os.getuid()}/com.arkadiy.smart-home-display-awake"
    result = run(["launchctl", "print", service], timeout=10)
    if not result.get("ok"):
        return {"loaded": False, "service": service}
    output = str(result.get("stdout") or "")
    state = re.search(r"\bstate = ([^\n]+)", output)
    pid = re.search(r"\bpid = ([0-9]+)", output)
    runs = re.search(r"\bruns = ([0-9]+)", output)
    exit_code = re.search(r"\blast exit code = (-?[0-9]+)", output)
    return {
        "loaded": True,
        "service": service,
        "state": state.group(1).strip() if state else None,
        "pid": int(pid.group(1)) if pid else None,
        "runs": int(runs.group(1)) if runs else None,
        "lastExitCode": int(exit_code.group(1)) if exit_code else None,
    }


def display_awake_observability() -> dict[str, Any]:
    status = load_json_file(DISPLAY_AWAKE_STATUS_PATH)
    summary = load_json_file(DISPLAY_AWAKE_SUMMARY_PATH)
    recent_events = read_jsonl_tail(DISPLAY_AWAKE_EVENTS_PATH, 30)
    generated_at = status.get("generatedAt")
    age_hours = source_age_hours(generated_at)
    age_seconds = round(age_hours * 3600, 1) if age_hours is not None else None
    display_config = load_config().get("display_awake")
    poll_seconds = int(display_config.get("poll_seconds") or 30) if isinstance(display_config, dict) else 30
    stale_after_seconds = max(120, poll_seconds * 4)
    missing = not bool(status)
    stale = age_seconds is None or age_seconds > stale_after_seconds
    unifi_ok = (status.get("unifi") or {}).get("ok") if isinstance(status.get("unifi"), dict) else None
    launchd = display_launchd_status()
    health = status.get("health") if isinstance(status.get("health"), dict) else {}
    if missing:
        overall = "missing"
    elif stale:
        overall = "stale"
    elif not launchd.get("loaded"):
        overall = "service_unloaded"
    elif unifi_ok is False or health.get("status") == "degraded":
        overall = "degraded"
    elif health.get("status") == "setup_required":
        overall = "setup_required"
    else:
        overall = str(status.get("status") or "healthy")
    ok = overall not in {"missing", "stale", "service_unloaded", "degraded"}
    return {
        "ok": ok,
        "status": overall,
        "generatedAt": generated_at,
        "ageSeconds": age_seconds,
        "staleAfterSeconds": stale_after_seconds,
        "mode": status.get("mode"),
        "health": health,
        "unifi": status.get("unifi"),
        "pollSeconds": poll_seconds,
        "launchd": launchd,
        "enrollment": status.get("enrollment"),
        "mappingConfigured": status.get("mappingConfigured"),
        "presence": status.get("presence"),
        "manualOverride": status.get("manualOverride"),
        "lights": status.get("lights"),
        "targets": status.get("targets"),
        "summary": summary,
        "recentEvents": recent_events,
    }


def html_escape(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def display_target_name(target_id: str) -> str:
    names = {
        "m2-office-mini": "M2 Office Mini",
        "m2-garage-mini": "M2 Garage Mini",
        "m4-bar-mini": "M4 Bar Mini",
        "m4-office-mini": "M4 Office Mini",
        "m2-macbook-pro": "M2 MacBook Pro",
    }
    return names.get(target_id, target_id.replace("-", " ").title())


def display_duration(value: Any) -> str:
    try:
        seconds = max(0, int(float(value)))
    except (TypeError, ValueError):
        return "—"
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def display_reason_label(reason: Any) -> str:
    labels = {
        "presence_room": "Presence matches room",
        "recent_activity": "Recent keyboard or mouse activity",
        "light_plus_activity": "Mapped light plus recent activity",
        "manual_override": "Manual override",
        "unreachable": "Unreachable",
        "logged_out": "Logged out",
        "locked": "Locked",
        "battery_power": "Running on battery",
        "lid_closed": "Lid closed",
    }
    key = str(reason)
    return labels.get(key, key.replace("_", " ").capitalize())


def render_display_page(*, read_only: bool = False) -> bytes:
    observability = display_awake_observability()
    targets = observability.get("targets") if isinstance(observability.get("targets"), dict) else {}
    summary = observability.get("summary") if isinstance(observability.get("summary"), dict) else {}
    summary_targets = summary.get("targets") if isinstance(summary.get("targets"), dict) else {}
    would_hold_count = sum(1 for item in targets.values() if isinstance(item, dict) and item.get("wouldHold"))
    predicted_total = sum(
        float((summary_targets.get(target_id) or {}).get("predictedHoldSeconds") or 0)
        for target_id in targets
        if isinstance(summary_targets.get(target_id), dict)
    )
    lease_total = sum(
        float((summary_targets.get(target_id) or {}).get("leaseActiveSeconds") or 0)
        for target_id in targets
        if isinstance(summary_targets.get(target_id), dict)
    )
    target_cards: list[str] = []
    for target_id, item in targets.items():
        if not isinstance(item, dict):
            continue
        probe = item.get("probe") if isinstance(item.get("probe"), dict) else {}
        totals = summary_targets.get(target_id) if isinstance(summary_targets.get(target_id), dict) else {}
        reasons = item.get("reasons") or []
        ineligible = item.get("ineligibleReasons") or []
        badges = "".join(
            f"<span class='reason'>{html_escape(display_reason_label(reason))}</span>"
            for reason in (reasons or ineligible)
        ) or "<span class='reason muted'>No active hold reason</span>"
        reachable = probe.get("reachable") is True
        eligible = item.get("eligible") is True
        would_hold = item.get("wouldHold") is True
        decision = "Would hold" if would_hold else "Release"
        decision_class = "good" if would_hold else "quiet"
        machine_meta = f"{item.get('room') or 'unmapped'} · {'reachable' if reachable else 'unreachable'}"
        power = "AC" if probe.get("onAcPower") is True else "battery" if probe.get("onAcPower") is False else "unknown"
        lid = "closed" if probe.get("lidClosed") is True else "open" if probe.get("lidClosed") is False else "unknown"
        target_cards.append(
            f"""<details class="machine">
<summary><span class="machine-grid">
  <span><span class="machine-name">{html_escape(display_target_name(str(target_id)))}</span><br><span class="muted tiny">{html_escape(machine_meta)}</span></span>
  <span><span class="muted tiny">Idle</span><br><strong>{html_escape(display_duration(probe.get('idleSeconds')))}</strong></span>
  <span><span class="muted tiny">Eligible</span><br><strong class="{'good' if eligible else 'quiet'}">{'Yes' if eligible else 'No'}</strong></span>
  <span><span class="muted tiny">Decision</span><br><strong class="{decision_class}">{decision}</strong></span>
  <span class="muted tiny reason-summary">{html_escape(', '.join(str(value) for value in (reasons or ineligible)) or 'no active hold reason')}</span>
</span></summary>
<div class="machine-detail">
  <div><span class="muted tiny">Why</span><div class="reason-list">{badges}</div></div>
  <div class="facts compact">
    <div><span class="muted tiny">Session</span><strong>{'locked' if probe.get('locked') else 'unlocked'}</strong></div>
    <div><span class="muted tiny">Power / lid</span><strong>{html_escape(power)} / {html_escape(lid)}</strong></div>
    <div><span class="muted tiny">Mapped light</span><strong>{'on' if item.get('lightOn') is True else 'off' if item.get('lightOn') is False else 'none'}</strong></div>
    <div><span class="muted tiny">Predicted today</span><strong>{html_escape(display_duration(totals.get('predictedHoldSeconds') or 0))}</strong></div>
    <div><span class="muted tiny">Lease time today</span><strong>{html_escape(display_duration(totals.get('leaseActiveSeconds') or 0))}</strong></div>
    <div><span class="muted tiny">Transitions</span><strong>{html_escape(totals.get('wouldHoldTransitions') or 0)}</strong></div>
  </div>
</div></details>"""
        )
    target_markup = "\n".join(target_cards) or "<div class='empty'>No display-manager status has been recorded.</div>"
    recent = observability.get("recentEvents") if isinstance(observability.get("recentEvents"), list) else []
    event_cards: list[str] = []
    for item in reversed(recent[-12:]):
        if not isinstance(item, dict):
            continue
        event_targets = item.get("targets") if isinstance(item.get("targets"), dict) else {}
        holding = [display_target_name(str(key)) for key, value in event_targets.items() if isinstance(value, dict) and value.get("wouldHold")]
        blocked = [display_target_name(str(key)) for key, value in event_targets.items() if isinstance(value, dict) and value.get("ineligibleReasons")]
        room = (item.get("presence") or {}).get("confirmedRoom") if isinstance(item.get("presence"), dict) else None
        source = (item.get("presence") or {}).get("source") if isinstance(item.get("presence"), dict) else None
        detail_parts = [f"Holding: {', '.join(holding)}" if holding else "Holding: none"]
        if blocked:
            detail_parts.append(f"Ineligible: {', '.join(blocked)}")
        event_cards.append(
            "<li>"
            f"<span class='event-dot'></span><div><strong>{html_escape(room or 'No confirmed room')}</strong>"
            f" <span class='muted'>via {html_escape(source or 'none')}</span><br>"
            f"<span class='tiny'>{html_escape(' · '.join(detail_parts))}</span></div>"
            f"<time>{html_escape(item.get('timestamp'))}</time></li>"
        )
    event_markup = "\n".join(event_cards) or "<li class='empty'>No decision changes recorded.</li>"
    presence = observability.get("presence") if isinstance(observability.get("presence"), dict) else {}
    devices = presence.get("devices") if isinstance(presence.get("devices"), dict) else {}
    watch = devices.get("watch") if isinstance(devices.get("watch"), dict) else {}
    iphone = devices.get("iphone") if isinstance(devices.get("iphone"), dict) else {}
    unifi = observability.get("unifi") if isinstance(observability.get("unifi"), dict) else {}
    launchd = observability.get("launchd") if isinstance(observability.get("launchd"), dict) else {}
    status = str(observability.get("status") or "missing")
    status_class = "good" if observability.get("ok") else "warn"
    mode = str(observability.get("mode") or "not running")
    shadow_note = (
        "Shadow mode records these decisions but launches no caffeinate leases."
        if mode == "shadow"
        else "Enforcement is active; every hold still passes the lock, power, lid, and reachability gates."
        if mode == "enforce"
        else "The controller has not produced status yet."
    )
    controls_markup = (
        "<div class=\"notice\">Read-only phone view. Display controls remain available only on the local controller.</div>"
        "<div class=\"actions\"><button class=\"tertiary\" id=\"refresh\" type=\"button\">Refresh status</button></div>"
        if read_only
        else "<div class=\"actions\"><button data-action=\"/action/screens-awake\">Screens Awake</button>"
        "<button class=\"secondary\" data-action=\"/action/screens-auto\">Screens Auto</button>"
        "<button class=\"tertiary\" id=\"refresh\" type=\"button\">Refresh status</button></div>"
    )
    footer_markup = (
        '<p class="footer">Read-only JSON: <a href="/status/displays">/status/displays</a></p>'
        if read_only
        else '<p class="footer">JSON: <a href="/status/displays">/status/displays</a> · <a href="/energy">Energy dashboard</a></p>'
    )
    action_script = "" if read_only else """
document.querySelectorAll('button[data-action]').forEach((button)=>button.addEventListener('click',async()=>{
  button.disabled=true;
  try {
    const response=await fetch(button.dataset.action,{method:'POST'});
    const payload=await response.json();
    document.getElementById('result').textContent=payload.ok ? 'Action accepted. Refresh status to see the next controller decision.' : JSON.stringify(payload);
  } catch(error) { document.getElementById('result').textContent=String(error); }
  finally { button.disabled=false; }
}));
"""
    body = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Display Observability</title><style>
* {{ box-sizing:border-box; }}
:root {{ color-scheme:light dark; --bg:#f8fafc; --panel:#fff; --panel2:#f1f5f9; --text:#172033; --muted:#64748b; --border:#dbe3ec; --accent:#0f766e; --good:#15803d; --warn:#b45309; }}
@media (prefers-color-scheme:dark) {{ :root {{ --bg:#0b1120; --panel:#111827; --panel2:#182235; --text:#e5edf7; --muted:#94a3b8; --border:#2b3a50; --accent:#5eead4; --good:#4ade80; --warn:#fbbf24; }} }}
body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
.shell {{ width:min(1100px,100%); margin:0 auto; padding:28px 20px 48px; display:grid; gap:14px; }}
h1,h2,p {{ margin:0; }} h1 {{ font-size:22px; }} h2 {{ font-size:15px; margin-top:8px; }}
.topline,.presence,.summary-grid,.machine-grid,.machine-detail,.facts,.actions,.event-list li {{ display:flex; align-items:center; justify-content:space-between; gap:12px; }}
.topline,.actions,.facts {{ flex-wrap:wrap; }} .muted {{ color:var(--muted); }} .tiny {{ font-size:12px; }}
.badge,.reason {{ display:inline-flex; align-items:center; gap:6px; border:1px solid var(--border); border-radius:999px; padding:4px 9px; background:var(--panel2); white-space:nowrap; }}
.dot,.event-dot {{ width:8px; height:8px; border-radius:50%; background:var(--muted); flex:0 0 auto; }} .dot.good,.event-dot {{ background:var(--good); }} .dot.warn {{ background:var(--warn); }}
.good {{ color:var(--good); }} .quiet {{ color:var(--muted); }} .warn {{ color:var(--warn); }}
.presence {{ padding:13px 15px; border:1px solid var(--border); border-radius:12px; background:var(--panel2); }}
.presence-main {{ display:flex; align-items:center; gap:11px; }} .presence-icon {{ display:grid; place-items:center; width:34px; height:34px; border:1px solid var(--border); border-radius:50%; color:var(--accent); font-size:19px; }}
.facts {{ justify-content:flex-start; }} .facts > div {{ min-width:92px; }} .facts strong {{ display:block; font-size:13px; }}
.summary-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); align-items:stretch; }}
.summary-card {{ padding:12px 14px; border:1px solid var(--border); border-radius:12px; background:var(--panel); }} .summary-card strong {{ display:block; font-size:20px; margin-top:2px; }}
.actions {{ justify-content:flex-start; }} button {{ appearance:none; border:1px solid var(--accent); background:var(--accent); color:var(--bg); border-radius:8px; padding:9px 12px; font:inherit; font-weight:600; cursor:pointer; }}
button.secondary {{ background:transparent; color:var(--accent); }} button.tertiary {{ border-color:var(--border); background:var(--panel); color:var(--text); }} button:disabled {{ opacity:.55; cursor:wait; }}
.notice,.empty {{ padding:11px 13px; border:1px solid var(--border); border-radius:10px; background:var(--panel2); color:var(--muted); }}
.machine-list {{ display:grid; gap:7px; }} .machine {{ border:1px solid var(--border); border-radius:10px; background:var(--panel); overflow:hidden; }}
.machine summary {{ padding:11px 12px; cursor:pointer; list-style:none; }} .machine summary::-webkit-details-marker {{ display:none; }} .machine[open] {{ border-color:var(--accent); box-shadow:inset 3px 0 0 var(--accent); }}
.machine-grid {{ display:grid; grid-template-columns:minmax(160px,1.35fr) repeat(3,minmax(82px,.65fr)) minmax(160px,1.3fr); align-items:center; }} .machine-name {{ font-weight:700; }}
.machine-detail {{ align-items:flex-start; padding:13px 15px 15px; border-top:1px solid var(--border); background:var(--panel2); }} .machine-detail > div:first-child {{ min-width:230px; }}
.facts.compact {{ display:grid; grid-template-columns:repeat(3,minmax(95px,1fr)); flex:1; }} .reason-list {{ display:flex; gap:6px; flex-wrap:wrap; margin-top:7px; }} .reason {{ background:var(--panel); font-size:12px; }}
.event-list {{ list-style:none; margin:0; padding:0; border:1px solid var(--border); border-radius:10px; background:var(--panel); overflow:hidden; }} .event-list li {{ justify-content:flex-start; padding:10px 12px; border-bottom:1px solid var(--border); }} .event-list li:last-child {{ border-bottom:0; }} .event-list li div {{ flex:1; }} time {{ color:var(--muted); font-size:11px; text-align:right; max-width:220px; }}
.footer {{ color:var(--muted); font-size:12px; }} a {{ color:var(--accent); }} #result {{ min-height:20px; white-space:pre-wrap; }}
@media (max-width:720px) {{ .summary-grid {{ grid-template-columns:1fr; }} .machine-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} .machine-grid > :first-child,.machine-grid > :last-child {{ grid-column:1/-1; }} .machine-detail {{ display:grid; }} .facts.compact {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} .presence {{ align-items:flex-start; }} }}
@media (max-width:430px) {{ .shell {{ padding:18px 12px 36px; }} .presence {{ display:grid; }} .facts.compact {{ grid-template-columns:1fr 1fr; }} time {{ display:none; }} }}
</style></head><body>
<main class="shell">
  <header class="topline"><div><h1>Display Awake</h1><p class="muted tiny">Updated {html_escape(observability.get('generatedAt') or 'never')} · age {html_escape(display_duration(observability.get('ageSeconds')))}</p></div><span class="badge"><span class="dot {status_class}"></span>{html_escape(mode)} · {html_escape(status)}</span></header>
  <section class="presence"><div class="presence-main"><div class="presence-icon">⌁</div><div><span class="muted tiny">Confirmed presence</span><strong>{html_escape(presence.get('confirmedRoom') or 'No confirmed room')}</strong></div></div><div class="facts"><div><span class="muted tiny">Source</span><strong>{html_escape(presence.get('source') or 'none')}</strong></div><div><span class="muted tiny">Watch</span><strong>{'fresh' if watch.get('fresh') else 'not fresh'} · {html_escape(display_duration(watch.get('ageSeconds')))}</strong></div><div><span class="muted tiny">iPhone</span><strong>{'fresh' if iphone.get('fresh') else 'not fresh'} · {html_escape(display_duration(iphone.get('ageSeconds')))}</strong></div><div><span class="muted tiny">Override</span><strong>{'Manual' if observability.get('manualOverride') else 'Auto'}</strong></div></div></section>
  <section class="summary-grid"><div class="summary-card"><span class="muted tiny">Would hold now</span><strong>{would_hold_count} of {len(targets)}</strong></div><div class="summary-card"><span class="muted tiny">Predicted / actual</span><strong>{html_escape(display_duration(predicted_total))} / {html_escape(display_duration(lease_total))}</strong></div><div class="summary-card"><span class="muted tiny">UniFi / controller</span><strong>{'healthy' if unifi.get('ok') is True else 'unavailable'} · {'loaded' if launchd.get('loaded') else 'unloaded'}</strong><span class="muted tiny">poll {html_escape(observability.get('pollSeconds'))}s</span></div></section>
  <div class="notice">{html_escape(shadow_note)}</div>
  {controls_markup}
  <div id="result" class="muted tiny"></div>
  <h2>Mac decisions</h2><section class="machine-list">{target_markup}</section>
  <h2>Recent decision changes</h2><ol class="event-list">{event_markup}</ol>
  {footer_markup}
</main>
<script>
{action_script}
document.getElementById('refresh').addEventListener('click',()=>location.reload());
</script>
</body></html>"""
    return body.encode()


def display_energy_value(value: Any, suffix: str = "", digits: int = 1) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    return f"{value:.{digits}f}{suffix}"


def filter_daily_energy_rows(rows: list[dict[str, Any]], days: int, now: datetime | None = None) -> list[dict[str, Any]]:
    fallback_date = (now or datetime.now(timezone.utc).astimezone()).date()
    sce_dates: list[Any] = []
    for row in rows:
        if not isinstance(row.get("sceDeliveredKwh"), (int, float)):
            continue
        try:
            sce_dates.append(datetime.fromisoformat(str(row.get("date"))).date())
        except (TypeError, ValueError):
            continue
    range_end = max(sce_dates) if sce_dates else fallback_date
    cutoff = range_end - timedelta(days=days - 1)
    filtered: list[dict[str, Any]] = []
    for row in rows:
        try:
            row_date = datetime.fromisoformat(str(row.get("date"))).date()
        except (TypeError, ValueError):
            continue
        if cutoff <= row_date <= range_end:
            filtered.append(row)
    return filtered


def energy_line_chart(
    title: str,
    subtitle: str,
    rows: list[dict[str, Any]],
    x_key: str,
    series: list[tuple[str, str, str]],
    unit: str,
) -> str:
    width, height = 920, 300
    left, right, top, bottom = 58, 20, 58, 46
    values = [
        float(row[key])
        for row in rows
        for key, _label, _color in series
        if isinstance(row.get(key), (int, float))
    ]
    if not values or not rows:
        return f"<div class='empty'><strong>{html_escape(title)}</strong><br>No observations yet.</div>"
    low = min(0.0, min(values))
    high = max(values)
    if high <= low:
        high = low + 1
    pad = (high - low) * 0.08
    low -= pad
    high += pad
    plot_w = width - left - right
    plot_h = height - top - bottom

    def x_pos(index: int) -> float:
        return left + (plot_w * index / max(1, len(rows) - 1))

    def y_pos(value: float) -> float:
        return top + (high - value) * plot_h / (high - low)

    parts = [
        f"<svg class='chart' viewBox='0 0 {width} {height}' role='img' aria-label='{html_escape(title)}'>",
        f"<text x='18' y='25' class='chart-title'>{html_escape(title)}</text>",
        f"<text x='18' y='43' class='chart-subtitle'>{html_escape(subtitle)}</text>",
    ]
    for tick in range(5):
        value = low + (high - low) * tick / 4
        y = y_pos(value)
        parts.append(f"<line x1='{left}' y1='{y:.1f}' x2='{width-right}' y2='{y:.1f}' class='gridline'/>")
        parts.append(f"<text x='{left-7}' y='{y+4:.1f}' text-anchor='end' class='axis'>{value:.1f}{html_escape(unit)}</text>")
    for key, label, color in series:
        points = [
            f"{x_pos(index):.1f},{y_pos(float(row[key])):.1f}"
            for index, row in enumerate(rows)
            if isinstance(row.get(key), (int, float))
        ]
        if len(points) >= 2:
            parts.append(f"<polyline points='{' '.join(points)}' fill='none' stroke='{color}' stroke-width='2.2'/>")
        elif points:
            x, y = points[0].split(",")
            parts.append(f"<circle cx='{x}' cy='{y}' r='3.5' fill='{color}'/>")
    label_indexes = sorted({0, len(rows) // 2, len(rows) - 1})
    for index in label_indexes:
        label = str(rows[index].get(x_key) or "")
        if "T" in label:
            label = label.replace("T", " ")[:16]
        else:
            label = label[-5:]
        parts.append(f"<text x='{x_pos(index):.1f}' y='{height-15}' text-anchor='middle' class='axis'>{html_escape(label)}</text>")
    legend_x = left
    for _key, label, color in series:
        parts.append(f"<line x1='{legend_x}' y1='{height-34}' x2='{legend_x+18}' y2='{height-34}' stroke='{color}' stroke-width='3'/>")
        parts.append(f"<text x='{legend_x+23}' y='{height-30}' class='legend'>{html_escape(label)}</text>")
        legend_x += max(120, len(label) * 8 + 45)
    parts.append("</svg>")
    return "".join(parts)


def render_energy_page(history_days: int = 7) -> bytes:
    status = energy_status(history_days)
    observability = status.get("observability") or {}
    live = observability.get("live") or {}
    quality = observability.get("quality") or {}
    daily = filter_daily_energy_rows(observability.get("dailyComparison") or [], history_days)
    history = status.get("observationHistory") or []
    sources = observability.get("sourceStatus") or status.get("sourceStatus") or status.get("operationalSourceStatus") or []
    refresh = status.get("refresh") or {}
    peak_events = observability.get("peakEvents") or []
    semantics = quality.get("sourceSemantics") or []
    coverage_fields = (
        ("SCE", "sceDeliveredKwh"),
        ("Alarm.com", "alarmClampKwh"),
        ("Envoy", "envoySiteLoadKwh"),
        ("Sense", "senseLoadKwh"),
    )
    coverage_counts = {
        label: sum(isinstance(row.get(field), (int, float)) for row in daily)
        for label, field in coverage_fields
    }
    comparable_range_days = sum(int(row.get("availableSourceCount") or 0) >= 3 for row in daily)
    range_quality_status = "complete" if daily and all(count == len(daily) for count in coverage_counts.values()) else "limited"

    range_label = "1 day" if history_days == 1 else f"{history_days} days"
    sce_delivered_total = sum(float(row.get("sceDeliveredKwh") or 0) for row in daily)
    sce_received_total = sum(float(row.get("sceReceivedKwh") or 0) for row in daily)
    sce_net_total = sum(float(row.get("sceNetImportKwh") or 0) for row in daily)
    sce_net_daily_average = sce_net_total / len(daily) if daily else None
    peak_sce_row = max(
        (row for row in daily if isinstance(row.get("sceDeliveredKwh"), (int, float))),
        key=lambda row: float(row.get("sceDeliveredKwh") or 0),
        default=None,
    )
    range_cards = [
        ("Grid delivered", display_energy_value(sce_delivered_total, " kWh"), range_label),
        ("Grid exported", display_energy_value(sce_received_total, " kWh"), range_label),
        ("Net grid import", display_energy_value(sce_net_total, " kWh"), range_label),
        ("Average net / day", display_energy_value(sce_net_daily_average, " kWh"), f"{len(daily)} completed {'day' if len(daily) == 1 else 'days'}"),
        (
            "Peak import day",
            display_energy_value((peak_sce_row or {}).get("sceDeliveredKwh"), " kWh"),
            str((peak_sce_row or {}).get("date") or "No SCE data"),
        ),
    ]
    range_card_markup = "".join(
        f"<div class='card range-card'><span>{html_escape(label)}</span><strong>{html_escape(value)}</strong><small>{html_escape(note)}</small></div>"
        for label, value, note in range_cards
    )

    cards = [
        ("Solar production", display_energy_value(live.get("envoyProductionKw"), " kW"), "Envoy live"),
        ("Total site load", display_energy_value(live.get("envoySiteLoadKw"), " kW"), "Includes storage effects"),
        ("Grid net", display_energy_value(live.get("envoyGridNetKw"), " kW", 2), "Positive import; negative export"),
        ("Battery", display_energy_value(live.get("batteryPercent"), "%", 0), "Charging" if live.get("batteryCharging") else "Discharging" if live.get("batteryDischarging") else "Idle"),
        ("Sense house load", display_energy_value(live.get("senseLoadKw"), " kW", 2), "Non-battery load"),
        ("Alarm projection", display_energy_value(live.get("alarmProjectedKwh"), " kWh", 0), f"Budget {display_energy_value(live.get('alarmBudgetKwh'), ' kWh', 0)}"),
    ]
    card_markup = "".join(
        f"<div class='card'><span>{html_escape(label)}</span><strong>{html_escape(value)}</strong><small>{html_escape(note)}</small></div>"
        for label, value, note in cards
    )
    source_rows = "".join(
        "<tr>"
        f"<td>{html_escape(item.get('source'))}</td>"
        f"<td><span class='pill {html_escape(item.get('status'))}'>{html_escape(item.get('status'))}</span></td>"
        f"<td>{html_escape(item.get('detail'))}</td>"
        f"<td>{html_escape(round(float(item.get('ageHours')), 2) if isinstance(item.get('ageHours'), (int, float)) else item.get('ageDays'))}</td>"
        "</tr>"
        for item in sources
    )
    semantics_rows = "".join(
        f"<tr><td>{html_escape(item.get('source'))}</td><td>{html_escape(item.get('measurement'))}</td><td>{html_escape(item.get('use'))}</td></tr>"
        for item in semantics
    )
    peak_rows = "".join(
        "<tr>"
        f"<td>{html_escape(str(item.get('start') or '').replace('T', ' ')[:16])}</td>"
        f"<td>{html_escape(display_energy_value(item.get('sceImportKw'), ' kW'))}</td>"
        f"<td>{html_escape(display_energy_value(item.get('envoySiteLoadKw'), ' kW'))}</td>"
        f"<td>{html_escape(display_energy_value(item.get('senseLoadKw'), ' kW'))}</td>"
        "</tr>"
        for item in peak_events
    ) or "<tr><td colspan='4'>No overlapping interval events yet.</td></tr>"
    range_quality_row = (
        "<li><strong>Selected-range source coverage</strong>: "
        + ", ".join(f"{html_escape(label)} {count}/{len(daily)} days" for label, count in coverage_counts.items())
        + f"; three-or-more-source comparison {comparable_range_days}/{len(daily)} days.</li>"
    )
    quality_rows = range_quality_row + "".join(
        f"<li><strong>{html_escape(item.get('title'))}</strong>: {html_escape(item.get('detail'))}</li>"
        for item in quality.get("issues") or []
    ) or "<li>Freshness, overlap, and daily reconciliation checks pass.</li>"
    live_window_start = str(history[0].get("capturedAt") or "") if history else ""
    live_window_end = str(history[-1].get("capturedAt") or "") if history else ""
    live_window_detail = (
        f"{len(history)} five-minute samples from {live_window_start.replace('T', ' ')[:16]} to {live_window_end.replace('T', ' ')[:16]}."
        if history
        else "No five-minute samples in the selected period."
    )
    live_chart = energy_line_chart(
        "Live energy flow — collected observation window",
        f"{live_window_detail} Positive grid values are imports.",
        history,
        "capturedAt",
        [
            ("envoyProductionKw", "Solar", "#d19a00"),
            ("envoySiteLoadKw", "Site load", "#2563eb"),
            ("envoyGridNetKw", "Grid net", "#dc2626"),
            ("senseLoadKw", "Sense load", "#7c3aed"),
        ],
        "",
    )
    load_chart = energy_line_chart(
        f"Daily gross-load comparison — {history_days} day{'s' if history_days != 1 else ''}",
        "Alarm.com, Sense, and Envoy are complementary load views, not interchangeable meters.",
        daily,
        "date",
        [
            ("alarmClampKwh", "Alarm clamp", "#ea580c"),
            ("senseLoadKwh", "Sense load", "#7c3aed"),
            ("envoySiteLoadKwh", "Envoy site", "#2563eb"),
        ],
        "",
    )
    grid_chart = energy_line_chart(
        f"Daily utility grid exchange — {history_days} day{'s' if history_days != 1 else ''}",
        "SCE Green Button intervals are authoritative for delivered and received utility energy.",
        daily,
        "date",
        [
            ("sceDeliveredKwh", "Delivered", "#dc2626"),
            ("sceReceivedKwh", "Received", "#16a34a"),
            ("sceNetImportKwh", "Net import", "#0f766e"),
        ],
        "",
    )
    range_links = " ".join(
        f"<a class='range {'active' if history_days == days else ''}' href='/energy?days={days}'>{label}</a>"
        for days, label in ((1, "1d"), (7, "7d"), (30, "30d"), (90, "90d"))
    )
    history_start = history[0].get("capturedAt") if history else None
    range_summary = (
        f"Selected window: {len(daily)} completed SCE days through {daily[-1].get('date') if daily else 'n/a'} "
        f"and {len(history)} five-minute live samples. "
        f"Live sampling began {history_start or 'today'}; retained utility history fills the longer views. "
        + "Coverage: "
        + ", ".join(f"{label} {count}/{len(daily)}" for label, count in coverage_counts.items())
        + "."
    )
    body = f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Smart Home Energy Observability</title><style>
:root {{ --bg:#f6f8fb; --panel:#fff; --ink:#172033; --muted:#657386; --line:#dce3eb; --accent:#0f766e; }}
* {{ box-sizing:border-box }} body {{ margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif }}
main {{ width:min(1180px,100%);margin:auto;padding:28px 20px 52px }} h1 {{ margin:0;font-size:28px }} h2 {{ margin:0 0 8px;font-size:18px }} .muted,small {{ color:var(--muted) }}
.top,.actions,.ranges {{ display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap }} .actions {{ justify-content:flex-start;margin:18px 0 }}
button,.range {{ border:1px solid var(--accent);border-radius:8px;padding:8px 11px;background:var(--accent);color:white;text-decoration:none;cursor:pointer }} button.secondary,.range {{ background:var(--panel);color:var(--accent) }} .range.active {{ background:var(--accent);color:white }}
.range-overview {{ margin:18px 0 }} .range-heading {{ display:flex;align-items:end;justify-content:space-between;gap:10px;margin-bottom:9px;flex-wrap:wrap }} .range-heading h2 {{ font-size:21px;margin:0 }}
.range-cards {{ display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px }} .range-card {{ border-top:3px solid var(--accent) }}
.cards {{ display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:10px 0 18px }} .card,.panel {{ background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px }}
.card span,.card small {{ display:block }} .card strong {{ display:block;font-size:24px;margin:5px 0 }} .grid {{ display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px }} .wide {{ grid-column:1/-1 }}
.chart {{ width:100%;height:auto;display:block }} .chart-title {{ font-size:17px;font-weight:700;fill:var(--ink) }} .chart-subtitle,.axis,.legend {{ font-size:10px;fill:var(--muted) }} .gridline {{ stroke:var(--line);stroke-width:1 }}
table {{ width:100%;border-collapse:collapse }} th,td {{ padding:8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top }} th {{ font-size:11px;text-transform:uppercase;color:var(--muted) }}
.pill {{ border-radius:999px;padding:3px 7px;background:#e2e8f0 }} .pill.fresh,.pill.complete {{ background:#dcfce7;color:#166534 }} .pill.stale,.pill.failed,.pill.missing {{ background:#fee2e2;color:#991b1b }}
.empty {{ min-height:180px;display:grid;place-content:center;text-align:center;color:var(--muted) }} code {{ background:#eef2f7;padding:2px 4px;border-radius:4px }}
@media(max-width:980px) {{ .range-cards {{ grid-template-columns:repeat(2,minmax(0,1fr)) }} }}
@media(max-width:820px) {{ .cards,.grid {{ grid-template-columns:1fr }} .wide {{ grid-column:auto }} }} @media(max-width:520px) {{ main {{ padding:20px 12px 40px }} .range-cards {{ grid-template-columns:1fr }} }}
</style></head><body><main>
<header class='top'><div><h1>Smart Home Energy</h1><div class='muted'>Updated {html_escape(observability.get('generatedAt') or status.get('generatedAt'))} · source quality {html_escape(quality.get('status') or 'collecting')} · selected range {range_quality_status}</div></div><div class='ranges'>{range_links}</div></header>
<section class='range-overview' id='selected-range' data-days='{history_days}'><div class='range-heading'><h2>Selected period · {html_escape(range_label)}</h2><span class='muted'>{html_escape(daily[0].get('date') if daily else 'n/a')} → {html_escape(daily[-1].get('date') if daily else 'n/a')}</span></div><div class='range-cards'>{range_card_markup}</div></section>
<h2>Live now</h2>
<section class='cards'>{card_markup}</section>
<div class='actions'><button data-action='/action/reconcile-energy'>Refresh all</button><button class='secondary' data-action='/action/refresh-sce'>Refresh SCE</button><button class='secondary' data-action='/action/refresh-alarm-cache'>Refresh Alarm.com</button><span id='result' class='muted'></span></div>
<p class='muted'>{html_escape(range_summary)}</p>
<section class='grid'><div class='panel wide primary-chart' id='range-chart'>{grid_chart}</div><div class='panel'>{load_chart}</div><div class='panel'>{live_chart}</div></section>
<section class='grid'><div class='panel'><h2>Source definitions</h2><table><thead><tr><th>Source</th><th>Measures</th><th>Use</th></tr></thead><tbody>{semantics_rows}</tbody></table></div>
<div class='panel'><h2>Data quality</h2><ul>{quality_rows}</ul><p class='muted'>{html_escape(quality.get('overlapPairCount'))} paired SCE/monitor intervals · {html_escape(quality.get('comparableDayCount'))} comparable days.</p></div>
<div class='panel'><h2>Peak 15-minute events</h2><table><thead><tr><th>Start</th><th>SCE import</th><th>Envoy site</th><th>Sense</th></tr></thead><tbody>{peak_rows}</tbody></table></div>
<div class='panel'><h2>Source freshness</h2><table><thead><tr><th>Source</th><th>Status</th><th>Detail</th><th>Age</th></tr></thead><tbody>{source_rows}</tbody></table></div></section>
<p class='muted'>Refresh <span class='pill {html_escape(refresh.get('status'))}'>{html_escape(refresh.get('status') or 'unknown')}</span> · JSON <code>/status/energy?days={history_days}</code> · 90-day local observation retention.</p>
<script>document.querySelectorAll('button[data-action]').forEach(button=>button.addEventListener('click',async()=>{{button.disabled=true;document.getElementById('result').textContent='Requesting '+button.textContent+'…';try{{const response=await fetch(button.dataset.action,{{method:'POST'}});const payload=await response.json();document.getElementById('result').textContent=payload.ok?'Accepted; refresh in a minute.':JSON.stringify(payload)}}catch(error){{document.getElementById('result').textContent=String(error)}}finally{{button.disabled=false}}}}));</script>
</main></body></html>"""
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
    if not isinstance(payload, dict):
        return {}
    return recover_stale_energy_refresh_payload(ENERGY_REFRESH_STATUS_PATH, payload)


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


def wait_for_energy_refresh_idle(
    timeout_seconds: int = SCE_REFRESH_ENERGY_WAIT_SECONDS,
    poll_seconds: int = SCE_REFRESH_ENERGY_WAIT_POLL_SECONDS,
) -> dict[str, Any]:
    started = time.monotonic()
    waited = 0.0
    while True:
        pid = read_refresh_lock_pid()
        if pid is None or not process_is_running(pid):
            return {"ok": True, "waitedSeconds": round(waited, 1), "pid": pid}
        waited = time.monotonic() - started
        if waited >= timeout_seconds:
            return {"ok": False, "waitedSeconds": round(waited, 1), "pid": pid}
        time.sleep(poll_seconds)


def run_sce_refresh_background(started_at: str) -> None:
    try:
        wait_result = wait_for_energy_refresh_idle()
        if wait_result["ok"]:
            result = run(sce_refresh_command(), timeout=600)
        else:
            result = {
                "ok": False,
                "returncode": None,
                "stdout": "",
                "stderr": (
                    "refresh_energy is still running after "
                    f"{wait_result['waitedSeconds']} seconds; pid={wait_result.get('pid')}"
                ),
            }
        latest = latest_energy_refresh_status()
        sce_api = latest_sce_api_status()
        refresh_summary = energy_refresh_summary()
        ok = bool(
            result["ok"]
            and sce_api.get("ok")
            and refresh_summary.get("energyRefreshOk") is not False
            and refresh_summary.get("energyRefreshStatus") != "interrupted"
        )
        write_sce_refresh_status(
            {
                "ok": ok,
                "status": "complete" if ok else "failed",
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
                "waitedForEnergyRefresh": wait_result,
                "sceApiOk": sce_api.get("ok"),
                "sceApiStatus": sce_api.get("status"),
                "sceApiFinishedAt": sce_api.get("finishedAt") or sce_api.get("generatedAt"),
                **refresh_summary,
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


def parse_repair_result(result: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(result.get("stdout") or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def repair_resolved_stale(parsed_repair: dict[str, Any], stale_count: int | None) -> bool:
    if parsed_repair.get("ok") is not True:
        return False
    if parsed_repair.get("skipped"):
        return False
    if not stale_count or stale_count <= 0:
        return True
    changed_count = parsed_repair.get("changedCount")
    if not isinstance(changed_count, int):
        return False
    return changed_count >= stale_count


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
        parsed_repair: dict[str, Any] = {}
        repair_verified = False
        repair_restart_result: dict[str, Any] = {"ok": True, "skipped": True}
        repair_wait_result: dict[str, Any] = {"ok": True, "skipped": True}
        third_capture: dict[str, Any] = {"ok": True, "returncode": None, "stdout": "", "stderr": "", "skipped": True}
        if after_stale and after_stale > 0:
            repair_result = run(alarm_cache_repair_command(), timeout=30)
            parsed_repair = parse_repair_result(repair_result)
            repair_verified = repair_resolved_stale(parsed_repair, after_stale)
            if repair_result["ok"] and parsed_repair.get("changedCount", 0) > 0:
                repair_pid = listening_pid(port)
                if repair_pid is None:
                    repair_restart_result = {"ok": False, "error": f"no Alarm child bridge is listening on port {port}"}
                    repair_wait_result = {"ok": False, "pid": None}
                else:
                    repair_restart_result = terminate(repair_pid)
                    repair_wait_result = wait_for_alarm_child_bridge(port, repair_pid)
                third_capture = run(alarm_cache_refresh_command(), timeout=180) if repair_wait_result.get("ok") else {"ok": False, "returncode": None, "stdout": "", "stderr": "Alarm child bridge did not restart after cache repair"}
                refreshed_stale = alarm_cache_stale_count()
                after_stale = refreshed_stale
                if repair_verified and not third_capture.get("ok"):
                    after_stale = 0
        capture_ok = bool(first_capture["ok"] and second_capture["ok"] and third_capture.get("ok"))
        verification_ok = capture_ok or repair_verified
        ok = bool(
            restart_result.get("ok")
            and wait_result.get("ok")
            and repair_result.get("ok")
            and repair_restart_result.get("ok")
            and repair_wait_result.get("ok")
            and verification_ok
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
                "captureVerified": capture_ok,
                "repairVerified": repair_verified,
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


def append_action_audit(action: str, result: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "requestedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "action": action,
        "ok": bool(result.get("ok")),
        "scheduled": bool(result.get("scheduled")),
        "targetPid": result.get("targetPid"),
        "error": result.get("error"),
        "method": request.get("method"),
        "path": request.get("path"),
        "remoteAddress": request.get("remoteAddress"),
        "source": request.get("source"),
        "reason": request.get("reason"),
        "userAgent": request.get("userAgent"),
        "recorded": True,
    }
    try:
        ACTION_AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with ACTION_AUDIT_PATH.open("a") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
        os.chmod(ACTION_AUDIT_PATH, 0o600)
    except Exception as exc:
        payload["recorded"] = False
        payload["auditError"] = str(exc)
    return payload


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


def set_screens_override(enabled: bool) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "ok": True,
        "enabled": enabled,
        "status": "manual" if enabled else "automatic",
        "updatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    temporary = DISPLAY_AWAKE_OVERRIDE_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.chmod(temporary, 0o600)
    temporary.replace(DISPLAY_AWAKE_OVERRIDE_PATH)
    os.chmod(DISPLAY_AWAKE_OVERRIDE_PATH, 0o600)
    return {**payload, "path": str(DISPLAY_AWAKE_OVERRIDE_PATH)}


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

    def action_request_context(self, path: str, query: dict[str, list[str]]) -> dict[str, Any]:
        def query_value(name: str) -> str | None:
            value = (query.get(name) or [None])[0]
            return str(value)[:240] if value is not None else None

        def header_value(name: str) -> str | None:
            value = self.headers.get(name) if getattr(self, "headers", None) else None
            return str(value)[:240] if value is not None else None

        return {
            "method": getattr(self, "command", None),
            "path": path,
            "remoteAddress": self.client_address[0] if getattr(self, "client_address", None) else None,
            "source": query_value("source") or header_value("X-Smart-Home-Source"),
            "reason": query_value("reason") or header_value("X-Smart-Home-Reason"),
            "userAgent": header_value("User-Agent"),
        }

    def route(self) -> tuple[int, dict[str, Any]]:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/health":
            return 200, {"ok": True}
        if path == "/status":
            return 200, action_status()
        if path == "/status/energy":
            days = normalized_energy_history_days((query.get("days") or [7])[0])
            return 200, energy_status(days)
        if path == "/status/displays":
            payload = display_awake_observability()
            return (200 if payload["ok"] else 503), payload
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
        if path == "/action/screens-awake":
            payload = set_screens_override(True)
            return 200, payload
        if path == "/action/screens-auto":
            payload = set_screens_override(False)
            return 200, payload
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
            payload["audit"] = append_action_audit(
                "restart-homebridge",
                payload,
                self.action_request_context(path, query),
            )
            return (202 if payload["ok"] else 500), payload
        if path == "/action/restart-office-tahoma":
            payload = restart_office_tahoma()
            payload["audit"] = append_action_audit(
                "restart-office-tahoma",
                payload,
                self.action_request_context(path, query),
            )
            return (202 if payload["ok"] else 500), payload
        if path == "/action/silence-alerts":
            payload = silence_alerts()
            return (200 if payload["ok"] else 500), payload
        return 404, {"ok": False, "error": "unknown endpoint"}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/displays":
            self.send_html(200, render_display_page())
            return
        if parsed.path in {"/", "/energy"}:
            query = parse_qs(parsed.query)
            days = normalized_energy_history_days((query.get("days") or [7])[0])
            self.send_html(200, render_energy_page(days))
            return
        status, payload = self.route()
        self.send_json(status, payload)

    def do_POST(self) -> None:
        status, payload = self.route()
        self.send_json(status, payload)


class ReadOnlyDisplayHandler(Handler):
    server_version = "SmartHomeDisplayReadOnly/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LOG_DIR / "display-dashboard.access.log").open("a") as log:
            log.write(f"{datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')} {self.address_string()} {format % args}\n")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in {"/", "/displays"}:
            self.send_html(200, render_display_page(read_only=True))
            return
        if path == "/health":
            self.send_json(200, {"ok": True, "readOnly": True})
            return
        if path == "/status/displays":
            payload = display_awake_observability()
            self.send_json(200 if payload["ok"] else 503, payload)
            return
        self.send_json(404, {"ok": False, "error": "read-only dashboard endpoint not found"})

    def do_POST(self) -> None:
        self.send_json(405, {"ok": False, "error": "read-only dashboard; actions are disabled"})


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
    dashboard_server = None
    if config.get("dashboard_enabled") is True:
        dashboard_host = str(config.get("dashboard_bind_host") or "0.0.0.0")
        dashboard_port = int(config.get("dashboard_port") or 18766)
        dashboard_server = ThreadingHTTPServer((dashboard_host, dashboard_port), ReadOnlyDisplayHandler)
        threading.Thread(
            target=dashboard_server.serve_forever,
            name="read-only-display-dashboard",
            daemon=True,
        ).start()
    schedule_garage_light_hold_check()
    try:
        server.serve_forever()
    finally:
        if dashboard_server is not None:
            dashboard_server.shutdown()
            dashboard_server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
