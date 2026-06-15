#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "sources.json"
HOMEBRIDGE_CONFIG = Path.home() / ".homebridge" / "config.json"
BACKUP_DIR = Path.home() / ".homebridge" / "codex-backups"


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


def main() -> int:
    homebridge = load_json(HOMEBRIDGE_CONFIG)
    original = copy.deepcopy(homebridge)
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
    kept = [item for item in existing if item.get("id") not in desired_by_id]
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
