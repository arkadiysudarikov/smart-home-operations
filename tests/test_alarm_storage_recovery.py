import json
import os
import signal
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import action_server
import guard_unifi_storage as guard
import maintain_storage


class AlarmStorageRecovery(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.attempt = self.root / "attempt.json"
        self.log = self.root / "homebridge.log"
        (self.root / "config.json").write_text(json.dumps({"platforms": [{
            "platform": "Alarmdotcom",
            "_bridge": {"username": "AB:CD:EF:AB:CD:EF", "port": 52230},
        }]}))
        self.log.write_text(
            "[11/11/2026, 4:29:36 PM] Failed to publish bridge accessory 'Alarm': "
            "ENOSPC: no space left on device, open '/bridge/persist/.AccessoryInfo.ABCDEFABCDEF.json.123.tmp'\n"
        )
        self.child = (os.getuid(), 100, datetime(2026, 11, 11, 16, 29, 34), guard.ALARM_PROCESS)
        self.parent = (os.getuid(), 1, datetime(2026, 11, 11, 8), "homebridge")

    def test_recovers_the_failed_child_once_when_headroom_returns(self):
        with (
            patch.object(guard, "listening", return_value=False),
            patch.object(guard, "alarm_restart_has_headroom", return_value=True),
            patch.object(guard, "process_record", side_effect={123: self.child, 100: self.parent}.get),
            patch.object(guard, "hap_responds", return_value=True),
            patch.object(guard.os, "kill") as kill,
        ):
            self.assertEqual(guard.recover_alarm_bridge(self.root, self.attempt), "recovered")
            self.assertEqual(guard.recover_alarm_bridge(self.root, self.attempt), "recovery_already_attempted")
            kill.assert_called_once_with(123, signal.SIGTERM)

    def test_low_disk_defers_automatic_and_manual_restarts(self):
        with (
            patch.object(guard, "listening", return_value=False),
            patch.object(guard, "alarm_restart_has_headroom", return_value=False),
            patch.object(guard.os, "kill") as kill,
        ):
            self.assertEqual(guard.recover_alarm_bridge(self.root, self.attempt), "waiting_for_disk_space")
            self.assertFalse(action_server.restart_alarm_child(123)["ok"])
            kill.assert_not_called()
            self.assertFalse(self.attempt.exists())

    def test_old_failure_cannot_restart_a_new_process_with_same_pid(self):
        reused_child = (*self.child[:2], datetime(2026, 11, 11, 17), self.child[3])
        with (
            patch.object(guard, "listening", return_value=False),
            patch.object(guard, "alarm_restart_has_headroom", return_value=True),
            patch.object(guard, "process_record", side_effect={123: reused_child, 100: self.parent}.get),
            patch.object(guard.os, "kill") as kill,
        ):
            self.assertEqual(guard.recover_alarm_bridge(self.root, self.attempt), "publication_error_from_different_process")
            kill.assert_not_called()

    def test_database_maintenance_defers_before_opening_database_under_pressure(self):
        db = self.root / "monitor.sqlite"
        db.write_bytes(b"fixture")
        with (
            patch.object(maintain_storage, "DB_PATH", db),
            patch.object(maintain_storage.shutil, "disk_usage") as usage,
            patch.object(maintain_storage.sqlite3, "connect") as connect,
        ):
            usage.return_value.free = guard.MIN_FREE_BYTES
            result = maintain_storage.compact_db({"retention": {}})
            self.assertEqual(result["databaseMaintenance"], "deferred for disk headroom")
            connect.assert_not_called()
