import contextlib
import io
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import additional_announcements as a

NOW = datetime(2026, 9, 28, 19, tzinfo=timezone.utc).timestamp()


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


class AdditionalTests(unittest.TestCase):
    def health(self, t, bad=False):
        return {"generatedAt": iso(t), "smartHQPlugin": {"ok": not bad},
                "laundrySources": {k: {"active": False, "status": "fresh"} for k in ("washer", "dryer", "combo")}}

    def run_health(self, state, t, bad):
        return a.evaluate({"enabled": True, "laundryHealth": True}, state, self.health(t, bad), [], {}, t)

    def test_sustained_failure_once_and_recovery_once(self):
        state, notices = self.run_health({}, NOW, True)
        self.assertEqual(notices, [])
        state, notices = self.run_health(state, NOW+300, True)
        self.assertEqual(notices, [])
        state, notices = self.run_health(state, NOW+600, True)
        self.assertEqual(len(notices), 1)
        state, notices = self.run_health(state, NOW+660, True)
        self.assertEqual(notices, [])
        state, notices = self.run_health(state, NOW+720, False)
        self.assertIn("healthy again", notices[0][1])
        self.assertEqual(self.run_health(state, NOW+780, False)[1], [])

    def test_stale_health_never_implies_recovery(self):
        state = {"laundry": {"at": NOW, "valid": True, "bad": True, "warned": True, "notified": True}}
        state, notices = a.evaluate({"enabled": True, "laundryHealth": True}, state, self.health(NOW-1000), [], {}, NOW+60)
        self.assertEqual(notices, [])
        self.assertEqual(self.run_health(state, NOW+120, False)[1], [])

    def test_gap_resets_timer(self):
        state, _ = self.run_health({}, NOW, True)
        self.assertEqual(self.run_health(state, NOW+601, True)[1], [])

    def test_quiet_failure_no_recovery_chatter(self):
        night = NOW+12*3600
        state, _ = self.run_health({}, night, True)
        state, notices = self.run_health(state, night+600, True)
        self.assertEqual(notices, [])
        self.assertFalse(state["laundry"]["notified"])
        self.assertEqual(self.run_health(state, night+660, False)[1], [])

    def test_doorbell_requires_verified_source_and_not_motion(self):
        config = {"enabled": True, "doorbell": {"enabled": True, "sourceVerified": True, "cameraId": "123", "name": "Doorbell", "component": "Alarm.com"}}
        event = {"eventKey": "abc", "capturedAt": iso(NOW), "component": "Alarm.com", "message": "Camera ring detected for Doorbell (123)"}
        self.assertEqual(a.evaluate(config, {}, {}, [event], {}, NOW)[1], [])
        state = {"doorbell": {"seen": {}, "lastAttempt": 0}}
        new, notices = a.evaluate(config, state, {}, [event], {}, NOW)
        self.assertEqual(len(notices), 1)
        self.assertEqual(a.evaluate(config, new, {}, [event], {}, NOW+1)[1], [])
        for field, value in (("message", "Motion detected for camera Doorbell (123)"), ("component", "Unknown"), ("capturedAt", iso(NOW-91))):
            e = dict(event, **{field: value})
            self.assertEqual(a.evaluate(config, state, {}, [e], {}, NOW)[1], [])
        config["doorbell"]["sourceVerified"] = False
        self.assertEqual(a.evaluate(config, state, {}, [event], {}, NOW)[1], [])

    def test_freezer_verified_persistent_hysteresis(self):
        config = {"enabled": True, "freezer": {"enabled": True, "sourceVerified": True, "sensorId": "freezer-1"}}
        state = {}
        for offset in range(0, 1801, 60):
            reading = {"sensorId": "freezer-1", "fahrenheit": 20, "ok": True, "observedAt": iso(NOW+offset)}
            state, notices = a.evaluate(config, state, {}, [], reading, NOW+offset)
            self.assertEqual(len(notices), int(offset == 1800))
        reading.update(fahrenheit=12, observedAt=iso(NOW+1860))
        state, notices = a.evaluate(config, state, {}, [], reading, NOW+1860)
        self.assertEqual(notices, [])
        reading.update(fahrenheit=9, observedAt=iso(NOW+1920))
        self.assertEqual(len(a.evaluate(config, state, {}, [], reading, NOW+1920)[1]), 1)

    def test_freezer_bad_samples_fail_closed(self):
        config = {"enabled": True, "freezer": {"enabled": True, "sourceVerified": True, "sensorId": "f"}}
        for value in (None, True, float("nan"), float("inf"), "20"):
            reading = {"sensorId": "f", "fahrenheit": value, "ok": True, "observedAt": iso(NOW)}
            self.assertEqual(a.evaluate(config, {}, {}, [], reading, NOW)[1], [])


class CalendarTests(unittest.TestCase):
    def event(self):
        return {"id": "e1", "calendar": "Maxim", "title": "Private", "start": iso(NOW+3600), "status": 1, "recurring": False, "location": "Office"}

    def test_baseline_reschedule_cancellation_and_dedup(self):
        event = self.event()
        state, notices = a.calendar_changes([event], {}, {"Maxim": True}, NOW)
        self.assertEqual(notices, [])
        event["start"] = iso(NOW+7200)
        state, notices = a.calendar_changes([event], state, {"Maxim": True}, NOW+60)
        self.assertIn("updated", notices[0][2])
        self.assertNotIn("Private", notices[0][2])
        self.assertEqual(a.calendar_changes([event], state, {"Maxim": True}, NOW+120)[1], [])
        # Maintain fresh baseline while waiting out the per-event repeat limit.
        for t in (360, 660, 960):
            state, _ = a.calendar_changes([event], state, {"Maxim": True}, NOW+t)
        event["status"] = 3
        self.assertIn("cancelled", a.calendar_changes([event], state, {"Maxim": True}, NOW+1020)[1][0][2])

    def test_disappearance_is_not_cancellation(self):
        state, _ = a.calendar_changes([self.event()], {}, {"Maxim": True}, NOW)
        self.assertEqual(a.calendar_changes([], state, {"Maxim": True}, NOW+60)[1], [])

    def test_away_change_never_replayed_on_arrival(self):
        event = self.event()
        state, _ = a.calendar_changes([event], {}, {"Maxim": True}, NOW)
        event["status"] = 3
        state, notices = a.calendar_changes([event], state, {"Maxim": False}, NOW+60)
        self.assertEqual(notices, [])
        self.assertEqual(a.calendar_changes([event], state, {"Maxim": True}, NOW+120)[1], [])

    def test_ambiguous_old_reader_recurring_and_stale_baselines_skipped(self):
        event = self.event()
        state, _ = a.calendar_changes([event], {}, {"Maxim": True}, NOW)
        event["status"] = 3
        for events in ([event, event], [dict(event, recurring=True)], [dict(event, recurring=None)]):
            self.assertEqual(a.calendar_changes(events, state, {"Maxim": True}, NOW+60)[1], [])
        self.assertEqual(a.calendar_changes([event], state, {"Maxim": True}, NOW+601)[1], [])


class RunnerTests(unittest.TestCase):
    def test_persist_before_relay_and_dry_run_never_sends(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "data").mkdir()
            path = root / "data/additional_announcements_state.json"
            proposed = {"laundry": {"bad": True, "warned": True, "notified": True}}
            def relay(*args):
                self.assertTrue(json.loads(path.read_text())["laundry"]["warned"])
                self.assertFalse(json.loads(path.read_text())["laundry"]["notified"])
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                return {"status": "accepted"}
            with patch.object(a, "ROOT", root), patch.object(a, "RUNTIME", root), patch.object(a, "evaluate", return_value=(proposed, [("laundry_health", "Synthetic")])), patch("generate_alerts.run_indoor_homepod_announcement", side_effect=relay) as send, contextlib.redirect_stdout(io.StringIO()):
                with patch.object(sys, "argv", ["test"]):
                    a.main()
                    send.assert_not_called()
                    self.assertFalse(path.exists())
                with patch.object(sys, "argv", ["test", "--deliver"]):
                    a.main()
                send.assert_called_once()
                self.assertTrue(json.loads(path.read_text())["laundry"]["notified"])

    def test_failed_or_paused_warning_does_not_enable_recovery(self):
        for status in ("failed", "skipped"):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                (root / "data").mkdir()
                state = {"laundry": {"bad": True, "warned": True, "notified": True}}
                with patch.object(a, "ROOT", root), patch.object(a, "RUNTIME", root), patch.object(a, "evaluate", return_value=(state, [("laundry_health", "Synthetic")])), patch("generate_alerts.run_indoor_homepod_announcement", return_value={"status": status}), patch.object(sys, "argv", ["test", "--deliver"]), contextlib.redirect_stdout(io.StringIO()):
                    a.main()
                self.assertFalse(json.loads((root / "data/additional_announcements_state.json").read_text())["laundry"]["notified"])


if __name__ == "__main__":
    unittest.main()
