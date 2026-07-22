#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
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
        "maximum_venting_hours": 8,
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
                    "cycle": {"accessory": "Washer", "service": "Cycle Status", "characteristic": "MotionDetected", "value": 0},
                    "door": {"accessory": "Washer", "service": "Washer Door", "characteristic": "ContactSensorState", "value": 0},
                }
            },
        }
        current = washer_notifier.current_appliance_state(latest, {**config(), "accessory": "Washer"}, now)
        self.assertTrue(current["fresh"])
        self.assertTrue(current["inUse"])
        self.assertFalse(current["cycleActive"])
        self.assertFalse(current["doorOpen"])

    def test_prefers_fresh_direct_smarthq_state_over_stale_homebridge_cache(self) -> None:
        now = datetime(2026, 7, 22, 12, 0, tzinfo=TZ)
        latest = {
            "captured_at": now.isoformat(),
            "homeEvents": {"currentCharacteristics": {}},
        }
        direct = {
            "ok": True,
            "source": "homebridge-hap-live",
            "capturedAt": (now - timedelta(seconds=5)).isoformat(),
            "devices": {
                "washer": {"inUse": True, "cycleActive": True, "doorOpen": None},
            },
        }
        current = washer_notifier.current_appliance_state(
            latest, {**config(), "id": "washer", "accessory": "Washer"}, now, direct
        )
        self.assertTrue(current["inUse"])
        self.assertTrue(current["cycleActive"])
        self.assertIsNone(current["doorOpen"])
        self.assertEqual(current["source"], "homebridge-hap-live")

    def test_stale_direct_state_falls_back_to_homebridge_cache(self) -> None:
        now = datetime(2026, 7, 22, 12, 0, tzinfo=TZ)
        latest = {
            "captured_at": now.isoformat(),
            "homeEvents": {
                "currentCharacteristics": {
                    "in-use": {"accessory": "Washer", "service": "Washer", "characteristic": "InUse", "value": 0},
                    "cycle": {"accessory": "Washer", "service": "Cycle Status", "characteristic": "MotionDetected", "value": 0},
                    "door": {"accessory": "Washer", "service": "Washer Door", "characteristic": "ContactSensorState", "value": 0},
                }
            },
        }
        direct = {
            "ok": True,
            "capturedAt": (now - timedelta(hours=1)).isoformat(),
            "devices": {"washer": {"inUse": True, "cycleActive": True, "doorOpen": None}},
        }
        current = washer_notifier.current_appliance_state(
            latest, {**config(), "id": "washer", "accessory": "Washer"}, now, direct
        )
        self.assertFalse(current["inUse"])
        self.assertEqual(current["source"], "homebridge-cache")

    def test_migrates_running_washer_venting_without_false_wash_alert(self) -> None:
        now = datetime(2026, 7, 15, 17, 30, tzinfo=TZ)
        legacy = {
            "armed": True,
            "lastInUse": True,
            "cycleStartedAt": (now - timedelta(hours=5)).isoformat(),
            "runningSamples": 42,
        }
        evolved, actions = washer_notifier.evolve_state(
            legacy,
            {"fresh": True, "inUse": True, "cycleActive": False, "doorOpen": False},
            now,
            {**config(), "finish_signal": "cycleActive"},
        )
        self.assertEqual(actions, [])
        self.assertFalse(evolved["primaryArmed"])
        self.assertTrue(evolved["ventingArmed"])

    def test_wash_completion_alerts_while_venting_stays_active(self) -> None:
        now = datetime(2026, 7, 15, 12, 37, tzinfo=TZ)
        state = {
            "lastCycleActive": True,
            "lastInUse": True,
            "primaryArmed": True,
            "washStartedAt": (now - timedelta(hours=1)).isoformat(),
            "runningSamples": 8,
        }
        evolved, actions = washer_notifier.evolve_state(
            state,
            {"fresh": True, "inUse": True, "cycleActive": False, "doorOpen": False},
            now,
            {**config(), "finish_signal": "cycleActive"},
        )
        self.assertEqual(actions, ["finish_on", "notify_finish", "announce_finish"])
        self.assertTrue(evolved["ventingArmed"])
        self.assertTrue(evolved["awaitingUnload"])

    def test_venting_completion_sends_fan_reminder(self) -> None:
        now = datetime(2026, 7, 15, 18, 0, tzinfo=TZ)
        state = {
            "lastCycleActive": False,
            "lastInUse": True,
            "primaryArmed": False,
            "ventingArmed": True,
            "washFinishedAt": (now - timedelta(hours=5)).isoformat(),
            "reminderSent": True,
        }
        evolved, actions = washer_notifier.evolve_state(
            state,
            {"fresh": True, "inUse": False, "cycleActive": False, "doorOpen": False},
            now,
            {**config(), "finish_signal": "cycleActive"},
        )
        self.assertEqual(actions, ["venting_on", "notify_venting", "announce_venting"])
        self.assertFalse(evolved["ventingArmed"])

    def test_stale_venting_alerts_once_without_claiming_completion(self) -> None:
        now = datetime(2026, 7, 16, 8, 0, tzinfo=TZ)
        state = {
            "lastCycleActive": False,
            "lastInUse": True,
            "primaryArmed": False,
            "ventingArmed": True,
            "ventingStartedAt": (now - timedelta(hours=9)).isoformat(),
            "ventingStaleAlertSent": False,
        }
        evolved, actions = washer_notifier.evolve_state(
            state,
            {"fresh": True, "inUse": True, "cycleActive": False, "doorOpen": False},
            now,
            {**config(), "finish_signal": "cycleActive"},
        )
        self.assertEqual(actions, ["notify_venting_stale", "announce_venting_stale"])
        self.assertTrue(evolved["ventingArmed"])
        self.assertTrue(evolved["ventingStaleAlertSent"])
        self.assertNotIn("venting_on", actions)

        _, repeated_actions = washer_notifier.evolve_state(
            evolved,
            {"fresh": True, "inUse": True, "cycleActive": False, "doorOpen": False},
            now + timedelta(minutes=5),
            {**config(), "finish_signal": "cycleActive"},
        )
        self.assertNotIn("notify_venting_stale", repeated_actions)

    def test_stale_venting_alert_does_not_block_later_real_completion(self) -> None:
        now = datetime(2026, 7, 16, 18, 0, tzinfo=TZ)
        state = {
            "lastCycleActive": False,
            "lastInUse": True,
            "ventingArmed": True,
            "ventingStartedAt": (now - timedelta(hours=9)).isoformat(),
            "ventingStaleAlertSent": True,
        }
        evolved, actions = washer_notifier.evolve_state(
            state,
            {"fresh": True, "inUse": False, "cycleActive": False, "doorOpen": False},
            now,
            {**config(), "finish_signal": "cycleActive"},
        )
        self.assertEqual(actions, ["venting_on", "notify_venting", "announce_venting"])
        self.assertFalse(evolved["ventingArmed"])

    def test_stale_venting_action_uses_check_message(self) -> None:
        notifications: list[tuple[str, str]] = []

        def notification(message: str, title: str) -> dict:
            notifications.append((message, title))
            return {"ok": True}

        with mock.patch.object(washer_notifier, "mac_notification", side_effect=notification):
            results = washer_notifier.execute_actions(
                ["notify_venting_stale"],
                {
                    "accessory": "Washer",
                    "finish_sensor_id": "washer-finished",
                    "reminder_sensor_id": "washer-unload",
                    "maximum_venting_hours": 8,
                    "venting_stale_title": "Check Washer Venting",
                    "venting_stale_message": "Washer still reports venting after 8 hours. Check the washer and turn off the laundry-room fan if appropriate.",
                },
                dry_run=False,
            )
        self.assertTrue(results[0]["ok"])
        self.assertEqual(
            notifications,
            [
                (
                    "Washer still reports venting after 8 hours. Check the washer and turn off the laundry-room fan if appropriate.",
                    "Check Washer Venting",
                )
            ],
        )

    def test_reads_dryer_characteristics_from_snapshot(self) -> None:
        now = datetime(2026, 7, 15, 12, 0, tzinfo=TZ)
        latest = {
            "captured_at": (now - timedelta(minutes=1)).isoformat(),
            "homeEvents": {
                "currentCharacteristics": {
                    "in-use": {"accessory": "Dryer", "service": "Dryer", "characteristic": "InUse", "value": 0},
                    "door": {"accessory": "Dryer", "service": "Dryer Door", "characteristic": "ContactSensorState", "value": 1},
                }
            },
        }
        current = washer_notifier.current_appliance_state(latest, {**config(), "accessory": "Dryer"}, now)
        self.assertTrue(current["fresh"])
        self.assertFalse(current["inUse"])
        self.assertTrue(current["doorOpen"])

    def test_dryer_actions_use_dryer_messages_and_sensors(self) -> None:
        calls: list[tuple[str, str]] = []

        def notification(message: str, title: str) -> dict:
            calls.append((message, title))
            return {"ok": True}

        with (
            mock.patch.object(washer_notifier, "mac_notification", side_effect=notification),
            mock.patch.object(washer_notifier, "webhook_set", return_value={"ok": True}),
        ):
            results = washer_notifier.execute_actions(
                ["finish_on", "notify_finish", "notify_reminder"],
                {
                    "accessory": "Dryer",
                    "display_name": "Dryer",
                    "finish_sensor_id": "dryer-finished",
                    "reminder_sensor_id": "dryer-unload",
                    "reminder_minutes": 20,
                },
                False,
            )
        self.assertTrue(all(item["ok"] for item in results))
        self.assertEqual(calls[0], ("The dryer has finished.", "Dryer Finished"))
        self.assertEqual(calls[1], ("The dryer finished 20 minutes ago and the door is still closed.", "Unload Dryer"))

    def test_washer_venting_action_uses_fan_message_and_sensor(self) -> None:
        notifications: list[tuple[str, str]] = []
        webhook_calls: list[tuple[str, bool]] = []

        def notification(message: str, title: str) -> dict:
            notifications.append((message, title))
            return {"ok": True}

        def webhook(_url: str, sensor_id: str, active: bool) -> dict:
            webhook_calls.append((sensor_id, active))
            return {"ok": True}

        with (
            mock.patch.object(washer_notifier, "mac_notification", side_effect=notification),
            mock.patch.object(washer_notifier, "webhook_set", side_effect=webhook),
        ):
            results = washer_notifier.execute_actions(
                ["venting_on", "notify_venting"],
                {
                    "accessory": "Washer",
                    "display_name": "Washer",
                    "finish_sensor_id": "wash-finished",
                    "reminder_sensor_id": "washer-unload",
                    "venting_sensor_id": "washer-venting",
                },
                False,
            )
        self.assertTrue(all(item["ok"] for item in results))
        self.assertEqual(webhook_calls, [("washer-venting", True)])
        self.assertEqual(
            notifications,
            [("Washer venting has finished. Turn off the laundry-room fan.", "Venting Finished")],
        )

    def test_homepod_announcement_uses_configured_target_and_restores_output(self) -> None:
        completed = SimpleNamespace(returncode=0, stderr="", stdout="")
        with mock.patch.object(washer_notifier.subprocess, "run", side_effect=[completed, completed]) as run:
            result = washer_notifier.homepod_announcement(
                "The washer has finished.",
                {"homepod_targets": ["Primary HomePod"], "homepod_volume": 45, "homepod_clip_seconds": 5},
            )
        self.assertTrue(result["ok"])
        self.assertEqual(run.call_args_list[0].args[0][0], "say")
        apple_script = run.call_args_list[1].args[0][2]
        self.assertIn('set targetNames to {"Primary HomePod"}', apple_script)
        self.assertIn("set sound volume of deviceItem to 45", apple_script)
        self.assertIn("set current AirPlay devices to originalDevices", apple_script)


if __name__ == "__main__":
    unittest.main()
