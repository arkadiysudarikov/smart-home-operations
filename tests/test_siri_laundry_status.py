import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from house_briefing import laundry_status_message

NOW = "2026-11-04T12:56:30-08:00"

class LaundryStatusTests(unittest.TestCase):
    def data(self, **washer):
        idle = {"apiLastSuccessAt": NOW, "cycleActive": False, "inUse": False}
        return {"ok": True, "capturedAt": NOW, "devices": {
            "washer": dict(idle, **washer), "dryer": dict(idle), "combo": dict(idle)}}

    def test_running_cycle_has_rounded_time_and_idle_is_not_completion(self):
        text = laundry_status_message(self.data(cycleActive=True, inUse=True, remainingSeconds=3018), NOW)
        self.assertIn("washer is running, with about 51 minutes remaining", text)
        self.assertIn("dryer is not running", text)
        self.assertNotIn("finished", text)

    def test_stale_capture_and_stale_heartbeat_do_not_claim_running(self):
        d = self.data(cycleActive=True, inUse=True)
        d["capturedAt"] = "2026-11-04T10:00:00-08:00"
        self.assertIn("unavailable or stale", laundry_status_message(d, NOW))
        d["capturedAt"] = NOW
        d["devices"]["washer"]["apiLastSuccessAt"] = "2026-11-04T10:00:00-08:00"
        self.assertIn("washer status is unavailable or stale", laundry_status_message(d, NOW))

    def test_in_use_without_active_cycle_does_not_claim_finished(self):
        self.assertIn("in use; no active cycle is confirmed", laundry_status_message(self.data(inUse=True), NOW))

    def test_invalid_remaining_time_is_not_spoken(self):
        for value in (True, float("nan"), -1):
            text = laundry_status_message(self.data(cycleActive=True, remainingSeconds=value), NOW)
            self.assertNotIn("minutes remaining", text)
