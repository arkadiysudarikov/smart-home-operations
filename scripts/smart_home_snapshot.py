#!/usr/bin/env python3
from __future__ import annotations

import json
import hashlib
import os
import platform
import re
import socket
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "sources.json"
DATA_DIR = ROOT / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
REPORT_DIR = ROOT / "reports"
DB_PATH = DATA_DIR / "smart_home.sqlite"
SOURCE_ROOT = Path.home() / "Documents" / "Smart Home"
RUNTIME_ROOT = Path.home() / "Library" / "Application Support" / "SmartHomeMonitor"
DRIFT_CHECK_FILES = [
    "scripts/action_server.py",
    "scripts/analyze_all_energy_readings.py",
    "scripts/analyze_bill_home_pairing.py",
    "scripts/analyze_chargepoint_pairing.py",
    "scripts/analyze_combined_energy_monitor.py",
    "scripts/analyze_energy_automation_opportunities.py",
    "scripts/analyze_energy_costs.py",
    "scripts/analyze_energy_pairing.py",
    "scripts/analyze_meter_reconciliation.py",
    "scripts/analyze_patterns.py",
    "scripts/apply_alarm_sensor_saver_ui.js",
    "scripts/capture_alarm_com.js",
    "scripts/capture_chargepoint_browser_csv.js",
    "scripts/capture_envoy_direct.py",
    "scripts/capture_sense_now.js",
    "scripts/capture_sense_trends.js",
    "scripts/extract_sce_bills.py",
    "scripts/fetch_chargepoint_sessions.py",
    "scripts/fetch_sce_green_button_connect.py",
    "scripts/gate_test_mode.py",
    "scripts/generate_alerts.py",
    "scripts/install_homekit_virtual_sensors.py",
    "scripts/install_monitor.sh",
    "scripts/maintain_storage.py",
    "scripts/pair_sense_now.py",
    "scripts/patch_smarthq_remaining_duration.js",
    "scripts/probe_alarm_energy_settings_ui.js",
    "scripts/probe_alarm_sensor_saver_ui.js",
    "scripts/recover_unifi_occupancy.py",
    "scripts/refresh_energy.py",
    "scripts/repair_alarm_homebridge_cache.py",
    "scripts/set_alarm_light.js",
    "scripts/set_alarm_panel.js",
    "scripts/smart_home_snapshot.py",
    "scripts/update_office_tahoma_ip.js",
]
DRIFT_CHECK_EXTERNAL_FILES = [
    (
        "launchagents/com.arkadiy.smart-home-monitor.plist",
        Path.home() / "Library" / "LaunchAgents" / "com.arkadiy.smart-home-monitor.plist",
    ),
    (
        "launchagents/com.arkadiy.smart-home-actions.plist",
        Path.home() / "Library" / "LaunchAgents" / "com.arkadiy.smart-home-actions.plist",
    ),
]


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
TS_RE = re.compile(r"\[(\d{1,2}/\d{1,2}/\d{4},\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)\]")
WARNING_RE = re.compile(
    r"\b(warn|warning|error|failed|unauthorized|timeout|ETIMEDOUT|unknown state)\b|(?<![\d.])(401|403|502|504)(?![\d.])",
    re.I,
)
EVENT_LINE_RE = re.compile(
    r"^\[(?P<ts>\d{1,2}/\d{1,2}/\d{4},\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)\]\s+"
    r"(?:\[(?P<component>[^\]]+)\]\s+)?(?P<message>.*)$"
)
STATIC_INFO_CHARACTERISTICS = {
    "AccessoryFlags",
    "ConfiguredName",
    "FirmwareRevision",
    "Identify",
    "Manufacturer",
    "Model",
    "Name",
    "SerialNumber",
}


def run(cmd: list[str], timeout: int = 12) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except Exception as exc:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": str(exc)}


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text())


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def file_sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except Exception:
        return None


def collect_runtime_drift() -> dict[str, Any]:
    if SOURCE_ROOT.resolve() == RUNTIME_ROOT.resolve():
        return {
            "status": "same-root",
            "ok": True,
            "sourceRoot": str(SOURCE_ROOT),
            "runtimeRoot": str(RUNTIME_ROOT),
            "files": [],
            "driftedFiles": [],
            "missingFiles": [],
        }

    files: list[dict[str, Any]] = []
    drifted: list[str] = []
    missing: list[str] = []
    check_items = [(relative, RUNTIME_ROOT / relative) for relative in DRIFT_CHECK_FILES]
    check_items.extend(DRIFT_CHECK_EXTERNAL_FILES)
    for relative, runtime_path in check_items:
        source_path = SOURCE_ROOT / relative
        source_hash = file_sha256(source_path)
        runtime_hash = file_sha256(runtime_path)
        item = {
            "path": relative,
            "sourcePresent": source_hash is not None,
            "runtimePresent": runtime_hash is not None,
            "sourceHash": source_hash[:12] if source_hash else None,
            "runtimeHash": runtime_hash[:12] if runtime_hash else None,
            "match": bool(source_hash and runtime_hash and source_hash == runtime_hash),
        }
        if not source_hash or not runtime_hash:
            item["status"] = "missing"
            missing.append(relative)
        elif source_hash != runtime_hash:
            item["status"] = "drift"
            drifted.append(relative)
        else:
            item["status"] = "ok"
        files.append(item)

    status = "drift" if drifted else "missing" if missing else "ok"
    return {
        "status": status,
        "ok": status == "ok",
        "sourceRoot": str(SOURCE_ROOT),
        "runtimeRoot": str(RUNTIME_ROOT),
        "files": files,
        "driftedFiles": drifted,
        "missingFiles": missing,
    }


def sanitize_homebridge_config(config: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {"present": False}
    return {
        "present": True,
        "bridge": {
            "name": config.get("bridge", {}).get("name"),
            "port": config.get("bridge", {}).get("port"),
            "username_present": bool(config.get("bridge", {}).get("username")),
        },
        "platforms": [
            {
                "platform": item.get("platform"),
                "name": item.get("name"),
                "childBridge": item.get("_bridge", {}).get("username") is not None,
                "childBridgeName": item.get("_bridge", {}).get("name"),
                "childBridgePort": item.get("_bridge", {}).get("port"),
                "useMatter": item.get("useMatter"),
                "matterOnly": item.get("matterOnly"),
                "shouldUseWebSockets": item.get("shouldUseWebSockets"),
                "disabled": item.get("disabled"),
            }
            for item in config.get("platforms", [])
            if isinstance(item, dict)
        ],
        "accessories": [
            {"accessory": item.get("accessory"), "name": item.get("name")}
            for item in config.get("accessories", [])
            if isinstance(item, dict)
        ],
        "disabledPlugins": config.get("disabledPlugins", []),
    }


def collect_cache_summary(homebridge_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    accessories_dir = homebridge_dir / "accessories"
    for path in sorted(accessories_dir.glob("cachedAccessories*")):
        data = read_json(path)
        count = len(data) if isinstance(data, list) else None
        names: list[str] = []
        if isinstance(data, list):
            for item in data[:20]:
                context = item.get("context", {}) if isinstance(item, dict) else {}
                display_name = context.get("displayName") or item.get("displayName") if isinstance(item, dict) else None
                if display_name:
                    names.append(str(display_name))
        out.append({"file": path.name, "count": count, "sampleNames": names})
    return out


def collect_homebridge_permissions(homebridge_dir: Path) -> dict[str, Any]:
    checks = [
        homebridge_dir,
        homebridge_dir / "config.json",
        homebridge_dir / "codex-backups",
        homebridge_dir / "auth.json",
        homebridge_dir / ".uix-secrets",
    ]
    entries: list[dict[str, Any]] = []
    for path in checks:
        try:
            stat = path.stat()
        except FileNotFoundError:
            entries.append({"path": str(path), "present": False})
            continue
        mode = stat.st_mode & 0o777
        entries.append(
            {
                "path": str(path),
                "present": True,
                "mode": oct(mode),
                "groupOrOtherReadable": bool(mode & 0o044),
                "groupOrOtherWritable": bool(mode & 0o022),
                "groupOrOtherExecutable": bool(mode & 0o011),
                "tooOpen": bool(mode & 0o077),
            }
        )
    return {
        "ok": not any(item.get("tooOpen") for item in entries),
        "entries": entries,
        "insecurePaths": [item["path"] for item in entries if item.get("tooOpen")],
    }


def find_apps(app_names: list[str]) -> list[dict[str, Any]]:
    roots = [Path("/Applications"), Path("/System/Applications"), Path.home() / "Applications"]
    found: list[dict[str, Any]] = []
    for name in app_names:
        matches = []
        for root in roots:
            if not root.exists():
                continue
            matches.extend(root.glob(f"*{name}*.app"))
        found.append({"name": name, "installed": bool(matches), "paths": [str(p) for p in matches]})
    return found


def parse_launchd(service: str) -> dict[str, Any]:
    result = run(["launchctl", "print", service])
    text = result["stdout"]
    state = re.search(r"\bstate = ([^\n]+)", text)
    pid = re.search(r"\bpid = ([0-9]+)", text)
    program = re.search(r"\bprogram = ([^\n]+)", text)
    return {
        "ok": result["ok"],
        "state": state.group(1).strip() if state else None,
        "pid": int(pid.group(1)) if pid else None,
        "program": program.group(1).strip() if program else None,
    }


def parse_ports() -> list[dict[str, Any]]:
    result = run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"], timeout=20)
    ports = []
    for line in result["stdout"].splitlines()[1:]:
        parts = line.split()
        if len(parts) < 9:
            continue
        name, pid = parts[0], parts[1]
        address = parts[-2] if parts[-1] == "(LISTEN)" else parts[-1]
        if name != "node":
            continue
        match = re.search(r":(\d+)$", address)
        ports.append({"process": name, "pid": int(pid), "address": address, "port": int(match.group(1)) if match else None})
    return ports


def read_tail(path: Path, max_lines: int) -> list[str]:
    if not path.exists():
        return []
    result = run(["tail", "-n", str(max_lines), str(path)], timeout=20)
    return [ANSI_RE.sub("", line) for line in result["stdout"].splitlines()]


def current_homebridge_run_lines(lines: list[str]) -> list[str]:
    start_index = 0
    for index, line in enumerate(lines):
        if "Started Homebridge v" in line or "Starting Homebridge with extra flags" in line:
            start_index = index
    return lines[start_index:]


def collect_log_signals(lines: list[str]) -> dict[str, Any]:
    joined = "\n".join(lines)
    warnings = [
        line
        for line in lines
        if TS_RE.search(line)
        and WARNING_RE.search(line)
    ]
    latest: dict[str, Any] = {}
    for key, pattern in {
        "enphase_production_kw": r"Live Data, Production, power: ([\d.-]+) kW",
        "enphase_consumption_net_kw": r"(?:Meter|Power And Energy), Consumption Net, power: ([\d.-]+) kW",
        "enphase_consumption_total_kw": r'(?:Updated device: Consumption Total \{.*?"powerKw": ([\d.-]+)|(?:Meter|Power And Energy), Consumption Total, power: ([\d.-]+) kW)',
        "enphase_backup_percent": r"Live Data, Encharge, backup level: ([\d.-]+) %",
        "enphase_backup_kw": r"Live Data, Encharge, backup energy: ([\d.-]+) kW",
    }.items():
        matches = re.findall(pattern, joined, flags=re.S)
        if matches:
            value = matches[-1]
            if isinstance(value, tuple):
                value = next((part for part in value if part), "")
            try:
                latest[key] = float(value)
            except ValueError:
                latest[key] = value
    unifi_status = {}
    for match in re.findall(r'(?:Accessory status unchanged|Updated accessory status): "([^"]+)" (active|inactive)', joined):
        unifi_status[match[0]] = match[1]
    first_timestamp = next((m.group(1) for line in lines if (m := TS_RE.search(line))), None)
    latest_timestamp = next((m.group(1) for line in reversed(lines) if (m := TS_RE.search(line))), None)
    return {
        "lineCount": len(lines),
        "runStartedAt": parse_log_timestamp(first_timestamp) if first_timestamp else None,
        "latestTimestamp": latest_timestamp,
        "alarmWebsocketEstablished": "WebSocket connection established" in joined,
        "moparLoginSuccessful": "Login successful" in joined and "[Mopar]" in joined,
        "unifiOccupancy": {
            "trackedAccessories": len(unifi_status),
            "active": sorted([name for name, state in unifi_status.items() if state == "active"]),
            "inactiveCount": len([1 for state in unifi_status.values() if state == "inactive"]),
        },
        "latestMetrics": latest,
        "warningCount": len(warnings),
        "recentWarnings": warnings[-40:],
    }


def parse_log_timestamp(raw: str) -> str | None:
    try:
        dt = datetime.strptime(raw, "%m/%d/%Y, %I:%M:%S %p")
    except ValueError:
        return None
    return dt.astimezone().isoformat(timespec="seconds")


def classify_home_event(component: str | None, message: str) -> str:
    text = f"{component or ''} {message}".lower()
    if WARNING_RE.search(text):
        return "warning"
    if "accessory status changed" in text or "accessory status unchanged" in text or "updated accessory status" in text:
        return "occupancy"
    if "mqtt message received" in text or "current-state" in text or "environmental-current-sensor-data" in text:
        return "telemetry"
    if "live data" in text or "power and energy" in text or "updated device" in text:
        return "energy"
    if "calendar" in text or "found events" in text:
        return "calendar"
    if re.search(r"\bpost /events/[0-9a-f-]+/fetch\b", text):
        return "home_event_fetch"
    if "websocket" in text:
        return "websocket"
    return "homebridge"


def collect_home_events(lines: list[str], limit: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in lines:
        clean = ANSI_RE.sub("", line).strip()
        match = EVENT_LINE_RE.match(clean)
        if not match:
            continue
        message = match.group("message").strip()
        if not message:
            continue
        component = match.group("component")
        captured_at = parse_log_timestamp(match.group("ts")) or match.group("ts")
        event_key = hashlib.sha256(f"{captured_at}|{component or ''}|{message}".encode()).hexdigest()
        events.append(
            {
                "eventKey": event_key,
                "capturedAt": captured_at,
                "component": component or "Homebridge",
                "type": classify_home_event(component, message),
                "message": message,
            }
        )
    return events[-limit:]


def flatten_characteristic_state(homebridge_dir: Path) -> dict[str, dict[str, Any]]:
    state: dict[str, dict[str, Any]] = {}
    accessories_dir = homebridge_dir / "accessories"
    for path in sorted(accessories_dir.glob("cachedAccessories*")):
        data = read_json(path)
        if not isinstance(data, list):
            continue
        for accessory in data:
            if not isinstance(accessory, dict):
                continue
            accessory_name = str(accessory.get("displayName") or accessory.get("UUID") or "Unknown Accessory")
            plugin = accessory.get("plugin")
            platform_name = accessory.get("platform")
            accessory_uuid = accessory.get("UUID")
            for service in accessory.get("services", []):
                if not isinstance(service, dict):
                    continue
                service_name = service.get("displayName") or service.get("constructorName") or "Service"
                service_type = service.get("constructorName")
                if service_type == "AccessoryInformation":
                    continue
                for characteristic in service.get("characteristics", []):
                    if not isinstance(characteristic, dict) or "value" not in characteristic:
                        continue
                    characteristic_type = characteristic.get("constructorName") or characteristic.get("displayName")
                    if characteristic_type in STATIC_INFO_CHARACTERISTICS:
                        continue
                    value = characteristic.get("value")
                    key = "|".join(
                        str(part or "")
                        for part in [
                            path.name,
                            accessory_uuid,
                            service.get("UUID"),
                            service_name,
                            characteristic.get("UUID"),
                            characteristic_type,
                        ]
                    )
                    state[key] = {
                        "accessory": accessory_name,
                        "service": service_name,
                        "characteristic": characteristic_type or "value",
                        "value": value,
                        "plugin": plugin,
                        "platform": platform_name,
                        "cacheFile": path.name,
                    }
    return state


def load_previous_characteristic_state() -> dict[str, dict[str, Any]]:
    path = DATA_DIR / "latest_characteristics.json"
    data = read_json(path)
    return data if isinstance(data, dict) else {}


def compare_characteristic_state(
    previous: dict[str, dict[str, Any]], current: dict[str, dict[str, Any]], captured_at: str
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for key, item in sorted(current.items()):
        if key not in previous:
            continue
        old_value = previous[key].get("value")
        new_value = item.get("value")
        if old_value == new_value:
            continue
        changes.append(
            {
                "eventKey": hashlib.sha256(f"{captured_at}|{key}|{old_value!r}|{new_value!r}".encode()).hexdigest(),
                "capturedAt": captured_at,
                "type": "characteristic_change",
                "accessory": item.get("accessory"),
                "service": item.get("service"),
                "characteristic": item.get("characteristic"),
                "previousValue": old_value,
                "value": new_value,
                "plugin": item.get("plugin"),
                "platform": item.get("platform"),
                "cacheFile": item.get("cacheFile"),
            }
        )
    return changes


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """
            create table if not exists snapshots (
              id integer primary key autoincrement,
              captured_at text not null,
              homebridge_state text,
              warning_count integer not null,
              alarm_websocket integer not null,
              unifi_active_count integer not null,
              metrics_json text not null,
              raw_json text not null
            )
            """
        )
        db.execute(
            """
            create table if not exists home_events (
              event_key text primary key,
              captured_at text not null,
              event_type text not null,
              component text,
              accessory text,
              service text,
              characteristic text,
              previous_value text,
              value text,
              message text,
              raw_json text not null
            )
            """
        )


def save_home_events(events: list[dict[str, Any]]) -> int:
    if not events:
        return 0
    inserted = 0
    with sqlite3.connect(DB_PATH) as db:
        for event in events:
            before = db.total_changes
            db.execute(
                """
                insert or ignore into home_events (
                  event_key, captured_at, event_type, component, accessory, service,
                  characteristic, previous_value, value, message, raw_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["eventKey"],
                    event["capturedAt"],
                    event["type"],
                    event.get("component"),
                    event.get("accessory"),
                    event.get("service"),
                    event.get("characteristic"),
                    json.dumps(event.get("previousValue"), sort_keys=True),
                    json.dumps(event.get("value"), sort_keys=True),
                    event.get("message"),
                    json.dumps(event, sort_keys=True),
                ),
            )
            if db.total_changes > before:
                inserted += 1
    return inserted


def save_snapshot(snapshot: dict[str, Any]) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    stamp = snapshot["captured_at"].replace(":", "").replace("-", "").replace("+", "Z")
    path = SNAPSHOT_DIR / f"{stamp}.json"
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    (DATA_DIR / "latest.json").write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """
            insert into snapshots (
              captured_at, homebridge_state, warning_count, alarm_websocket,
              unifi_active_count, metrics_json, raw_json
            ) values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot["captured_at"],
                snapshot["homebridge"]["launchd"].get("state"),
                snapshot["homebridge"]["logs"].get("warningCount", 0),
                1 if snapshot["homebridge"]["logs"].get("alarmWebsocketEstablished") else 0,
                len(snapshot["homebridge"]["logs"].get("unifiOccupancy", {}).get("active", [])),
                json.dumps(snapshot["homebridge"]["logs"].get("latestMetrics", {}), sort_keys=True),
                json.dumps(snapshot, sort_keys=True),
            ),
        )
    inserted_events = save_home_events(snapshot["homeEvents"]["recent"] + snapshot["homeEvents"]["characteristicChanges"])
    snapshot["homeEvents"]["newEventsStored"] = inserted_events
    (DATA_DIR / "latest_characteristics.json").write_text(
        json.dumps(snapshot["homeEvents"]["currentCharacteristics"], indent=2, sort_keys=True) + "\n"
    )
    (DATA_DIR / "latest_events.json").write_text(
        json.dumps(snapshot["homeEvents"], indent=2, sort_keys=True) + "\n"
    )
    return path


def write_report(snapshot: dict[str, Any], snapshot_path: Path) -> None:
    hb = snapshot["homebridge"]
    logs = hb["logs"]
    metrics = logs.get("latestMetrics", {})
    apps = snapshot["apps"]
    active = logs.get("unifiOccupancy", {}).get("active", [])
    permissions = hb.get("security", {}).get("homebridgePermissions", {})
    runtime_drift = snapshot.get("runtimeDrift", {})
    alarm_platform = next(
        (
            item
            for item in hb["config"].get("platforms", [])
            if item.get("platform") == "Alarmdotcom"
        ),
        {},
    )
    home_events = snapshot["homeEvents"]
    event_counts = home_events.get("recentCounts", {})
    changes = home_events.get("characteristicChanges", [])
    lines = [
        "# Smart Home Snapshot",
        "",
        f"- Captured: `{snapshot['captured_at']}`",
        f"- Snapshot file: `{snapshot_path}`",
        f"- Host: `{snapshot['host']['hostname']}`",
        "",
        "## Core Runtime",
        "",
        f"- Homebridge launchd state: `{hb['launchd'].get('state')}`",
        f"- Homebridge pid: `{hb['launchd'].get('pid')}`",
        f"- Homebridge UI port configured: `{snapshot['sourceConfig']['homebridge'].get('ui_port')}`",
        f"- Main bridge port in config: `{hb['config'].get('bridge', {}).get('port')}`",
        f"- Node listening ports observed: `{', '.join(str(p['port']) for p in hb['ports'] if p.get('port'))}`",
        f"- Homebridge storage permissions private: `{permissions.get('ok')}`",
        "",
        "## Integrations",
        "",
        f"- Platforms configured: `{len(hb['config'].get('platforms', []))}`",
        f"- Direct accessories configured: `{len(hb['config'].get('accessories', []))}`",
        f"- Accessory cache files: `{len(hb['accessoryCaches'])}`",
        f"- Alarm.com websocket configured: `{alarm_platform.get('shouldUseWebSockets')}`",
        f"- Alarm.com websocket established in recent log: `{logs.get('alarmWebsocketEstablished')}`",
        f"- Mopar login successful in recent log: `{logs.get('moparLoginSuccessful')}`",
        f"- UniFi active occupancy accessories in recent log: `{', '.join(active) if active else 'none observed'}`",
        "",
        "## Home Events",
        "",
        f"- Recent Homebridge event lines sampled: `{len(home_events.get('recent', []))}`",
        f"- Newly stored event rows this run: `{home_events.get('newEventsStored', 0)}`",
        f"- Characteristic values tracked: `{len(home_events.get('currentCharacteristics', {}))}`",
        f"- Sensor/accessory changes since last run: `{len(changes)}`",
        f"- Event types observed: `{', '.join(f'{k}={v}' for k, v in sorted(event_counts.items())) or 'none'}`",
        "",
        "## Recent Sensor/Accessory Changes",
        "",
    ]
    if changes:
        for change in changes[-20:]:
            lines.append(
                "- "
                f"`{change.get('accessory')}` / `{change.get('service')}` / "
                f"`{change.get('characteristic')}`: `{change.get('previousValue')}` -> `{change.get('value')}`"
            )
    else:
        lines.append("- No sensor/accessory value changes detected since the previous snapshot.")
    lines.extend(
        [
            "",
            "## Recent Home Events",
            "",
        ]
    )
    recent_events = home_events.get("recent", [])
    if recent_events:
        for event in recent_events[-20:]:
            lines.append(
                f"- `{event.get('capturedAt')}` `{event.get('type')}` "
                f"`{event.get('component')}`: `{str(event.get('message'))[-220:]}`"
            )
    else:
        lines.append("- No Homebridge event lines found in the sampled log window.")
    lines.extend(
        [
            "",
            "## Energy Signals",
            "",
            f"- Enphase production: `{metrics.get('enphase_production_kw', 'not observed')}` kW",
            f"- Enphase net consumption: `{metrics.get('enphase_consumption_net_kw', 'not observed')}` kW",
            f"- Enphase total consumption: `{metrics.get('enphase_consumption_total_kw', 'not observed')}` kW",
            f"- Enphase backup level: `{metrics.get('enphase_backup_percent', 'not observed')}` %",
            f"- Enphase backup energy: `{metrics.get('enphase_backup_kw', 'not observed')}` kW",
            "",
            "## Local Apps",
            "",
        ]
    )
    for app in apps:
        lines.append(f"- {app['name']}: `{'installed' if app['installed'] else 'not found'}`")
    lines.extend(["", "## Recent Warnings", ""])
    warnings = logs.get("recentWarnings", [])
    if warnings:
        lines.extend(f"- `{line[-220:]}`" for line in warnings[-12:])
    else:
        lines.append("- No warning lines found in the sampled log window.")
    lines.extend(["", "## Runtime Drift", ""])
    lines.append(f"- Status: `{runtime_drift.get('status', 'unknown')}`")
    lines.append(f"- Source root: `{runtime_drift.get('sourceRoot')}`")
    lines.append(f"- Runtime root: `{runtime_drift.get('runtimeRoot')}`")
    drifted_files = runtime_drift.get("driftedFiles") or []
    missing_files = runtime_drift.get("missingFiles") or []
    if drifted_files:
        lines.append(f"- Drifted files: `{', '.join(drifted_files)}`")
    if missing_files:
        lines.append(f"- Missing files: `{', '.join(missing_files)}`")
    if not drifted_files and not missing_files:
        lines.append("- Runtime scripts match the source checkout for monitored files.")
    insecure_paths = permissions.get("insecurePaths", [])
    lines.extend(["", "## Security Hygiene", ""])
    if insecure_paths:
        lines.extend(f"- `{path}` has group/other permission bits set." for path in insecure_paths)
    else:
        lines.append("- Homebridge storage paths checked are private to the current user.")
    (REPORT_DIR / "latest.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    config = load_config()
    homebridge_dir = Path(config["homebridge"]["storage_path"]).expanduser()
    hb_config = read_json(homebridge_dir / "config.json")
    log_lines = read_tail(homebridge_dir / "homebridge.log", config["monitoring"]["log_tail_lines"])
    current_log_lines = current_homebridge_run_lines(log_lines)
    captured_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    previous_characteristics = load_previous_characteristic_state()
    current_characteristics = flatten_characteristic_state(homebridge_dir)
    characteristic_changes = compare_characteristic_state(previous_characteristics, current_characteristics, captured_at)
    recent_events = collect_home_events(current_log_lines, config["monitoring"].get("home_event_tail_limit", 500))
    recent_counts: dict[str, int] = {}
    for event in recent_events:
        recent_counts[event["type"]] = recent_counts.get(event["type"], 0) + 1
    snapshot = {
        "captured_at": captured_at,
        "host": {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
        },
        "apps": find_apps(config["local_apps"]),
        "homebridge": {
            "launchd": parse_launchd(config["homebridge"]["launchd_service"]),
            "config": sanitize_homebridge_config(hb_config),
            "accessoryCaches": collect_cache_summary(homebridge_dir),
            "ports": parse_ports(),
            "logs": collect_log_signals(current_log_lines),
            "security": {
                "homebridgePermissions": collect_homebridge_permissions(homebridge_dir),
            },
        },
        "homeEvents": {
            "recent": recent_events,
            "recentCounts": recent_counts,
            "currentCharacteristics": current_characteristics,
            "characteristicChanges": characteristic_changes,
            "newEventsStored": 0,
        },
        "runtimeDrift": collect_runtime_drift(),
        "sourceConfig": config,
    }
    snapshot_path = save_snapshot(snapshot)
    write_report(snapshot, snapshot_path)
    print(snapshot_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
