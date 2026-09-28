import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import announcement_health as health
import recover_smarthq_laundry as recovery


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.now = datetime(2026, 9, 28, 18, tzinfo=timezone.utc)

    def write(self, name, data):
        (self.data/name).write_text(json.dumps(data))

    def report(self):
        return health.build(self.data, {"ok": True}, self.now)

    def test_relay_acceptance_never_means_audible_delivery(self):
        self.write("homepod_announcement_events.jsonl", {"ok": True, "at": self.now.isoformat()})
        result = self.report()
        self.assertEqual(result["relay"]["status"], "accepted_not_delivered")
        self.assertEqual(result["audibleDelivery"]["status"], "unverified")

    def test_failed_and_skipped_are_separate(self):
        self.write("homepod_announcement_events.jsonl", {"ok": False})
        self.assertFalse(self.report()["ok"])
        self.write("homepod_announcement_events.jsonl", {"ok": True, "skipped": True})
        self.assertEqual(self.report()["relay"]["status"], "skipped")

    def test_malformed_latest_does_not_hide_behind_success(self):
        (self.data/"homepod_announcement_events.jsonl").write_text('{"ok":true}\n{broken\n')
        self.assertEqual(self.report()["relay"]["status"], "invalid_log")

    def test_active_stale_and_fresh_sources(self):
        payload = {"ok": True, "capturedAt": self.now.isoformat(), "devices": {"washer": {"inUse": True, "apiLastSuccessAt": (self.now-timedelta(days=1)).isoformat()}}}
        self.write("latest_smarthq_laundry_state.json", payload)
        self.assertIn("Active laundry source stale", self.report()["faults"])
        payload["devices"]["washer"]["apiLastSuccessAt"] = self.now.isoformat()
        self.write("latest_smarthq_laundry_state.json", payload)
        self.assertEqual(self.report()["laundrySources"]["washer"]["status"], "fresh")
        payload["capturedAt"] = (self.now-timedelta(hours=1)).isoformat()
        self.write("latest_smarthq_laundry_state.json", payload)
        self.assertFalse(self.report()["ok"])

    def test_unknown_version_or_code_is_not_trusted(self):
        self.write("package.json", {"version": "0.8.2"})
        (self.data/"dist/devices").mkdir(parents=True)
        (self.data/"dist/devices/device.js").write_text("function recordSmartHQHeartbeat() {}")
        self.assertEqual(health.inspect_plugin(self.data)["status"], "plugin_changed_review_required")

    def test_absent_plugin_is_not_healthy(self):
        self.assertFalse(health.inspect_plugin(self.data)["ok"])

    def test_changed_plugin_stops_recovery_restarts(self):
        with mock.patch.object(recovery, "ROOT", recovery.RUNTIME_ROOT), mock.patch.object(recovery, "load_json", return_value={"smarthq_laundry_recovery": {"enabled": True}}), mock.patch.object(health, "active_plugin", return_value=self.data), mock.patch.object(health, "inspect_plugin", return_value={"ok":False,"status":"heartbeat_patch_missing"}), mock.patch.object(recovery, "restart_smarthq_child") as restart, mock.patch.object(recovery, "write_status") as status, mock.patch.object(sys, "argv", ["recover_smarthq_laundry.py"]), mock.patch("builtins.print"):
            self.assertEqual(recovery.main(), 1)
            restart.assert_not_called()
            self.assertEqual(status.call_args.args[0]["classification"], "instrumentation_review_required")


if __name__ == "__main__":
    unittest.main()
