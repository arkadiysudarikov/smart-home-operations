#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "repair_alarm_homebridge_cache",
    ROOT / "scripts" / "repair_alarm_homebridge_cache.py",
)
assert SPEC and SPEC.loader
repair_alarm_homebridge_cache = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair_alarm_homebridge_cache)


class RepairAlarmHomebridgeCacheTest(unittest.TestCase):
    def patch_module(self, **replacements: Any) -> None:
        self._restore = getattr(self, "_restore", {})
        for name, replacement in replacements.items():
            if name not in self._restore:
                self._restore[name] = getattr(repair_alarm_homebridge_cache, name)
            setattr(repair_alarm_homebridge_cache, name, replacement)

    def tearDown(self) -> None:
        for name, original in getattr(self, "_restore", {}).items():
            setattr(repair_alarm_homebridge_cache, name, original)

    def test_refuses_live_cache_repair_outside_runtime_root_by_default(self) -> None:
        calls: list[Path] = []

        self.patch_module(
            ROOT=Path("/repo"),
            RUNTIME_ROOT=Path("/runtime"),
            repair_cache_file=lambda *args: calls.append(args[0]) or {"ok": True},
        )

        result = repair_alarm_homebridge_cache.repair_from_latest_comparison()

        self.assertFalse(result["ok"])
        self.assertEqual(result["changedCount"], 0)
        self.assertIn("outside the runtime root", result["error"])
        self.assertEqual(calls, [])

    def test_force_allows_existing_repair_flow_outside_runtime_root(self) -> None:
        self.patch_module(
            ROOT=Path("/repo"),
            RUNTIME_ROOT=Path("/runtime"),
            HOMEBRIDGE_ACCESSORY_DIR=Path("/homebridge/accessories"),
            load_json=lambda path: {
                "comparison": {
                    "stale": [
                        {
                            "portalGroup": "sensors",
                            "homebridgeCharacteristic": "ContactSensorState",
                            "portalState": "Open",
                            "device": "Sideyard Gate",
                            "portalDeviceId": "device-1",
                        }
                    ],
                    "staleCount": 1,
                },
                "characteristics": {
                    "row": {
                        "plugin": "homebridge-node-alarm-dot-com",
                        "accessory": "Sideyard Gate",
                        "characteristic": "ContactSensorState",
                        "cacheFile": "cachedAccessories.alarm",
                    }
                },
            }["comparison" if path == repair_alarm_homebridge_cache.COMPARISON_PATH else "characteristics"],
            repair_cache_file=lambda *args: {
                "ok": True,
                "changed": True,
                "cacheFile": str(args[0]),
            },
        )

        result = repair_alarm_homebridge_cache.repair_from_latest_comparison(allow_outside_runtime=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["changedCount"], 1)
        self.assertEqual(
            result["repairs"][0]["result"]["cacheFile"],
            "/homebridge/accessories/cachedAccessories.alarm",
        )

    def test_repairs_alarm_light_on_characteristic(self) -> None:
        self.patch_module(
            ROOT=Path("/runtime"),
            RUNTIME_ROOT=Path("/runtime"),
            HOMEBRIDGE_ACCESSORY_DIR=Path("/homebridge/accessories"),
            load_json=lambda path: {
                "comparison": {
                    "stale": [
                        {
                            "portalGroup": "lights",
                            "homebridgeCharacteristic": "On",
                            "portalState": "Off",
                            "device": "Kitchen Light",
                            "portalDeviceId": "device-2",
                        }
                    ],
                    "staleCount": 1,
                },
                "characteristics": {
                    "row": {
                        "plugin": "homebridge-node-alarm-dot-com",
                        "accessory": "Kitchen Light",
                        "characteristic": "On",
                        "cacheFile": "cachedAccessories.alarm",
                    }
                },
            }["comparison" if path == repair_alarm_homebridge_cache.COMPARISON_PATH else "characteristics"],
            repair_cache_file=lambda *args: {
                "ok": True,
                "changed": True,
                "cacheFile": str(args[0]),
                "value": args[4],
            },
        )

        result = repair_alarm_homebridge_cache.repair_from_latest_comparison()

        self.assertTrue(result["ok"])
        self.assertEqual(result["repairs"][0]["desiredValue"], False)
        self.assertEqual(result["repairs"][0]["result"]["cacheFile"], "/homebridge/accessories/cachedAccessories.alarm")

    def test_repeated_repairs_do_not_overwrite_existing_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_path = root / "cachedAccessories.alarm"
            backup_dir = root / "backups"
            cache_path.write_text(
                json.dumps(
                    [
                        {
                            "displayName": "Kitchen Light",
                            "context": {"state": True},
                            "services": [{"characteristics": [{"displayName": "On", "value": True}]}],
                        }
                    ]
                )
            )
            self.patch_module(BACKUP_DIR=backup_dir)

            first = repair_alarm_homebridge_cache.repair_cache_file(cache_path, "", "Kitchen Light", "On", False)
            cache_path.write_text(
                json.dumps(
                    [
                        {
                            "displayName": "Kitchen Light",
                            "context": {"state": True},
                            "services": [{"characteristics": [{"displayName": "On", "value": True}]}],
                        }
                    ]
                )
            )
            second = repair_alarm_homebridge_cache.repair_cache_file(cache_path, "", "Kitchen Light", "On", False)

            self.assertNotEqual(first["backup"], second["backup"])
            self.assertTrue(Path(first["backup"]).exists())
            self.assertTrue(Path(second["backup"]).exists())

    def test_repairs_alarm_panel_current_state_from_portal(self) -> None:
        self.patch_module(
            ROOT=Path("/runtime"),
            RUNTIME_ROOT=Path("/runtime"),
            HOMEBRIDGE_ACCESSORY_DIR=Path("/homebridge/accessories"),
            load_json=lambda path: {
                "comparison": {
                    "stale": [
                        {
                            "portalGroup": "partitions",
                            "homebridgeCharacteristic": "SecuritySystemCurrentState",
                            "portalState": "Armed stay",
                            "device": "Entry Panel",
                            "portalDeviceId": "device-3",
                        }
                    ],
                    "staleCount": 1,
                },
                "characteristics": {
                    "row": {
                        "plugin": "homebridge-node-alarm-dot-com",
                        "accessory": "Entry Panel",
                        "characteristic": "SecuritySystemCurrentState",
                        "cacheFile": "cachedAccessories.alarm",
                    }
                },
            }["comparison" if path == repair_alarm_homebridge_cache.COMPARISON_PATH else "characteristics"],
            repair_cache_file=lambda *args: {
                "ok": True,
                "changed": True,
                "cacheFile": str(args[0]),
                "value": args[4],
            },
        )

        result = repair_alarm_homebridge_cache.repair_from_latest_comparison()

        self.assertTrue(result["ok"])
        self.assertEqual(result["repairs"][0]["desiredValue"], 0)
        self.assertEqual(result["repairs"][0]["characteristic"], "SecuritySystemCurrentState")


if __name__ == "__main__":
    unittest.main()
