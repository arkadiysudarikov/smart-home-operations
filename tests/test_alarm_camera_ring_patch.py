import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from patch_alarm_camera_ring import patched, OLD, NEW


class CameraPatchTests(unittest.TestCase):
    def test_patch_preserves_other_event_handlers_and_is_idempotent(self):
        source = 'before\n'+OLD+'\nanalytics-handler-after'
        result = patched(source)
        self.assertEqual(result, 'before\n'+NEW+'\nanalytics-handler-after')
        self.assertEqual(patched(result),result)

    def test_unreviewed_layout_is_not_modified(self):
        with self.assertRaises(ValueError): patched('unknown handler')
