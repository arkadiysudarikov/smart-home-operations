import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import washer_notifier as w


class CountdownTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 28, 10, tzinfo=ZoneInfo('America/Los_Angeles'))
        self.cfg = {'finish_signal': 'countdown_confirmed', 'minimum_running_samples': 2}

    def step(self, state, seconds, minute, **extra):
        now = self.now+timedelta(minutes=minute)
        current = dict(fresh=True, source='homebridge-hap-live', capturedAt=now.isoformat(),
                       inUse=True, cycleActive=False, remainingSeconds=seconds, doorOpen=None)
        current.update(extra)
        return w.evolve_state(state, current, now, self.cfg)

    def test_actual_missed_cycle_trace_and_single_finish(self):
        state = {'lastCycleActive': False, 'primaryArmed': False}
        # Values taken from September 28 countdown logs; status tile was false.
        for minute, seconds in [(0,2002),(5,1822),(10,1400),(15,1103),(20,807),(25,667),(28,664),(30,484),(33,364),(35,184),(38,29),(40,0)]:
            state, actions = self.step(state, seconds, minute)
            self.assertNotIn('announce_finish', actions)
        state, actions = self.step(state, 0, 42)
        self.assertIn('announce_finish', actions)
        self.assertEqual(state['completedCycles'], 1)
        state, actions = self.step(state, 0, 44)
        self.assertNotIn('announce_finish', actions)

    def test_large_jump_to_zero_not_completion(self):
        state, _ = self.step({}, 2000, 0)
        state, _ = self.step(state, 1800, 2)
        for minute in (4,6,8):
            state, actions = self.step(state, 0, minute)
            self.assertNotIn('announce_finish', actions)

    def test_idle_startup_never_announces(self):
        state = {}
        for minute in (0,2,4):
            state, actions = self.step(state, 0, minute)
            self.assertNotIn('announce_finish', actions)

    def test_transient_zero_then_positive_resets_confirmation(self):
        state, _ = self.step({}, 120, 0)
        state, _ = self.step(state, 60, 2)
        state, _ = self.step(state, 0, 4)
        state, actions = self.step(state, 30, 5)
        self.assertNotIn('announce_finish', actions)
        state, actions = self.step(state, 0, 6)
        self.assertNotIn('announce_finish', actions)
        state, actions = self.step(state, 0, 8)
        self.assertIn('announce_finish', actions)

    def test_repeated_capture_and_stale_zero_cannot_complete(self):
        state, _ = self.step({}, 120, 0)
        state, _ = self.step(state, 60, 2)
        state, _ = self.step(state, 0, 4)
        state, actions = self.step(state, 0, 5, capturedAt=(self.now+timedelta(minutes=4)).isoformat())
        self.assertNotIn('announce_finish', actions)
        state, actions = self.step(state, 0, 6, fresh=False)
        self.assertNotIn('announce_finish', actions)
        state, actions = self.step(state, 0, 7)
        self.assertNotIn('announce_finish', actions)

    def test_gap_and_invalid_values_fail_closed(self):
        for value in [None, float('nan'), True, -1, '0']:
            state, _ = self.step({}, 120, 0)
            state, _ = self.step(state, 60, 2)
            self.assertNotIn('announce_finish', self.step(state, value, 4)[1])
        state, _ = self.step({}, 120, 0)
        state, _ = self.step(state, 60, 2)
        self.assertNotIn('announce_finish', self.step(state, 0, 10)[1])


if __name__ == '__main__': unittest.main()
