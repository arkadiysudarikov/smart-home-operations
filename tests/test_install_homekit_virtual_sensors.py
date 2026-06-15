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
