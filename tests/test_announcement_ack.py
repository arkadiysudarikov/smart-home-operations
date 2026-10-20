import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import announcement_ack as ack

class AcknowledgementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 1792512000  # Daytime Pacific
        self.event = dict(announcementId='washer', message='Washer finished.', ok=True, at='2026-10-20T13:00:00-07:00')
        self.send = patch('generate_alerts.run_indoor_homepod_announcement')
        self.mock_send = self.send.start()
        self.addCleanup(self.send.stop)
        self.pause = patch('announcement_followup.active', return_value=False)
        self.pause.start()
        self.addCleanup(self.pause.stop)

    def test_repeats_every_five_minutes_until_acknowledged(self):
        ack.record(self.root, self.event, self.now)
        self.assertEqual(ack.tick(self.root, self.now+299), 0)
        self.assertEqual(ack.tick(self.root, self.now+300), 1)
        self.assertEqual(ack.tick(self.root, self.now+599), 0)
        self.assertEqual(ack.tick(self.root, self.now+600), 1)
        self.assertEqual(ack.acknowledge(self.root), 1)
        self.assertEqual(ack.tick(self.root, self.now+900), 0)

    def test_multiple_pending_and_no_replay_recursion(self):
        ack.record(self.root, self.event, self.now)
        ack.record(self.root, dict(self.event, announcementId='dryer'), self.now)
        ack.record(self.root, dict(self.event, announcementId='dr_house_unacknowledged'), self.now)
        self.assertEqual(ack.acknowledge(self.root), 2)

    def test_failed_skipped_and_status_are_not_queued(self):
        for change in ({'ok':False}, {'skipped':True}, {'announcementId':'dr_house_status'}):
            ack.record(self.root, dict(self.event, **change), self.now)
        self.assertEqual(ack.acknowledge(self.root), 0)

    def test_duplicates_do_not_delay_and_new_message_replaces(self):
        ack.record(self.root, self.event, self.now)
        ack.record(self.root, self.event, self.now+250)
        self.assertEqual(ack.tick(self.root, self.now+300), 1)
        ack.record(self.root, dict(self.event, message='Please unload the washer.'), self.now+301)
        self.assertEqual(ack.tick(self.root, self.now+600), 0)
        self.assertEqual(ack.tick(self.root, self.now+601), 1)

    def test_recovery_clears_energy_pending(self):
        ack.record(self.root, dict(self.event, announcementId='energy_high_on'), self.now)
        ack.record(self.root, dict(self.event, announcementId='energy_high_clear'), self.now+1)
        self.assertEqual(ack.acknowledge(self.root), 0)

    def test_uncertain_delivery_waits_five_minutes(self):
        ack.record(self.root, self.event, self.now)
        self.mock_send.side_effect=RuntimeError('uncertain')
        self.assertEqual(ack.tick(self.root, self.now+300), 1)
        self.assertEqual(ack.tick(self.root, self.now+301), 0)
        self.assertEqual(ack.tick(self.root, self.now+600), 1)

    def test_pause_and_calendar_privacy(self):
        ack.record(self.root, self.event, self.now)
        with patch('announcement_followup.active', return_value=True):
            self.assertEqual(ack.tick(self.root, self.now+300), 0)
        ack.record(self.root, dict(self.event, announcementId='calendar-'+'a'*64), self.now)
        with patch('announcement_followup.can_disclose', return_value=False):
            self.assertEqual(ack.tick(self.root, self.now+300), 1)  # washer only

    def test_acknowledgement_cancels_snooze(self):
        (self.root/'announcement_followup.json').write_text('{"dueAt":1}')
        ack.acknowledge(self.root)
        self.assertEqual(json.loads((self.root/'announcement_followup.json').read_text()), {})
