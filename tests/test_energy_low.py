import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import energy_low as low


class EnergyLowTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 21, 19, tzinfo=timezone.utc)
        self.sample = dict(at=self.now.isoformat(), load_kw=0.4, verified=True,
                           source="envoy_site_load", home=True, hvac=False, ev=False)
        self.history = [dict(self.sample, at=(self.now-timedelta(days=i)).isoformat(), load_kw=1.2)
                        for i in range(1, 15)]

    def run_case(self, minute=0, state=None, **updates):
        now = self.now + timedelta(minutes=minute)
        sample = dict(self.sample, at=now.isoformat(), **updates)
        return low.evaluate(sample, self.history, state or {}, now.isoformat(), enabled=True)

    def sustained(self):
        state = {}
        for minute in range(16):
            state, result = self.run_case(minute, state)
        return state, result

    def test_requires_continuity(self):
        state, result = self.run_case()
        self.assertEqual(result["status"], "waiting_for_sustained_low")
        self.assertEqual(self.run_case(15, state)[1]["status"], "waiting_for_sustained_low")
        self.assertEqual(self.sustained()[1]["status"], "needs_jev")

    def test_disabled(self):
        self.assertEqual(low.evaluate({}, [], {}, self.now.isoformat())[1]["status"], "disabled")

    def test_bad_data_never_requests_model(self):
        for updates in ({"load_kw": 0}, {"load_kw": float("nan")}, {"load_kw": True},
                        {"verified": False}, {"source": "grid_net"}, {"home": None}):
            with self.subTest(updates=updates):
                self.assertEqual(self.run_case(**updates)[1]["status"], "invalid_or_stale_data")

    def test_history_requires_seven_distinct_days_and_matching_context(self):
        self.history = [self.history[0]] * 100
        self.assertEqual(self.run_case()[1]["status"], "insufficient_history")
        self.setUp()
        self.assertEqual(self.run_case(ev=True)[1]["status"], "insufficient_history")

    def test_normal_does_not_request_model(self):
        self.assertEqual(self.run_case(load_kw=1.0)[1]["status"], "not_low")

    def test_stale_and_future_samples(self):
        for delta in (-600, 10):
            sample = dict(self.sample, at=(self.now+timedelta(seconds=delta)).isoformat())
            self.assertEqual(low.evaluate(sample, self.history, {}, self.now.isoformat(), enabled=True)[1]["status"], "invalid_or_stale_data")

    def test_response_validation(self):
        self.assertFalse(low.response_allows(None))
        self.assertFalse(low.response_allows({"answers": {}}))
        self.assertFalse(low.response_allows(self.response(0.5)))
        self.assertTrue(low.response_allows(self.response()))

    def response(self, confidence=0.98):
        return {"model": "jev-1.13.0", "answers": {"low_energy": {
            "type": "choice", "choice": "unusual_low", "confidence": confidence,
            "probabilities": {"unusual_low": .98, "review": .02, "expected_low": 0}}}}

    def test_bound_reply_and_one_attempt(self):
        state, result = self.sustained()
        now = self.now+timedelta(minutes=15)
        sample = dict(self.sample, at=now.isoformat())
        reply = {"request_hash": result["request_hash"], "response": self.response()}
        rejected = low.evaluate(sample, self.history, state, now.isoformat(), enabled=True,
                                reply=dict(reply, request_hash="wrong"))
        self.assertEqual(rejected[1]["status"], "jev_did_not_approve")
        state, result = low.evaluate(sample, self.history, state, now.isoformat(), enabled=True, reply=reply)
        self.assertEqual(result["status"], "eligible")
        self.assertEqual(self.run_case(16, state)[1]["status"], "already_attempted")

    def test_request_contains_no_raw_labels(self):
        self.sample["private_email"] = "PRIVATE"
        _, result = self.sustained()
        self.assertNotIn("PRIVATE", str(result))

    def test_quiet_hours_suppress(self):
        self.now = self.now.replace(hour=5)  # 22:00 Pacific previous day
        self.sample["at"] = self.now.isoformat()
        self.history = [dict(self.sample, at=(self.now-timedelta(days=i)).isoformat(), load_kw=1.2)
                        for i in range(1, 15)]
        self.assertEqual(self.sustained()[1]["status"], "quiet_or_away")

    def test_away_suppresses(self):
        self.sample["home"] = False
        for row in self.history:
            row["home"] = False
        self.assertEqual(self.sustained()[1]["status"], "quiet_or_away")

    def test_cooldown_and_input_state_unchanged(self):
        state, _ = self.sustained()
        import copy
        original = copy.deepcopy(state)
        self.run_case(16, state)
        self.assertEqual(state, original)
        state["last_attempt"] = self.now.isoformat()
        self.assertEqual(self.run_case(16, state)[1]["status"], "cooldown")


if __name__ == "__main__":
    unittest.main()
