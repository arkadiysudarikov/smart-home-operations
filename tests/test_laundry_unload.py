import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import laundry_unload
import washer_notifier


class LaundryUnloadTest(unittest.TestCase):
    def test_acknowledgement_persists_and_only_clears_selected_appliance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / 'data'
            data.mkdir()
            prior = {'awaitingUnload': True, 'reminderSent': True, 'reminderLatched': True}
            for appliance in ['washer', 'dryer']:
                (data / f'{appliance}_notifier_state.json').write_text(json.dumps(prior))
            with mock.patch.object(washer_notifier, 'execute_actions', side_effect=lambda actions, config, dry: [{'ok': True, 'action': a} for a in actions]):
                result = laundry_unload.acknowledge_unloaded(root, 'washer')
            self.assertTrue(result['ok'])
            washer = json.loads((data / 'washer_notifier_state.json').read_text())
            self.assertFalse(washer['awaitingUnload'])
            self.assertFalse(washer['reminderLatched'])
            self.assertEqual(washer['unloadedBy'], 'user')
            self.assertEqual(json.loads((data / 'dryer_notifier_state.json').read_text()), prior)
            with self.assertRaises(ValueError):
                laundry_unload.acknowledge_unloaded(root, 'all')
