import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import announcement_presence
import washer_notifier


class PresenceTests(unittest.TestCase):
    def test_only_fresh_explicit_home_readings_allow_speech(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            now = 1800000000
            for present, age, expected in [(True, 30, True), (False, 30, False),
                                         (None, 30, False), ('true', 30, False),
                                         (True, 181, False), (True, -1, False)]:
                with self.subTest(present=present, age=age):
                    (root / 'latest_display_awake.json').write_text(json.dumps({
                        'generatedAt': datetime.fromtimestamp(now-age, timezone.utc).isoformat(),
                        'presence': {'homePresent': present}}))
                    self.assertEqual(announcement_presence.allowed(root, now), expected)
            (root / 'latest_display_awake.json').write_text('{}')
            self.assertFalse(announcement_presence.allowed(root, now))

    def test_away_blocks_all_transports_and_does_not_enqueue(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(washer_notifier, 'DATA_DIR', Path(temp)), patch('announcement_presence.allowed', return_value=False), patch.object(washer_notifier.subprocess, 'run') as run:
            for identifier in ('washer', 'help_request', 'dr_house_unacknowledged', 'calendar-'+'a'*64):
                result = washer_notifier.homepod_announcement('Private reminder.', {'id': identifier, 'homepod_targets': ['Office']})
                self.assertTrue(result['skipped'])
            run.assert_not_called()
            self.assertFalse((Path(temp)/'announcement_ack.json').exists())
