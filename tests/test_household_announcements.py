import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location("alerts", Path(__file__).resolve().parents[1] / "scripts/generate_alerts.py")
alerts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(alerts)


class HouseholdTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 9, 9, 20, tzinfo=timezone.utc)
        self.config = {"household_announcements": {"enabled": True, "rules": [
            {"key": "garages:1", "active_state": "open", "after_minutes": 15, "message": "Garage open."}
        ]}}

    def evaluate(self, minute, previous=None, value="Open", stale=False):
        now = (self.start + timedelta(minutes=minute)).isoformat()
        alarm = {"generatedAt": self.start.isoformat() if stale else now,
                 "alarmState": {"ok": True, "systems": [{"components": {"garages": [
                     {"id": "1", "description": "Garage door", "stateText": value}
                 ]}}]}}
        return alerts.evaluate_household_reminders(self.config, alarm, previous or {}, now)

    def test_continuous_open_requires_fifteen_minutes(self):
        state = {}
        for minute in range(0, 15, 3):
            state, pending = self.evaluate(minute, state)
            self.assertEqual(pending, [])
        state, pending = self.evaluate(15, state)
        self.assertEqual(pending, ["garages:1"])
        state["episodes"]["garages:1"]["attempted"] = True
        for minute in (18, 21, 24):
            state, pending = self.evaluate(minute, state)
            self.assertEqual(pending, [])

    def test_stale_gap_restarts_timer(self):
        state, _ = self.evaluate(0)
        state, _ = self.evaluate(3, state)
        state, pending = self.evaluate(16, state, stale=True)
        self.assertEqual(pending, [])
        state, pending = self.evaluate(18, state)
        self.assertEqual(pending, [])
        self.assertEqual(state["episodes"]["garages:1"]["since"], (self.start + timedelta(minutes=18)).isoformat())

    def test_unknown_does_not_rearm_delivered_episode(self):
        state, _ = self.evaluate(0)
        state["episodes"]["garages:1"]["attempted"] = True
        state, _ = self.evaluate(3, state, value="Unknown")
        state, _ = self.evaluate(6, state)
        self.assertTrue(state["episodes"]["garages:1"]["attempted"])
        state, _ = self.evaluate(9, state, value="Closed")
        state, _ = self.evaluate(12, state)
        self.assertFalse(state["episodes"]["garages:1"]["attempted"])

    def test_quiet_hours_and_stale_bedtime(self):
        self.start = datetime(2026, 9, 10, 5, tzinfo=timezone.utc)
        state = {}
        for minute in range(0, 31, 3):
            state, pending = self.evaluate(minute, state)
            self.assertEqual(pending, [])
        self.assertIn("unavailable", alerts.bedtime_message({}, self.start.isoformat()))

    def test_disabled_policy(self):
        self.config["household_announcements"]["enabled"] = False
        state = {}
        for minute in range(0, 31, 3):
            state, pending = self.evaluate(minute, state)
            self.assertEqual(pending, [])


if __name__ == "__main__":
    unittest.main()
