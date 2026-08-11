#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "recover_smarthq_laundry", ROOT / "scripts" / "recover_smarthq_laundry.py"
)
assert SPEC and SPEC.loader
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)


class RecoverSmartHQLaundryTests(unittest.TestCase):
    def test_assessment_only_monitors_active_or_armed_appliances(self) -> None:
        now = datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc).astimezone()
        latest = {
            "ok": True,
            "devices": {
                "washer": {
                    "inUse": True,
                    "cycleActive": True,
                    "apiLastSuccessAt": (now - timedelta(minutes=6)).isoformat(),
                },
                "dryer": {
                    "inUse": False,
                    "cycleActive": False,
                    "apiLastSuccessAt": (now - timedelta(hours=1)).isoformat(),
                },
                "combo": {
                    "inUse": False,
                    "cycleActive": False,
                    "apiLastSuccessAt": (now - timedelta(seconds=30)).isoformat(),
                },
            },
        }
        states = {"washer": {"armed": True}, "dryer": {}, "combo": {"armed": True}}

        assessed = recovery.assess_heartbeats(latest, states, now, 5)

        self.assertEqual(assessed["monitoredAppliances"], ["washer", "combo"])
        self.assertEqual(assessed["staleAppliances"], ["washer"])
        self.assertTrue(assessed["stale"])

    def test_two_close_stale_checks_reach_restart_threshold(self) -> None:
        now = datetime(2026, 7, 22, 14, 5, tzinfo=timezone.utc).astimezone()
        previous = {
            "stale": True,
            "checkedAt": (now - timedelta(minutes=5)).isoformat(),
            "consecutiveStaleChecks": 1,
        }

        self.assertEqual(recovery.consecutive_stale_checks(previous, True, now, 12), 2)
        self.assertEqual(recovery.consecutive_stale_checks(previous, False, now, 12), 0)

    def test_old_stale_check_does_not_count_as_consecutive(self) -> None:
        now = datetime(2026, 7, 22, 14, 30, tzinfo=timezone.utc).astimezone()
        previous = {
            "stale": True,
            "checkedAt": (now - timedelta(minutes=20)).isoformat(),
            "consecutiveStaleChecks": 8,
        }

        self.assertEqual(recovery.consecutive_stale_checks(previous, True, now, 12), 1)

    def test_restart_targets_only_validated_smarthq_listener(self) -> None:
        with (
            mock.patch.object(recovery, "listening_pid", return_value=1234),
            mock.patch.object(recovery, "process_command", return_value="homebridge: @homebridge-plugins/homebridge-smarthq"),
            mock.patch.object(recovery.os, "kill") as kill,
            mock.patch.object(recovery, "wait_for_new_smarthq_pid", return_value={"ok": True, "pid": 5678}),
        ):
            result = recovery.restart_smarthq_child(40893)

        self.assertTrue(result["ok"])
        self.assertEqual(result["previousPid"], 1234)
        self.assertEqual(result["currentPid"], 5678)
        kill.assert_called_once_with(1234, recovery.signal.SIGTERM)

    def test_restart_refuses_non_smarthq_listener(self) -> None:
        with (
            mock.patch.object(recovery, "listening_pid", return_value=1234),
            mock.patch.object(recovery, "process_command", return_value="some-other-service"),
            mock.patch.object(recovery.os, "kill") as kill,
        ):
            result = recovery.restart_smarthq_child(40893)

        self.assertFalse(result["ok"])
        self.assertIn("not the SmartHQ", result["error"])
        kill.assert_not_called()

    def test_cooldown_blocks_repeat_restart(self) -> None:
        now = datetime(2026, 7, 22, 14, 10, tzinfo=timezone.utc).astimezone()
        previous = {"lastRestartAt": (now - timedelta(minutes=10)).isoformat()}

        self.assertTrue(recovery.cooldown_active(previous, now, 20))
        self.assertFalse(recovery.cooldown_active(previous, now + timedelta(minutes=11), 20))


if __name__ == "__main__":
    unittest.main()
