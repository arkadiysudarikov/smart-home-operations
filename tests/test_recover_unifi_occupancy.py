#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
