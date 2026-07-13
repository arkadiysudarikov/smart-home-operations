#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path.home() / "Library" / "Application Support" / "SmartHomeMonitor"
CONFIG_PATH = ROOT / "config" / "sources.json"
HOMEBRIDGE_CONFIG = Path.home() / ".homebridge" / "config.json"
BACKUP_DIR = Path.home() / ".homebridge" / "codex-backups"
ALARM_BUBBLER_ID = "104430779-1234"
BUBBLER_NAME = "🐠 Bubbler"
BUBBLER_UUID_BASE = "Bubbler"
CALENDAR_PREFIX = "📅 "
RETIRED_ACTION_IDS = {"office-restart"}
MANAGED_VIRTUAL_SENSOR_PREFIXES = ("smart_home_", "home_status_")
HOME_STATUS_PLATFORM = "HomeStatusDashboard"


def running_from_runtime_root() -> bool:
    return ROOT.resolve() == RUNTIME_ROOT.resolve()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def virtual_accessories() -> list[dict[str, Any]]:
    config = load_json(CONFIG_PATH)
    return [
        {
            "id": item["id"],
            "name": item["name"],
            "type": "Switch",
            "defaultState": "off",
            "sensor": {
                "type": "OccupancySensor",
                "behavior": "MIRROR",
            },
            "enableWebhook": True,
            "resetOnRestart": False,
        }
        for item in config["homekit_virtual_sensors"]["accessories"]
    ]


def apply_homekit_name_overrides(homebridge: dict[str, Any]) -> None:
    for platform in homebridge.get("platforms", []):
        if platform.get("platform") != "Alarmdotcom":
            continue
        aliases = platform.setdefault("deviceAliases", [])
        alias = next((item for item in aliases if item.get("id") == ALARM_BUBBLER_ID), None)
        if alias is None:
            aliases.append({"id": ALARM_BUBBLER_ID, "name": BUBBLER_NAME})
        else:
            alias["name"] = BUBBLER_NAME
        break

    for platform in homebridge.get("platforms", []):
        if platform.get("platform") != "CalendarScheduler":
            continue
        for calendar in platform.get("calendars", []):
            calendar_name = str(calendar.get("calendarName") or "").strip()
            if calendar_name:
                calendar["calendarDisplayName"] = f"{CALENDAR_PREFIX}{calendar_name}"
            for event in calendar.get("calendarEvents", []):
                event_name = str(event.get("eventName") or "").strip()
                if event_name:
                    event["eventDisplayName"] = f"{CALENDAR_PREFIX}{event_name}"
        break

    for platform in homebridge.get("platforms", []):
        if platform.get("platform") == "SmartHomeActions" and isinstance(platform.get("actions"), list):
            platform["actions"] = [
                action for action in platform["actions"]
                if action.get("id") not in RETIRED_ACTION_IDS
            ]
            break

    for accessory in homebridge.get("accessories", []):
        if accessory.get("accessory") != "DelaySwitch":
            continue
        if accessory.get("name") not in {BUBBLER_UUID_BASE, BUBBLER_NAME} and accessory.get("uuid_base") != BUBBLER_UUID_BASE:
            continue
        accessory.setdefault("uuid_base", BUBBLER_UUID_BASE)
        accessory["name"] = BUBBLER_NAME
        break


def remove_retired_home_status_dashboard(homebridge: dict[str, Any]) -> None:
    platforms = homebridge.setdefault("platforms", [])
    platforms[:] = [item for item in platforms if item.get("platform") != HOME_STATUS_PLATFORM]


def main() -> int:
    parser = argparse.ArgumentParser(description="Install Smart Home virtual status sensors into the HomebridgeDummy platform.")
    parser.add_argument(
        "--force-outside-runtime",
        action="store_true",
        help="allow live Homebridge config writes when this script is not running from the runtime root",
    )
    args = parser.parse_args()
    if not args.force_outside_runtime and not running_from_runtime_root():
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "refusing to update live Homebridge config outside the runtime root",
                    "sourceRoot": str(ROOT),
                    "runtimeRoot": str(RUNTIME_ROOT),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1

    homebridge = load_json(HOMEBRIDGE_CONFIG)
    original = copy.deepcopy(homebridge)
    apply_homekit_name_overrides(homebridge)
    remove_retired_home_status_dashboard(homebridge)
    target = None
    for platform in homebridge.get("platforms", []):
        if platform.get("platform") == "HomebridgeDummy":
            target = platform
            break
    if target is None:
        target = {"name": "Homebridge Dummy", "platform": "HomebridgeDummy", "accessories": []}
        homebridge.setdefault("platforms", []).append(target)

    desired = virtual_accessories()
    desired_by_id = {item["id"]: item for item in desired}
    existing = target.setdefault("accessories", [])
    kept = [
        item
        for item in existing
        if item.get("id") not in desired_by_id
        and not str(item.get("id") or "").startswith(MANAGED_VIRTUAL_SENSOR_PREFIXES)
    ]
    target["accessories"] = kept + desired
    target.setdefault("name", "Homebridge Dummy")
    if "webhookConfig" not in target:
        target["webhookConfig"] = {"port": 63743, "disableSSL": True}
    else:
        target["webhookConfig"]["port"] = 63743
        target["webhookConfig"]["disableSSL"] = True

    if homebridge == original:
        print("Virtual sensors already installed; no Homebridge config changes needed.")
        return 0

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = BACKUP_DIR / f"config-before-smart-home-virtual-sensors-{stamp}.json"
    shutil.copy2(HOMEBRIDGE_CONFIG, backup_path)
    HOMEBRIDGE_CONFIG.write_text(json.dumps(homebridge, indent=4) + "\n")
    print(backup_path)
    print("Installed virtual sensors:")
    for item in desired:
        print(f"- {item['name']} ({item['id']})")
    print(f"- {BUBBLER_NAME} (Alarm.com light and delay switch)")
    print(f"- {CALENDAR_PREFIX.strip()} Calendar Scheduler display names")
    print("- Home Status read-only sensor dashboard")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
