#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "install_homekit_virtual_sensors",
    ROOT / "scripts" / "install_homekit_virtual_sensors.py",
)
assert SPEC and SPEC.loader
install_homekit_virtual_sensors = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install_homekit_virtual_sensors)


class InstallHomeKitVirtualSensorsTest(unittest.TestCase):
    def test_configured_tile_names_describe_their_active_condition(self) -> None:
        config = json.loads((ROOT / "config" / "sources.json").read_text())
        names_by_id = {
            item["id"]: item["name"]
            for item in config["homekit_virtual_sensors"]["accessories"]
        }

        self.assertEqual(
            names_by_id,
            {
                "smart_home_battery_critical_v2": "🪫 BATTERY CRIT",
                "smart_home_battery_low_v2": "🪫 BATTERY LOW",
                "smart_home_alarm_degraded_v2": "🔐 ALARM LOGIN",
                "smart_home_unifi_auth_failed_v2": "🔐 UNIFI LOGIN",
                "smart_home_smarthq_auth_failed_v2": "🔐 SMARTHQ LOGIN",
                "smart_home_sense_auth_failed_v2": "🔐 SENSE LOGIN",
                "smart_home_tahoma_auth_failed_v2": "🔐 TAHOMA LOGIN",
                "smart_home_high_load_v2": "⚡ High Usage",
                "smart_home_grid_importing_v2": "⬅️ From Grid",
                "smart_home_grid_exporting_v2": "☀️ To Grid",
                "smart_home_battery_charging_v2": "☀️🔋 Charging",
                "smart_home_energy_data_stale_v2": "🕒 ENVOY STALE",
                "smart_home_sce_data_stale_v2": "🕒 SCE STALE",
                "smart_home_alarm_media_missing_v2": "🎥 CLIPS MISSING",
                "smart_home_ev_charging_v2": "🔋 Car Charging",
            },
        )

    def test_visible_names_avoid_monitor_jargon(self) -> None:
        config = json.loads((ROOT / "config" / "sources.json").read_text())
        tiles = config["homekit_virtual_sensors"]["accessories"]
        tile_names = [item["name"] for item in tiles]
        action_source = (ROOT / "plugins" / "homebridge-smart-home-actions" / "index.js").read_text()
        default_actions = action_source.split("];", 1)[0]
        action_names = re.findall(r'name: "([^"]+)"', default_actions)

        alert_ids = {
            "smart_home_battery_critical_v2",
            "smart_home_battery_low_v2",
            "smart_home_alarm_degraded_v2",
            "smart_home_unifi_auth_failed_v2",
            "smart_home_smarthq_auth_failed_v2",
            "smart_home_sense_auth_failed_v2",
            "smart_home_tahoma_auth_failed_v2",
            "smart_home_energy_data_stale_v2",
            "smart_home_sce_data_stale_v2",
            "smart_home_alarm_media_missing_v2",
        }
        informational_ids = {
            "smart_home_high_load_v2",
            "smart_home_grid_importing_v2",
            "smart_home_grid_exporting_v2",
            "smart_home_battery_charging_v2",
            "smart_home_ev_charging_v2",
        }
        for tile in tiles:
            visible_text = tile["name"].split(" ", 1)[1]
            if tile["id"] in alert_ids:
                self.assertTrue(visible_text.isupper(), tile["name"])
            if tile["id"] in informational_ids:
                self.assertFalse(visible_text.isupper(), tile["name"])
        for name in action_names:
            self.assertFalse(name.isupper(), name)

        for name in tile_names + action_names:
            self.assertLessEqual(len(name), 16, f"visible name is too long for a Home tile: {name}")
            words = set(name.lower().split())
            self.assertTrue(
                words.isdisjoint({"auth", "issue", "reconcile", "surplus"}),
                f"visible name contains monitor jargon: {name}",
            )

    def test_refuses_live_homebridge_config_write_outside_runtime_root_by_default(self) -> None:
        stdout = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout),
            mock.patch.object(install_homekit_virtual_sensors, "ROOT", Path("/repo")),
            mock.patch.object(install_homekit_virtual_sensors, "RUNTIME_ROOT", Path("/runtime")),
            mock.patch.object(install_homekit_virtual_sensors, "load_json") as load_json,
            mock.patch.object(sys, "argv", ["install_homekit_virtual_sensors.py"]),
        ):
            self.assertEqual(install_homekit_virtual_sensors.main(), 1)

        self.assertIn("outside the runtime root", stdout.getvalue())
        load_json.assert_not_called()

    def test_force_outside_runtime_updates_only_patched_homebridge_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_config = root / "sources.json"
            homebridge_config = root / "homebridge.json"
            backup_dir = root / "backups"
            source_config.write_text(
                json.dumps(
                    {
                        "homekit_virtual_sensors": {
                            "accessories": [
                                {"id": "smart_home_test", "name": "Test Tile"},
                            ]
                        }
                    }
                )
                + "\n"
            )
            homebridge_config.write_text(json.dumps({"platforms": []}) + "\n")

            with (
                contextlib.redirect_stdout(io.StringIO()),
                mock.patch.object(install_homekit_virtual_sensors, "ROOT", Path("/repo")),
                mock.patch.object(install_homekit_virtual_sensors, "RUNTIME_ROOT", Path("/runtime")),
                mock.patch.object(install_homekit_virtual_sensors, "CONFIG_PATH", source_config),
                mock.patch.object(install_homekit_virtual_sensors, "HOMEBRIDGE_CONFIG", homebridge_config),
                mock.patch.object(install_homekit_virtual_sensors, "BACKUP_DIR", backup_dir),
                mock.patch.object(sys, "argv", ["install_homekit_virtual_sensors.py", "--force-outside-runtime"]),
            ):
                self.assertEqual(install_homekit_virtual_sensors.main(), 0)

            payload = json.loads(homebridge_config.read_text())
            platform = payload["platforms"][0]
            self.assertEqual(platform["platform"], "HomebridgeDummy")
            self.assertEqual(platform["accessories"][0]["name"], "Test Tile")
            self.assertTrue(any(backup_dir.iterdir()))

    def test_install_removes_retired_managed_tiles_and_preserves_unmanaged_accessories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_config = root / "sources.json"
            homebridge_config = root / "homebridge.json"
            backup_dir = root / "backups"
            source_config.write_text(
                json.dumps(
                    {
                        "homekit_virtual_sensors": {
                            "accessories": [{"id": "smart_home_current", "name": "Current Tile"}]
                        }
                    }
                )
                + "\n"
            )
            homebridge_config.write_text(
                json.dumps(
                    {
                        "platforms": [
                            {
                                "platform": "HomebridgeDummy",
                                "accessories": [
                                    {"id": "smart_home_retired", "name": "Retired Tile"},
                                    {"id": "other_accessory", "name": "Other Accessory"},
                                ],
                            }
                        ]
                    }
                )
                + "\n"
            )

            with (
                contextlib.redirect_stdout(io.StringIO()),
                mock.patch.object(install_homekit_virtual_sensors, "ROOT", Path("/repo")),
                mock.patch.object(install_homekit_virtual_sensors, "RUNTIME_ROOT", Path("/runtime")),
                mock.patch.object(install_homekit_virtual_sensors, "CONFIG_PATH", source_config),
                mock.patch.object(install_homekit_virtual_sensors, "HOMEBRIDGE_CONFIG", homebridge_config),
                mock.patch.object(install_homekit_virtual_sensors, "BACKUP_DIR", backup_dir),
                mock.patch.object(sys, "argv", ["install_homekit_virtual_sensors.py", "--force-outside-runtime"]),
            ):
                self.assertEqual(install_homekit_virtual_sensors.main(), 0)

            payload = json.loads(homebridge_config.read_text())
            accessories = payload["platforms"][0]["accessories"]
            self.assertEqual(
                [(item["id"], item["name"]) for item in accessories],
                [("other_accessory", "Other Accessory"), ("smart_home_current", "Current Tile")],
            )


if __name__ == "__main__":
    unittest.main()
