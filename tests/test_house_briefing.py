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
    def test_washer_request_is_one_shot_and_expires(self):
        import tempfile
        import washer_free_reminder as reminder
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(reminder, "PATH", Path(directory) / "request.json"):
            self.assertFalse(reminder.update(now=1000))
            reminder.update(arm=True, now=1000)
            self.assertTrue(reminder.update(now=1100))
            self.assertFalse(reminder.update(now=1101))
            reminder.update(arm=True, now=1000)
            self.assertFalse(reminder.update(now=44201))

    def test_resume_clears_only_pause_state(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(pause, "PATH", Path(directory) / "pause.json"):
            pause.hold()
            self.assertTrue(pause.active("washer"))
            pause.resume()
            self.assertFalse(pause.active("washer"))

    def test_washer_request_requires_fresh_running_cycle(self):
        import washer_free_reminder as reminder
        with mock.patch.object(house.alerts, "load_json_file", return_value={}), mock.patch.object(reminder, "update") as arm:
            self.assertIn("no reminder set", house.build("washerfree"))
            arm.assert_not_called()

    def test_morning_pause_is_bounded_and_safety_exempt(self):
        from datetime import datetime
        start = datetime.fromisoformat("2026-09-14T22:00:00-07:00").timestamp()
        end = datetime.fromisoformat("2026-09-15T08:00:00-07:00").timestamp()
        self.assertEqual(pause.morning_end(start), end)
        state = {"startedAt": start, "until": end, "mode": "morning"}
        self.assertTrue(pause.paused("washer", state, start + 7200))
        self.assertFalse(pause.paused("washer", state, end))
        for identifier in ("smoke", "co", "help_request", "dr_house_morning"):
            self.assertFalse(pause.paused(identifier, state, start + 10))
        self.assertFalse(pause.paused("washer", {**state, "until": end + 3600}, start + 10))

    def test_new_modes_are_whitelisted(self):
        for mode in ("leave", "unusual", "departure", "quiet", "morning"):
            self.assertIn(mode, house.MODES)
            self.assertIn(mode, dr_house_ssh.MODES)

    def test_unusual_ignores_normal_temperature(self):
        data = self.context()
        data["liveLoadKw"] = 1
        with mock.patch.object(house.alerts, "load_json_file", return_value=data), mock.patch.object(house, "fresh", return_value=True), mock.patch.object(house.alerts, "load_alarm_com", return_value={}), mock.patch.object(house.alerts, "household_observations", return_value={"temperature:hot:1": {"state": "closed"}}):
            self.assertIn("Nothing unusual", house.build("unusual"))

    def test_quiet_build_does_not_mute(self):
        with mock.patch.object(pause, "quiet_until_morning") as mute:
            self.assertIn("8 AM", house.build("quiet"))
            mute.assert_not_called()

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
