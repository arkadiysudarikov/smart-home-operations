#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path.home() / "Library" / "Application Support" / "SmartHomeMonitor"
DATA_DIR = ROOT / "data"
COMPARISON_PATH = DATA_DIR / "latest_alarm_homebridge_state.json"
CHARACTERISTICS_PATH = DATA_DIR / "latest_characteristics.json"
HOMEBRIDGE_ACCESSORY_DIR = Path.home() / ".homebridge" / "accessories"
BACKUP_DIR = Path.home() / ".homebridge" / "codex-backups" / "alarm-cache-repair"

MOTION_FALSE_STATES = {"Idle", "Closed"}
MOTION_TRUE_STATES = {"Active", "Activated", "Open"}
CONTACT_CLOSED_STATES = {"Idle", "Closed"}
CONTACT_OPEN_STATES = {"Open", "Active", "Activated"}
LIGHT_OFF_STATES = {"Off"}
LIGHT_ON_STATES = {"On"}
SECURITY_SYSTEM_STATES = {
    "Armed stay": 0,
    "Armed away": 1,
    "Armed night": 2,
    "Disarmed": 3,
    "Alarm triggered": 4,
}


def running_from_runtime_root() -> bool:
    return ROOT.resolve() == RUNTIME_ROOT.resolve()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def desired_value(characteristic: str, portal_state: str) -> Any:
    if characteristic == "MotionDetected":
        if portal_state in MOTION_FALSE_STATES:
            return False
        if portal_state in MOTION_TRUE_STATES:
            return True
    if characteristic == "ContactSensorState":
        if portal_state in CONTACT_CLOSED_STATES:
            return 0
        if portal_state in CONTACT_OPEN_STATES:
            return 1
    if characteristic == "On":
        if portal_state in LIGHT_OFF_STATES:
            return False
        if portal_state in LIGHT_ON_STATES:
            return True
    if characteristic == "SecuritySystemCurrentState":
        return SECURITY_SYSTEM_STATES.get(portal_state)
    return None


def characteristic_display_name(characteristic: str) -> str | None:
    return {
        "MotionDetected": "Motion Detected",
        "ContactSensorState": "Contact Sensor State",
        "On": "On",
        "SecuritySystemCurrentState": "Security System Current State",
    }.get(characteristic)


def characteristic_index(characteristics: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for item in characteristics.values():
        if not isinstance(item, dict):
            continue
        if item.get("plugin") != "homebridge-node-alarm-dot-com":
            continue
        accessory = str(item.get("accessory") or "")
        characteristic = str(item.get("characteristic") or "")
        if accessory and characteristic:
            index[(accessory, characteristic)] = item
    return index


def repair_cache_file(cache_path: Path, device_id: str, display_name: str, characteristic: str, value: Any) -> dict[str, Any]:
    data = load_json(cache_path)
    if not isinstance(data, list):
        return {"ok": False, "cacheFile": str(cache_path), "error": "cache file is not a list"}

    target_characteristic = characteristic_display_name(characteristic)
    changes: list[dict[str, Any]] = []
    for accessory in data:
        if not isinstance(accessory, dict):
            continue
        context = accessory.get("context") if isinstance(accessory.get("context"), dict) else {}
        if context.get("accID") != device_id and accessory.get("displayName") != display_name:
            continue
        if context.get("state") != value:
            changes.append({"field": "context.state", "from": context.get("state"), "to": value})
            context["state"] = value
        for service in accessory.get("services") or []:
            if not isinstance(service, dict):
                continue
            for char in service.get("characteristics") or []:
                if not isinstance(char, dict):
                    continue
                if char.get("displayName") == target_characteristic and char.get("value") != value:
                    changes.append({"field": target_characteristic, "from": char.get("value"), "to": value})
                    char["value"] = value

    if not changes:
        return {"ok": True, "cacheFile": str(cache_path), "changed": False, "changes": []}

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_path = BACKUP_DIR / f"{cache_path.name}.{stamp}.bak"
    suffix = 1
    while backup_path.exists():
        backup_path = BACKUP_DIR / f"{cache_path.name}.{stamp}.{suffix}.bak"
        suffix += 1
    shutil.copy2(cache_path, backup_path)

    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, separators=(",", ":")) + "\n")
    os.replace(tmp_path, cache_path)
    os.chmod(cache_path, 0o600)
    return {
        "ok": True,
        "cacheFile": str(cache_path),
        "backup": str(backup_path),
        "changed": True,
        "changes": changes,
    }


def repair_from_latest_comparison(*, allow_outside_runtime: bool = False) -> dict[str, Any]:
    if not allow_outside_runtime and not running_from_runtime_root():
        return {
            "ok": False,
            "error": "refusing to repair live Homebridge cache outside the runtime root",
            "sourceRoot": str(ROOT),
            "runtimeRoot": str(RUNTIME_ROOT),
            "changedCount": 0,
            "repairs": [],
            "skipped": [],
        }
    comparison = load_json(COMPARISON_PATH)
    characteristics = load_json(CHARACTERISTICS_PATH)
    if not isinstance(comparison, dict):
        return {"ok": False, "error": "missing alarm comparison"}
    if not isinstance(characteristics, dict):
        return {"ok": False, "error": "missing Homebridge characteristics"}

    indexed_characteristics = characteristic_index(characteristics)
    repairs: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in comparison.get("stale") or []:
        if not isinstance(row, dict):
            continue
        if row.get("portalGroup") not in {"sensors", "lights", "partitions"}:
            skipped.append({"device": row.get("device"), "reason": "unsupported portal group"})
            continue
        characteristic = str(row.get("homebridgeCharacteristic") or "")
        portal_state = str(row.get("portalState") or "")
        value = desired_value(characteristic, portal_state)
        if value is None:
            skipped.append({"device": row.get("device"), "reason": "unsupported state or characteristic"})
            continue
        device = str(row.get("device") or "")
        match = indexed_characteristics.get((device, characteristic))
        if not match:
            skipped.append({"device": device, "reason": "characteristic cache row not found"})
            continue
        cache_file = match.get("cacheFile")
        if not cache_file:
            skipped.append({"device": device, "reason": "cache file not recorded"})
            continue
        repairs.append(
            {
                "device": device,
                "portalState": portal_state,
                "characteristic": characteristic,
                "desiredValue": value,
                "result": repair_cache_file(
                    HOMEBRIDGE_ACCESSORY_DIR / str(cache_file),
                    str(row.get("portalDeviceId") or ""),
                    device,
                    characteristic,
                    value,
                ),
            }
        )

    return {
        "ok": all(item.get("result", {}).get("ok") for item in repairs),
        "comparisonGeneratedAt": comparison.get("generatedAt"),
        "staleCount": comparison.get("staleCount"),
        "changedCount": len([item for item in repairs if item.get("result", {}).get("changed")]),
        "repairs": repairs,
        "skipped": skipped,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair stale Alarm.com sensor state in the Homebridge child cache.")
    parser.add_argument(
        "--force-outside-runtime",
        action="store_true",
        help="allow live Homebridge cache repair when this script is not running from the runtime root",
    )
    args = parser.parse_args()
    result = repair_from_latest_comparison(allow_outside_runtime=args.force_outside_runtime)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
