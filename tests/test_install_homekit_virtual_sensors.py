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
                "smart_home_battery_critical_v2": "⚠️ BATTERY CRIT",
                "smart_home_battery_low_v2": "⚠️ BATTERY LOW",
                "smart_home_alarm_degraded_v2": "⚠️ ALARM LOGIN",
                "smart_home_unifi_auth_failed_v2": "⚠️ UNIFI LOGIN",
                "smart_home_smarthq_auth_failed_v2": "⚠️ SMARTHQ LOGIN",
                "smart_home_sense_auth_failed_v2": "⚠️ SENSE OFFLINE",
                "smart_home_tahoma_auth_failed_v2": "⚠️ TAHOMA LOGIN",
                "smart_home_high_load_v2": "☀️ High Usage",
                "smart_home_grid_importing_v2": "☀️ From Grid",
                "smart_home_grid_exporting_v2": "☀️ To Grid",
                "smart_home_battery_charging_v2": "🔋 Charging",
                "smart_home_battery_discharging_v2": "🔋 Discharging",
                "smart_home_energy_data_stale_v2": "⚠️ ENVOY STALE",
                "smart_home_sce_data_stale_v2": "⚠️ SCE STALE",
                "smart_home_alarm_media_missing_v2": "⚠️ CLIPS MISSING",
                "smart_home_ev_charging_v2": "🔋 Car Charging",
                "smart_home_washer_finished_v1": "🧺 Washer Done",
                "smart_home_washer_unload_v1": "🧺 Unload Washer",
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
            "smart_home_battery_discharging_v2",
            "smart_home_ev_charging_v2",
            "smart_home_washer_finished_v1",
            "smart_home_washer_unload_v1",
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
            visible_length = len(name.replace("\ufe0f", ""))
            self.assertLessEqual(visible_length, 16, f"visible name is too long for a Home tile: {name}")
            words = set(name.lower().split())
            self.assertTrue(
                words.isdisjoint({"auth", "issue", "reconcile", "surplus"}),
                f"visible name contains monitor jargon: {name}",
            )

        household_energy_ids = {
            "smart_home_high_load_v2",
            "smart_home_grid_importing_v2",
            "smart_home_grid_exporting_v2",
        }
        charging_ids = {
            "smart_home_battery_charging_v2",
            "smart_home_battery_discharging_v2",
            "smart_home_ev_charging_v2",
        }
        for tile in tiles:
            if tile["id"] in household_energy_ids:
                self.assertTrue(tile["name"].startswith("☀️ "), tile["name"])
                self.assertEqual(tile["name"].count("☀️"), 1, tile["name"])
            if tile["id"] in alert_ids:
                self.assertTrue(tile["name"].startswith("⚠️ "), tile["name"])
            if tile["id"] in charging_ids:
                self.assertTrue(tile["name"].startswith("🔋 "), tile["name"])

        approved_prefixes = {"⚠️", "☀️", "🔋", "🧺", "⚙️", "🛡️", "📅", "🐠"}
        grouped_names = tile_names + action_names + [
            install_homekit_virtual_sensors.BUBBLER_NAME,
            f"{install_homekit_virtual_sensors.CALENDAR_PREFIX}Automation",
        ]
        self.assertEqual(
            {name.split(" ", 1)[0] for name in grouped_names},
            approved_prefixes,
        )

    def test_removes_retired_home_status_dashboard(self) -> None:
        homebridge = {
            "platforms": [
                {"platform": "HomeStatusDashboard", "name": "Home Status Core"},
                {"platform": "HomebridgeDummy", "name": "Dummy"},
            ]
        }

        install_homekit_virtual_sensors.remove_retired_home_status_dashboard(homebridge)

        self.assertEqual(homebridge["platforms"], [{"platform": "HomebridgeDummy", "name": "Dummy"}])

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
            platform = next(item for item in payload["platforms"] if item["platform"] == "HomebridgeDummy")
            self.assertEqual(platform["platform"], "HomebridgeDummy")
            self.assertEqual(platform["accessories"][0]["name"], "Test Tile")
            self.assertTrue(any(backup_dir.iterdir()))

    def test_virtual_accessories_support_motion_sensors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "sources.json"
            config_path.write_text(
                json.dumps(
                    {
                        "homekit_virtual_sensors": {
                            "accessories": [
                                {"id": "smart_home_test", "name": "Test", "sensor_type": "MotionSensor"}
                            ]
                        }
                    }
                )
            )
            with mock.patch.object(install_homekit_virtual_sensors, "CONFIG_PATH", config_path):
                accessories = install_homekit_virtual_sensors.virtual_accessories()
            self.assertEqual(accessories[0]["sensor"]["type"], "MotionSensor")

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
                                    {"id": "home_status_retired", "name": "Retired Mirror"},
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

    def test_homekit_name_overrides_preserve_bubbler_identity(self) -> None:
        homebridge = {
            "accessories": [
                {
                    "accessory": "DelaySwitch",
                    "name": "Bubbler",
                    "delay": 5,
                }
            ],
            "platforms": [
                {
                    "platform": "Alarmdotcom",
                    "deviceAliases": [{"id": "existing", "name": "Existing Alias"}],
                },
                {
                    "platform": "CalendarScheduler",
                    "calendars": [
                        {
                            "calendarName": "Automation",
                            "calendarEvents": [
                                {"eventName": "Peak Rate"},
                                {"eventName": "Test Event"},
                            ],
                        }
                    ],
                },
                {
                    "platform": "SmartHomeActions",
                    "actions": [
                        {"id": "check", "name": "Run Home Check"},
                        {"id": "office-restart", "name": "Restart Office Shades"},
                    ],
                },
            ],
        }

        install_homekit_virtual_sensors.apply_homekit_name_overrides(homebridge)

        bubbler = homebridge["accessories"][0]
        self.assertEqual(bubbler["name"], "🐠 Bubbler")
        self.assertEqual(bubbler["uuid_base"], "Bubbler")
        self.assertEqual(
            homebridge["platforms"][0]["deviceAliases"],
            [
                {"id": "existing", "name": "Existing Alias"},
                {"id": "104430779-1234", "name": "🐠 Bubbler"},
            ],
        )
        calendar = homebridge["platforms"][1]["calendars"][0]
        self.assertEqual(calendar["calendarName"], "Automation")
        self.assertEqual(calendar["calendarDisplayName"], "📅 Automation")
        self.assertEqual(
            [event["eventDisplayName"] for event in calendar["calendarEvents"]],
            ["📅 Peak Rate", "📅 Test Event"],
        )
        self.assertEqual(
            homebridge["platforms"][2]["actions"],
            [{"id": "check", "name": "Run Home Check"}],
        )


if __name__ == "__main__":
    unittest.main()
