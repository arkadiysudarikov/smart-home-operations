import sys
import unittest
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
