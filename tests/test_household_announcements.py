import importlib.util
import unittest
import tempfile
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location("alerts", Path(__file__).resolve().parents[1] / "scripts/generate_alerts.py")
alerts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(alerts)


class HouseholdTests(unittest.TestCase):
    def test_disabled_policy_does_not_probe_or_announce(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(alerts, "DATA_DIR", Path(tmp)), mock.patch.object(alerts, "HOUSEHOLD_ANNOUNCEMENT_PATH", Path(tmp) / "state.json"), mock.patch("internet_restored.reachable") as probe, mock.patch("household_weather.check") as weather, mock.patch.object(alerts, "run_indoor_homepod_announcement") as speak:
            alerts.deliver_household_reminders({"household_announcements": {"enabled": False, "rain_open_enabled": True, "internet_restored_enabled": True}}, {}, updated_at="2026-09-10T18:00:00Z")
            probe.assert_not_called()
            weather.assert_not_called()
            speak.assert_not_called()

    def test_help_failure_is_not_reported_as_success(self):
        with mock.patch.object(alerts.sys, "argv", ["generate_alerts.py", "--help-announcement", "--speak"]), mock.patch.object(alerts, "running_from_runtime_root", return_value=True), mock.patch.object(alerts, "load_config", return_value={}), mock.patch.object(alerts, "run_indoor_homepod_announcement", return_value={"status": "failed"}), mock.patch("builtins.print"):
            self.assertEqual(alerts.main(), 1)

    def test_temperature_requires_fresh_sustained_readings(self):
        config = {"household_announcements": {"enabled": True, "temperature_enabled": True}}
        state = {}
        for minute in (0, 15, 30):
            now = (datetime(2026, 9, 10, 18, tzinfo=timezone.utc) + timedelta(minutes=minute)).isoformat()
            alarm = {"generatedAt": now, "alarmState": {"ok": True, "systems": [{"components": {"thermostats": [{"id": "test", "ambientTemp": 86, "stateText": "Cooling"}]}}]}}
            state, pending = alerts.evaluate_household_reminders(config, alarm, state, now)
            self.assertEqual(bool(pending), minute == 30)
        alarm["alarmState"]["systems"][0]["components"]["thermostats"][0]["ambientTemp"] = 73
        self.assertEqual(alerts.evaluate_household_reminders(config, alarm, state, now)[1], [])

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
        state, pending = self.evaluate(26, state, stale=True)
        self.assertEqual(pending, [])
        state, pending = self.evaluate(28, state)
        self.assertEqual(pending, [])
        self.assertEqual(state["episodes"]["garages:1"]["since"], (self.start + timedelta(minutes=28)).isoformat())

    def test_duplicate_capture_does_not_advance_timer(self):
        state, _ = self.evaluate(0)
        state, pending = self.evaluate(15, state, stale=True)
        self.assertEqual(pending, [])

    def test_cooling_needs_actual_cooling_and_two_samples(self):
        self.config["household_announcements"]["cooling_open_enabled"] = True
        def capture(minute, mode):
            return {"generatedAt": (self.start + timedelta(minutes=minute)).isoformat(),
                    "alarmState": {"ok": True, "systems": [{"components": {
                        "thermostats": [{"stateText": mode, "desiredState": 3}],
                        "sensors": [{"id": "door", "description": "Entry Door", "stateText": "Open"}]
                    }}]}}
        stamp = self.start.isoformat()
        state, pending = alerts.evaluate_household_reminders(self.config, capture(0, "Idle"), {}, stamp)
        self.assertEqual(pending, [])
        state, _ = alerts.evaluate_household_reminders(self.config, capture(0, "Cooling"), state, stamp)
        state, pending = alerts.evaluate_household_reminders(self.config, capture(6, "Cooling"), state,
                                                           (self.start + timedelta(minutes=6)).isoformat())
        self.assertEqual(pending, ["cooling:sensors:door"])

    def test_solar_requires_fresh_meter_readings_and_ten_minutes(self):
        self.config["household_announcements"]["solar_surplus_enabled"] = True
        state = {}
        for minute in (0, 3, 6, 9, 12):
            now = self.start + timedelta(minutes=minute)
            def meter(kind, watts):
                return {"type": "eim", "activeCount": 1, "readingTime": now.timestamp(),
                        "measurementType": kind, "wNow": watts}
            envoy = {"ok": True, "probes": [{"productionStatus": 200, "production": {
                "production": [meter("production", 5000)],
                "consumption": [meter("total-consumption", 3000), meter("net-consumption", -2000)]}}]}
            state, messages = alerts.daily_household_messages(self.config, {}, envoy, state, now.isoformat())
            self.assertEqual("solar" in messages, minute == 12)
        state["dailyAttempts"] = {"solar": now.date().isoformat()}
        _, messages = alerts.daily_household_messages(self.config, {}, envoy, state, now.isoformat())
        self.assertEqual(messages, {})
        self.assertIsNone(alerts.solar_surplus_sample(envoy, (now + timedelta(minutes=6)).isoformat()))

    def test_security_digest_after_five_once_daily(self):
        self.config["household_announcements"]["security_digest_enabled"] = True
        now = "2026-09-09T17:00:00-07:00"
        alarm = {"troubleConditions": {"checkedAt": now, "ok": True,
                 "rows": [{"description": "Sideyard sensor low battery"}]}}
        state, messages = alerts.daily_household_messages(self.config, alarm, {}, {}, now)
        self.assertIn("security", messages)
        state["dailyAttempts"] = {"security": "2026-09-09"}
        _, messages = alerts.daily_household_messages(self.config, alarm, {}, state, now)
        self.assertEqual(messages, {})

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
