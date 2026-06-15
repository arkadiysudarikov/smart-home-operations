#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("recover_unifi_occupancy", ROOT / "scripts" / "recover_unifi_occupancy.py")
assert SPEC and SPEC.loader
recover_unifi_occupancy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recover_unifi_occupancy)


class RecoverUniFiOccupancyTest(unittest.TestCase):
    def test_detects_api_warning_without_treating_sense_401_as_unifi_auth(self) -> None:
        snapshot = {
            "homebridge": {
                "logs": {
                    "recentWarnings": [
                        '[homebridge-unifi-occupancy] ERROR: Failed to load device fingerprints StatusCodeError: 504 - {"error":{"code":504,"message":"Gateway Timeout"}}',
                        "[Sense Energy Meter] Error event on sense: Unexpected server response: 401.",
                    ]
                }
            }
        }

        self.assertTrue(recover_unifi_occupancy.has_unifi_api_warning(snapshot))
        self.assertFalse(recover_unifi_occupancy.has_unifi_auth_warning(snapshot))

    def test_counts_active_unifi_occupancy_from_snapshot(self) -> None:
        snapshot = {
            "homebridge": {
                "logs": {
                    "unifiOccupancy": {
                        "trackedAccessories": 3,
                        "active": ["Level 2 m2-office-mini", "Express iPad"],
                    }
                }
            }
        }

        self.assertEqual(recover_unifi_occupancy.unifi_active_count(snapshot), 2)
        self.assertEqual(recover_unifi_occupancy.unifi_tracked_count(snapshot), 3)

    def test_finds_unifi_platform_config(self) -> None:
        config = {
            "platforms": [
                {"platform": "Alarmdotcom"},
                {"platform": "UnifiOccupancy", "_bridge": {"port": 52746}},
            ]
        }

        self.assertEqual(recover_unifi_occupancy.unifi_platform(config)["_bridge"]["port"], 52746)

    def test_restart_decision_requires_api_warning_by_default(self) -> None:
        self.assertFalse(recover_unifi_occupancy.should_restart_for_stale_occupancy(False, 0, {}))
        self.assertTrue(recover_unifi_occupancy.should_restart_for_stale_occupancy(True, 5, {}))
        self.assertTrue(
            recover_unifi_occupancy.should_restart_for_stale_occupancy(
                False,
                0,
                {"restart_when_no_tracked_accessories": True},
            )
        )

    def test_main_refuses_live_recovery_outside_runtime_root_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status_path = Path(tmp) / "latest_unifi_occupancy_recovery.json"
            with (
                mock.patch.object(recover_unifi_occupancy, "ROOT", Path("/repo")),
                mock.patch.object(recover_unifi_occupancy, "RUNTIME_ROOT", Path("/runtime")),
                mock.patch.object(recover_unifi_occupancy, "STATUS_PATH", status_path),
                mock.patch.object(recover_unifi_occupancy, "DATA_DIR", Path(tmp)),
                mock.patch.object(recover_unifi_occupancy, "load_config") as load_config,
                mock.patch.object(sys, "argv", ["recover_unifi_occupancy.py"]),
            ):
                self.assertEqual(recover_unifi_occupancy.main(), 1)

            payload = json.loads(status_path.read_text())
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["action"], "none")
            self.assertIn("outside the runtime root", payload["error"])
            load_config.assert_not_called()

    def test_force_outside_runtime_allows_disabled_config_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status_path = Path(tmp) / "latest_unifi_occupancy_recovery.json"
            with (
                mock.patch.object(recover_unifi_occupancy, "ROOT", Path("/repo")),
                mock.patch.object(recover_unifi_occupancy, "RUNTIME_ROOT", Path("/runtime")),
                mock.patch.object(recover_unifi_occupancy, "STATUS_PATH", status_path),
                mock.patch.object(recover_unifi_occupancy, "DATA_DIR", Path(tmp)),
                mock.patch.object(
                    recover_unifi_occupancy,
                    "load_config",
                    return_value={"unifi_occupancy_recovery": {"enabled": False}},
                ),
                mock.patch.object(sys, "argv", ["recover_unifi_occupancy.py", "--force-outside-runtime"]),
            ):
                self.assertEqual(recover_unifi_occupancy.main(), 0)

            payload = json.loads(status_path.read_text())
            self.assertTrue(payload["ok"])
            self.assertFalse(payload["enabled"])


if __name__ == "__main__":
    unittest.main()
