import sys
import unittest
import json
import io
import tempfile
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timezone
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import calendar_announcements as cal


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.now = cal.timestamp("2026-09-10T00:00:00Z")
        self.event = dict(id="event", calendar="Maxim", start="2026-09-10T00:30:00Z", latitude=34, longitude=-119, location="Field", allDay=False, status=1)

    def test_mapped_timed_event(self):
        self.assertTrue(cal.eligible(self.event, self.now))

    def test_exclusions(self):
        for changes in ({"allDay": True}, {"status": 3}, {"calendar": "Family"}, {"location": "https://meeting"}, {"latitude": None}, {"latitude": float("nan")}):
            self.assertFalse(cal.eligible(dict(self.event, **changes), self.now))

    def test_jeanne_calendar_eligible_but_unmapped_presence_is_not_home(self):
        self.assertTrue(cal.eligible(dict(self.event, calendar="Jeanne"), self.now))
        self.assertFalse({"Arkadiy": True, "Maxim": True}.get("Jeanne", False))

    def test_due_window(self):
        self.assertTrue(cal.due(self.event, 900, self.now, 15))
        self.assertFalse(cal.due(self.event, 900, self.now - 1, 15))
        self.assertFalse(cal.due(self.event, 900, self.now + 121, 15))
        self.assertFalse(cal.due(self.event, float("nan"), self.now, 15))

    def test_video_detection(self):
        self.assertTrue(cal.video_event(dict(self.event, url="https://meet.google.com/abc-defg-hij"), self.now))
        self.assertTrue(cal.video_event(dict(self.event, location="Video appointment"), self.now))
        self.assertFalse(cal.video_event(dict(self.event, url="https://venue.example"), self.now))
        self.assertFalse(cal.video_event(dict(self.event, location="Video call", allDay=True), self.now))

    def test_presence_fails_closed(self):
        self.assertFalse(cal.is_home([], "phone", self.now))
        self.assertFalse(cal.is_home([dict(mac="tablet", last_seen=self.now)], "phone", self.now))
        for age in (91, -1):
            self.assertFalse(cal.is_home([dict(mac="phone", last_seen=self.now-age)], "phone", self.now))
        self.assertTrue(cal.is_home([dict(mac="phone", last_seen=self.now)], "phone", self.now))

    def test_recurring_occurrences_have_distinct_keys(self):
        self.assertNotEqual(cal.occurrence(self.event), cal.occurrence(dict(self.event, start="2026-09-17T00:30:00Z")))


class CalendarDeliveryTests(unittest.TestCase):
    """Exercise the scheduler without real calendars, network, email, or audio."""

    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.now = datetime(2026, 9, 15, 19, tzinfo=timezone.utc)
        self.state = root / "state.json"
        config = root / "config.json"
        config.write_text(json.dumps(dict(enabled=True, phones={"Jeanne": "phone"},
                                          homeAddress="Synthetic origin", arrivalBufferMinutes=15)))
        self.event = dict(id="synthetic", calendar="Jeanne", title="Synthetic appointment",
                          start="2026-09-15T19:30:00Z", latitude=34, longitude=-119,
                          location="Synthetic venue", allDay=False, status=1)
        self.snapshot = dict(generatedAt=self.now.isoformat(), events=[self.event])
        self.eta = dict(generatedAt=self.now.isoformat(), seconds=900)
        clock = stack.enter_context(patch.object(cal, "datetime", wraps=datetime))
        clock.now.side_effect = lambda tz=None: self.now.astimezone(tz)
        for name, value in (("CONFIG", config), ("STATE", self.state),
                            ("RUNTIME", Path(cal.__file__).resolve().parents[1])):
            stack.enter_context(patch.object(cal, name, value))
        self.reader = stack.enter_context(patch.object(cal, "invoke", side_effect=lambda *args: self.eta if args else self.snapshot))
        self.clients = stack.enter_context(patch.object(cal, "active_clients", return_value=[dict(mac="phone", last_seen=self.now.timestamp())]))
        self.relay = stack.enter_context(patch.object(cal, "run_indoor_homepod_announcement", return_value={"status": "accepted"}))

    def run_scheduler(self, deliver=True):
        with patch.object(sys, "argv", ["calendar"] + (["--deliver"] if deliver else [])), redirect_stdout(io.StringIO()) as output:
            cal.main()
        return json.loads(output.getvalue())

    def test_home_due_delivers_once_and_persists_before_relay(self):
        def relay(message, identifier):
            self.assertIn(cal.occurrence(self.event), json.loads(self.state.read_text()))
            self.assertIn("Jeanne, it is time to leave", message)
            return {"status": "accepted"}
        self.relay.side_effect = relay
        self.assertEqual(self.run_scheduler()["due"], 1)
        self.assertEqual(self.run_scheduler()["due"], 0)
        self.relay.assert_called_once()
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)

    def test_away_or_tablet_only_is_silent(self):
        self.clients.return_value = [dict(mac="tablet", last_seen=self.now.timestamp())]
        self.assertEqual(self.run_scheduler()["due"], 0)
        self.relay.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_departure_during_eta_check_is_silent(self):
        self.clients.side_effect = [self.clients.return_value, []]
        self.assertEqual(self.run_scheduler()["attempts"], [])
        self.relay.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_dry_run_does_not_deliver_or_consume_occurrence(self):
        self.assertEqual(self.run_scheduler(deliver=False)["due"], 1)
        self.relay.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_stale_snapshot_fails_closed(self):
        self.snapshot["generatedAt"] = "2026-09-15T18:00:00Z"
        with self.assertRaisesRegex(RuntimeError, "Stale calendar"):
            self.run_scheduler()
        self.relay.assert_not_called()

    def test_stale_eta_is_silent(self):
        self.eta["generatedAt"] = "2026-09-15T18:00:00Z"
        self.assertEqual(self.run_scheduler()["due"], 0)
        self.relay.assert_not_called()

    def test_uncertain_relay_is_not_replayed(self):
        self.relay.side_effect = RuntimeError("Synthetic uncertain delivery")
        with self.assertRaises(RuntimeError):
            self.run_scheduler()
        self.assertEqual(self.run_scheduler()["due"], 0)
        self.relay.assert_called_once()
