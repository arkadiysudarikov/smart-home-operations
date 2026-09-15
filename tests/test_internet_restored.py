import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from internet_restored import evaluate

class InternetTests(unittest.TestCase):
    def test_startup_never_announces(self):
        state, said = evaluate({}, True, 0)
        self.assertFalse(said)
        self.assertFalse(evaluate(state, True, 120)[1])

    def test_confirmed_outage_and_recovery_only_once(self):
        state, _ = evaluate({}, True, 0)
        for now in (120, 240, 360):
            state, said = evaluate(state, False, now)
            self.assertFalse(said)
        state, said = evaluate(state, True, 480)
        self.assertFalse(said)
        state, said = evaluate(state, True, 600)
        self.assertTrue(said)
        self.assertFalse(evaluate(state, True, 720)[1])

    def test_short_failure_and_stale_gap_do_not_announce(self):
        state, _ = evaluate({}, True, 0)
        state, _ = evaluate(state, False, 120)
        self.assertFalse(evaluate(state, True, 240)[1])
        self.assertFalse(evaluate(dict(state, outage=True), True, 1000)[1])
