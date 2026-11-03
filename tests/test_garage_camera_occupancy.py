import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import garage_camera_occupancy as camera


class CameraGuardTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 3, 9, tzinfo=timezone.utc)
        self.controller = SimpleNamespace(garage_light_config=lambda: {'camera_motion': {'enabled': True}},
            write_garage_light_hold_state=Mock(), schedule_garage_light_hold_check=Mock())

    def test_active_camera_holds_then_clear_starts_full_countdown(self):
        state = {'active': True, 'holdSeconds': 300}
        with patch.object(camera, 'read', return_value={'occupied': True}):
            self.assertTrue(camera.guard(self.controller, state, self.now))
            self.assertEqual(state['status'], 'camera-occupied')
        cleared = self.now + timedelta(seconds=300)
        with patch.object(camera, 'read', return_value={'occupied': False}):
            self.assertTrue(camera.guard(self.controller, state, cleared))
            self.assertEqual(datetime.fromisoformat(state['holdUntil']), cleared+timedelta(seconds=300))
            self.assertFalse(camera.guard(self.controller, state, cleared+timedelta(seconds=300)))

    def test_unavailable_camera_preserves_light_and_retries_then_recovers(self):
        state = {'active': True}
        with patch.object(camera, 'read', return_value={'occupied': None, 'error': 'unavailable'}):
            self.assertTrue(camera.guard(self.controller, state, self.now))
            self.assertEqual(state['status'], 'camera-unavailable')
            self.assertIn('retryAt', state)
        with patch.object(camera, 'read', return_value={'occupied': False}):
            self.assertFalse(camera.guard(self.controller, state, self.now+timedelta(seconds=60)))
            self.assertNotIn('retryAt', state)
