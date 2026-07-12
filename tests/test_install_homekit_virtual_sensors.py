#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
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
                "smart_home_battery_critical": "Battery Critical",
                "smart_home_battery_low": "Battery Low",
                "smart_home_alarm_degraded": "Alarm Issue",
                "smart_home_alarm_cache_stale": "Alarm Cache Stale",
                "smart_home_alarm_activity_degraded": "Alarm Activity Issue",
                "smart_home_unifi_auth_failed": "UniFi Auth",
                "smart_home_smarthq_auth_failed": "SmartHQ Auth",
                "smart_home_sense_auth_failed": "Sense Auth",
                "smart_home_tahoma_auth_failed": "TaHoma Auth",
                "smart_home_office_tahoma_offline": "Office Offline",
                "smart_home_high_load": "Load High",
                "smart_home_warnings_high": "Warnings High",
                "smart_home_actions_online": "Actions Online",
                "smart_home_sce_fresh": "SCE Fresh",
                "smart_home_alarm_cache_clean": "Alarm Cache OK",
                "smart_home_grid_importing": "Grid Import",
                "smart_home_grid_exporting": "Grid Export",
                "smart_home_solar_surplus": "Solar Surplus",
                "smart_home_energy_data_stale": "Energy Stale",
                "smart_home_sce_data_stale": "SCE Stale",
                "smart_home_energy_check": "Energy Reconcile",
                "smart_home_energy_source_stale": "Source Stale",
                "smart_home_alarm_energy_recapture": "Alarm Energy Issue",
                "smart_home_alarm_media_missing": "Alarm Media Missing",
                "smart_home_ev_charging": "EV Charging",
                "smart_home_ev_heavy": "EV Share High",
            },
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


if __name__ == "__main__":
    unittest.main()
