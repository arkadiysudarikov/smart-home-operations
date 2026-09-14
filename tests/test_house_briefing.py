import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import house_briefing as house
from unittest import mock
import announcement_pause as pause
import dr_house_ssh

NOW = "2026-09-11T16:00:00-07:00"


class BriefingTests(unittest.TestCase):
    def test_ssh_dispatcher_rejects_arbitrary_commands(self):
        with mock.patch.object(dr_house_ssh.urllib.request, "urlopen") as send:
            for command in ("", "status; whoami", "status\n", "cat /etc/passwd", "sftp", "../status"):
                with self.assertRaises(ValueError):
                    dr_house_ssh.dispatch(command)
            send.assert_not_called()

    def test_pause_expires_and_does_not_mute_help_or_unknown_safety_alert(self):
        state = {"startedAt": 1000, "until": 4600}
        for identifier in ("washer", "energy_high_on", "calendar-example", "household_reminder"):
            self.assertTrue(pause.paused(identifier, state, 1001))
            self.assertFalse(pause.paused(identifier, state, 4600))
        for identifier in ("help_request", "smoke", "co", "dr_house_status", "unknown"):
            self.assertFalse(pause.paused(identifier, state, 1001))
        self.assertFalse(pause.paused("washer", {"startedAt": 1000, "until": 999999}, 1001))

    def test_changes_does_not_treat_missing_as_closed(self):
        previous = {"at": NOW, "observations": {"door": {"name": "Door", "state": "open"}}}
        text = house.changes_message(previous, {}, NOW)
        self.assertIn("unavailable", text)
        self.assertNotIn("closed", text)
        text = house.changes_message(previous, {"door": {"name": "Door", "state": "closed"}}, NOW)
        self.assertIn("Door is now closed", text)

    def test_complications_silent_when_known_readings_normal(self):
        values = {"sensors:door": {"name": "Door", "state": "closed"}, "energy": {"name": "Energy High", "state": "clear"}}
        with mock.patch.object(house, "observations", return_value=values):
            self.assertEqual(house.build("complications"), "")
        with mock.patch.object(house, "observations", return_value={}):
            self.assertIn("unavailable", house.build("complications"))

    def context(self):
        return {"generatedAt": NOW, "sampleAt": NOW, "liveLoadKw": 5, "thresholdKw": 3.5,
                "candidates": [{"source": "Sense", "capturedAt": NOW, "devices": [{"name": "Dryer", "watts": 3000}, {"name": "Solar", "watts": 9000}]}]}

    def test_high_is_evidence_backed(self):
        text = house.energy_message(self.context(), NOW, True)
        self.assertEqual("Energy is high; Dryer is using about 3.0 kilowatts.", text)
        self.assertNotIn("Solar", text)
        self.assertNotIn("Dr. House", text)

    def test_stale_source_never_names_cause(self):
        data = self.context()
        data["sampleAt"] = "2026-09-10T16:00:00-07:00"
        self.assertIn("unavailable", house.energy_message(data, NOW, True))
        data = self.context()
        data["candidates"][0]["capturedAt"] = data["sampleAt"] = NOW
        data["candidates"][0]["capturedAt"] = "2026-09-10T16:00:00-07:00"
        self.assertNotIn("Dryer", house.energy_message(data, NOW, True))

    def test_normal_does_not_explain_nonexistent_alert(self):
        data = self.context()
        data["liveLoadKw"] = 1
        text = house.energy_message(data, NOW, True)
        self.assertEqual("Energy use is normal.", text)
        self.assertNotIn("Dryer", text)

    def test_laundry_requires_fresh_heartbeat_and_never_infers_finished(self):
        laundry = {"ok": True, "capturedAt": NOW, "devices": {"washer": {"cycleActive": True, "remainingSeconds": 2400, "apiLastSuccessAt": NOW}}}
        self.assertIn("40 minutes", house.status_message({}, laundry, self.context(), NOW))
        laundry["devices"]["washer"]["apiLastSuccessAt"] = "2026-09-10T16:00:00-07:00"
        text = house.status_message({}, laundry, {}, NOW)
        self.assertNotIn("40 minutes", text)
        self.assertNotIn("finished", text)
