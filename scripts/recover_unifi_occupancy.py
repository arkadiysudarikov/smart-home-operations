#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import signal
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "sources.json"
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
STATUS_PATH = DATA_DIR / "latest_unifi_occupancy_recovery.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_config() -> dict[str, Any]:
    return load_json(CONFIG_PATH)


def homebridge_config_path(config: dict[str, Any]) -> Path:
    return Path(config["homebridge"]["storage_path"]).expanduser() / "config.json"


def unifi_platform(homebridge_config: dict[str, Any]) -> dict[str, Any]:
    for item in homebridge_config.get("platforms", []):
        if isinstance(item, dict) and item.get("platform") == "UnifiOccupancy":
            return item
    return {}


def latest_snapshot() -> dict[str, Any]:
    return load_json(DATA_DIR / "latest.json")


def recent_warning_lines(snapshot: dict[str, Any]) -> list[str]:
    warnings = snapshot.get("homebridge", {}).get("logs", {}).get("recentWarnings", [])
    return [str(item) for item in warnings if item is not None]


def unifi_active_count(snapshot: dict[str, Any]) -> int:
    active = snapshot.get("homebridge", {}).get("logs", {}).get("unifiOccupancy", {}).get("active", [])
    return len(active) if isinstance(active, list) else 0


def unifi_tracked_count(snapshot: dict[str, Any]) -> int:
    tracked = snapshot.get("homebridge", {}).get("logs", {}).get("unifiOccupancy", {}).get("trackedAccessories")
    return int(tracked) if isinstance(tracked, (int, float)) else 0


def has_unifi_api_warning(snapshot: dict[str, Any]) -> bool:
    for line in recent_warning_lines(snapshot):
        text = line.lower()
        if "homebridge-unifi-occupancy" not in text:
            continue
        if any(token in text for token in ("502", "504", "gateway timeout", "bad gateway", "timeout", "etimedout")):
            return True
    return False


def has_unifi_auth_warning(snapshot: dict[str, Any]) -> bool:
    return any("homebridge-unifi-occupancy" in line and "401" in line for line in recent_warning_lines(snapshot))


def should_restart_for_stale_occupancy(api_warning: bool, tracked_count: int, recovery_config: dict[str, Any]) -> bool:
    restart_when_untracked = bool(recovery_config.get("restart_when_no_tracked_accessories", False))
    return api_warning or (restart_when_untracked and tracked_count == 0)


def parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def cooldown_active(status: dict[str, Any], minutes: float) -> bool:
    last = parse_dt(status.get("restartedAt"))
    if last is None:
        return False
    return datetime.now(timezone.utc).astimezone() - last.astimezone() < timedelta(minutes=minutes)


def request_json(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: int = 12,
) -> tuple[bool, int | None, Any, str | None]:
    data = None
    headers = {"User-Agent": "smart-home-monitor-unifi-recovery"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read()
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except Exception:
                parsed = {"bytes": len(raw)}
            return True, int(response.status), parsed, None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except Exception:
            parsed = {"bytes": len(raw)}
        return False, int(exc.code), parsed, None
    except Exception as exc:
        return False, None, None, str(exc)


def probe_unifi_api(platform_config: dict[str, Any]) -> dict[str, Any]:
    unifi = platform_config.get("unifi") if isinstance(platform_config.get("unifi"), dict) else {}
    controller = str(unifi.get("controller") or "").rstrip("/")
    username = unifi.get("username")
    password = unifi.get("password")
    site = str(unifi.get("site") or "default")
    unifios = unifi.get("unifios") is not False
    if not controller or not username or not password:
        return {"ok": False, "authOk": False, "apiOk": False, "error": "UniFi controller credentials are incomplete"}

    context = ssl._create_unverified_context() if unifi.get("secure") is False else None
    cookie_jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar), urllib.request.HTTPSHandler(context=context))
    auth_path = "/api/auth/login" if unifios else "/api/login"
    login_ok, login_status, _login_payload, login_error = request_json(
        opener,
        f"{controller}{auth_path}",
        method="POST",
        body={"username": username, "password": password},
        timeout=12,
    )
    result: dict[str, Any] = {
        "ok": False,
        "authOk": bool(login_ok),
        "apiOk": False,
        "loginStatus": login_status,
        "loginError": login_error,
    }
    if not login_ok:
        result["classification"] = "auth" if login_status in {401, 403} else "login_unavailable"
        return result

    prefix = "/proxy/network" if unifios else ""
    endpoints = {
        "fingerprints": f"{controller}{prefix}/v2/api/fingerprint_devices/0",
        "activeClients": f"{controller}{prefix}/v2/api/site/{site}/clients/active",
    }
    api_results: dict[str, Any] = {}
    for name, url in endpoints.items():
        started = time.monotonic()
        ok, status, payload, error = request_json(opener, url, timeout=15)
        elapsed = round(time.monotonic() - started, 3)
        count = len(payload) if isinstance(payload, list) else None
        api_results[name] = {"ok": ok, "status": status, "seconds": elapsed, "count": count, "error": error}
    result["endpoints"] = api_results
    result["apiOk"] = all(item.get("ok") for item in api_results.values())
    result["ok"] = bool(result["authOk"] and result["apiOk"])
    if result["apiOk"]:
        result["classification"] = "healthy"
    else:
        result["classification"] = "api"
    return result


def listening_pid(port: int) -> int | None:
    try:
        proc = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return None
    for line in proc.stdout.splitlines():
        if line.strip().isdigit():
            return int(line.strip())
    return None


def wait_for_new_pid(port: int, old_pid: int | None, timeout: int = 60) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pid = listening_pid(port)
        if pid is not None and pid != old_pid:
            return {"ok": True, "pid": pid}
        time.sleep(2)
    pid = listening_pid(port)
    return {"ok": pid is not None, "pid": pid, "timedOut": True}


def restart_child_bridge(port: int) -> dict[str, Any]:
    before_pid = listening_pid(port)
    if before_pid is None:
        return {"ok": False, "port": port, "error": "no child bridge is listening on the configured port"}
    try:
        os.kill(before_pid, signal.SIGTERM)
    except Exception as exc:
        return {"ok": False, "port": port, "previousPid": before_pid, "error": str(exc)}
    wait = wait_for_new_pid(port, before_pid)
    return {
        "ok": bool(wait.get("ok")),
        "port": port,
        "previousPid": before_pid,
        "currentPid": wait.get("pid"),
        "waitForRestart": wait,
    }


def write_status(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def main() -> int:
    config = load_config()
    recovery_config = config.get("unifi_occupancy_recovery", {}) if isinstance(config.get("unifi_occupancy_recovery"), dict) else {}
    if recovery_config.get("enabled", True) is False:
        write_status({"ok": True, "enabled": False, "checkedAt": now_iso(), "action": "disabled"})
        return 0

    hb_config = load_json(homebridge_config_path(config))
    platform_config = unifi_platform(hb_config)
    if not platform_config:
        write_status({"ok": False, "checkedAt": now_iso(), "action": "none", "error": "UnifiOccupancy platform is not configured"})
        return 0

    snapshot = latest_snapshot()
    active_count = unifi_active_count(snapshot)
    tracked_count = unifi_tracked_count(snapshot)
    api_warning = has_unifi_api_warning(snapshot)
    auth_warning = has_unifi_auth_warning(snapshot)
    restart_when_untracked = bool(recovery_config.get("restart_when_no_tracked_accessories", False))
    stale = should_restart_for_stale_occupancy(api_warning, tracked_count, recovery_config)
    probe = probe_unifi_api(platform_config)
    status: dict[str, Any] = {
        "ok": True,
        "checkedAt": now_iso(),
        "action": "none",
        "activeCountBefore": active_count,
        "trackedCountBefore": tracked_count,
        "restartWhenNoTrackedAccessories": restart_when_untracked,
        "apiWarningInRecentLog": api_warning,
        "authWarningInRecentLog": auth_warning,
        "stale": stale,
        "probe": probe,
    }

    if auth_warning or probe.get("classification") == "auth":
        status.update({"ok": False, "action": "none", "classification": "auth", "reason": "UniFi credentials are rejected; not restarting child bridge"})
        write_status(status)
        return 0
    if not probe.get("apiOk"):
        status.update({"ok": False, "action": "none", "classification": probe.get("classification") or "api", "reason": "UniFi API is not healthy; not restarting child bridge"})
        write_status(status)
        return 0
    if not stale:
        status.update({"classification": "healthy", "reason": "UniFi API and occupancy log are healthy"})
        write_status(status)
        return 0

    previous_status = load_json(STATUS_PATH)
    cooldown_minutes = float(recovery_config.get("cooldown_minutes", 20))
    if cooldown_active(previous_status, cooldown_minutes):
        status.update({"action": "none", "classification": "cooldown", "reason": f"last restart is inside {cooldown_minutes:g} minute cooldown"})
        write_status(status)
        return 0

    port = int(platform_config.get("_bridge", {}).get("port") or recovery_config.get("child_bridge_port") or 52746)
    restart = restart_child_bridge(port)
    status.update(
        {
            "action": "restart_child_bridge",
            "classification": "recovered" if restart.get("ok") else "restart_failed",
            "restart": restart,
            "restartedAt": now_iso() if restart.get("ok") else None,
        }
    )
    if restart.get("ok") and recovery_config.get("refresh_snapshot_after_restart", True) is not False:
        time.sleep(float(recovery_config.get("post_restart_snapshot_delay_seconds", 8)))
        snapshot_run = subprocess.run(
            [str(ROOT / "scripts" / "smart_home_snapshot.py")],
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        status["postRestartSnapshot"] = {
            "ok": snapshot_run.returncode == 0,
            "returncode": snapshot_run.returncode,
            "stdout": snapshot_run.stdout[-1000:],
            "stderr": snapshot_run.stderr[-1000:],
        }
    write_status(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
