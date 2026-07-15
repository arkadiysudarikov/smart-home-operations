#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("washer_notifier", ROOT / "scripts" / "washer_notifier.py")
assert SPEC and SPEC.loader
washer_notifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(washer_notifier)
TZ = ZoneInfo("America/Los_Angeles")


def config() -> dict:
    return {
        "max_snapshot_age_minutes": 10,
        "minimum_cycle_minutes": 10,
        "minimum_running_samples": 2,
        "reminder_minutes": 20,
        "pulse_seconds": 120,
        "announcement_start_hour": 8,
        "announcement_end_hour": 21,
    }


class WasherNotifierTest(unittest.TestCase):
    def test_initial_idle_sample_does_not_notify(self) -> None:
        now = datetime(2026, 7, 15, 12, 0, tzinfo=TZ)
        state, actions = washer_notifier.evolve_state(
            {}, {"fresh": True, "inUse": False, "doorOpen": False}, now, config()
        )
        self.assertEqual(actions, [])
        self.assertFalse(state["lastInUse"])

    def test_running_then_finished_sends_phone_and_daytime_announcement(self) -> None:
        now = datetime(2026, 7, 15, 12, 0, tzinfo=TZ)
        state = {
            "lastInUse": True,
            "armed": True,
            "cycleStartedAt": (now - timedelta(hours=1)).isoformat(),
            "runningSamples": 4,
            "completedCycles": 0,
        }
        evolved, actions = washer_notifier.evolve_state(
            state, {"fresh": True, "inUse": False, "doorOpen": False}, now, config()
        )
        self.assertEqual(actions, ["finish_on", "notify_finish", "announce_finish"])
        self.assertTrue(evolved["awaitingUnload"])
        self.assertEqual(evolved["completedCycles"], 1)

    def test_finish_during_quiet_hours_does_not_announce(self) -> None:
        now = datetime(2026, 7, 15, 23, 0, tzinfo=TZ)
        state = {
            "lastInUse": True,
            "armed": True,
            "cycleStartedAt": (now - timedelta(hours=1)).isoformat(),
            "runningSamples": 4,
        }
        _, actions = washer_notifier.evolve_state(
            state, {"fresh": True, "inUse": False, "doorOpen": False}, now, config()
        )
        self.assertEqual(actions, ["finish_on", "notify_finish"])

    def test_reminds_once_after_twenty_minutes_if_door_stays_closed(self) -> None:
        now = datetime(2026, 7, 15, 12, 21, tzinfo=TZ)
        state = {
            "lastInUse": False,
            "awaitingUnload": True,
            "finishedAt": (now - timedelta(minutes=21)).isoformat(),
            "reminderSent": False,
        }
        evolved, actions = washer_notifier.evolve_state(
            state, {"fresh": True, "inUse": False, "doorOpen": False}, now, config()
        )
        self.assertEqual(actions, ["reminder_on", "notify_reminder"])
        self.assertTrue(evolved["reminderSent"])

        _, second_actions = washer_notifier.evolve_state(
            evolved, {"fresh": True, "inUse": False, "doorOpen": False}, now + timedelta(minutes=5), config()
        )
        self.assertNotIn("notify_reminder", second_actions)

    def test_opening_door_clears_unload_state_and_sensors(self) -> None:
        now = datetime(2026, 7, 15, 12, 10, tzinfo=TZ)
        state = {
            "lastInUse": False,
            "awaitingUnload": True,
            "finishedAt": (now - timedelta(minutes=5)).isoformat(),
            "reminderSent": False,
        }
        evolved, actions = washer_notifier.evolve_state(
            state, {"fresh": True, "inUse": False, "doorOpen": True}, now, config()
        )
        self.assertEqual(actions, ["finish_off", "reminder_off"])
        self.assertFalse(evolved["awaitingUnload"])

    def test_stale_snapshot_cannot_finish_cycle(self) -> None:
        now = datetime(2026, 7, 15, 12, 0, tzinfo=TZ)
        state = {"lastInUse": True, "armed": True, "runningSamples": 8}
        evolved, actions = washer_notifier.evolve_state(
            state, {"fresh": False, "inUse": False, "doorOpen": False}, now, config()
        )
        self.assertEqual(actions, [])
        self.assertTrue(evolved["lastInUse"])

    def test_reads_smarthq_characteristics_from_snapshot(self) -> None:
        now = datetime(2026, 7, 15, 12, 0, tzinfo=TZ)
        latest = {
            "captured_at": (now - timedelta(minutes=1)).isoformat(),
            "homeEvents": {
                "currentCharacteristics": {
                    "in-use": {"accessory": "Washer", "service": "Washer", "characteristic": "InUse", "value": 1},
                    "door": {"accessory": "Washer", "service": "Washer Door", "characteristic": "ContactSensorState", "value": 0},
                }
            },
        }
        current = washer_notifier.current_washer_state(latest, config(), now)
        self.assertTrue(current["fresh"])
        self.assertTrue(current["inUse"])
        self.assertFalse(current["doorOpen"])


if __name__ == "__main__":
    unittest.main()
