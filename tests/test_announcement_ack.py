import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import announcement_ack as ack

class AcknowledgementTests(unittest.TestCase):
    def test_generic_household_notice_is_not_queued(self):
        ack.record(self.root, dict(self.event, announcementId='household_reminder'), self.now)
        self.assertEqual(ack.acknowledge(self.root), 0)

    def test_legacy_household_repeat_is_removed_without_speaking(self):
        event = dict(self.event, announcementId='household_reminder')
        (self.root/'announcement_ack.json').write_text(json.dumps({'pending': {
            'household_reminder': {'event': event, 'dueAt': self.now}}}))
        self.assertEqual(ack.tick(self.root, self.now+300), 0)
        self.mock_send.assert_not_called()
        self.assertEqual(json.loads((self.root/'announcement_ack.json').read_text())['pending'], {})

    def test_opened_appliance_clears_only_its_queue_and_snooze(self):
        ack.record(self.root, self.event, self.now)
        ack.record(self.root, dict(self.event, announcementId='dryer'), self.now)
        (self.root/'announcement_followup.json').write_text(json.dumps({'event': self.event}))
        self.assertTrue(ack.acknowledge_appliance(self.root, 'washer'))
        self.assertEqual(set(json.loads((self.root/'announcement_ack.json').read_text())['pending']), {'dryer'})
        self.assertEqual(json.loads((self.root/'announcement_followup.json').read_text()), {})
        with self.assertRaises(ValueError):
            ack.acknowledge_appliance(self.root, 'energy_high_on')

    def test_other_appliance_snooze_survives(self):
        (self.root/'announcement_followup.json').write_text(json.dumps({'event': {'announcementId':'dryer'}}))
        ack.acknowledge_appliance(self.root, 'washer')
        self.assertEqual(json.loads((self.root/'announcement_followup.json').read_text())['event']['announcementId'], 'dryer')

    def test_refilled_tank_clears_only_detergent_reminder(self):
        ack.record(self.root, self.event, self.now)
        ack.record(self.root, dict(self.event, announcementId='detergent_tank_low'), self.now)
        self.assertTrue(ack.clear_source(self.root, 'detergent_tank_low'))
        self.assertEqual(set(json.loads((self.root/'announcement_ack.json').read_text())['pending']), {'washer'})
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

    def test_repeats_with_progressive_backoff_until_acknowledged(self):
        ack.record(self.root, self.event, self.now)
        self.assertEqual(ack.tick(self.root, self.now+299), 0)
        self.assertEqual(ack.tick(self.root, self.now+300), 1)
        self.assertEqual(ack.tick(self.root, self.now+599), 0)
        self.assertEqual(ack.tick(self.root, self.now+600), 0)
        self.assertEqual(ack.tick(self.root, self.now+900), 1)
        self.assertEqual(ack.tick(self.root, self.now+2099), 0)
        self.assertEqual(ack.tick(self.root, self.now+2100), 1)
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

    def test_uncertain_delivery_also_backs_off(self):
        ack.record(self.root, self.event, self.now)
        self.mock_send.side_effect=RuntimeError('uncertain')
        self.assertEqual(ack.tick(self.root, self.now+300), 1)
        self.assertEqual(ack.tick(self.root, self.now+301), 0)
        self.assertEqual(ack.tick(self.root, self.now+600), 0)
        self.assertEqual(ack.tick(self.root, self.now+900), 1)

    def test_backoff_caps_and_survives_reload_without_duplicate_reset(self):
        ack.record(self.root, self.event, self.now)
        now = self.now
        for delay, next_delay in [(300,600),(600,1200),(1200,2400),(2400,3600),(3600,3600)]:
            now += delay
            self.assertEqual(ack.tick(self.root, now), 1)
            before = json.loads((self.root/'announcement_ack.json').read_text())
            self.assertEqual(before['pending']['washer']['dueAt'], now+next_delay)
            ack.record(self.root, self.event, now+1)
            self.assertEqual(json.loads((self.root/'announcement_ack.json').read_text()), before)

    def test_legacy_pending_starts_backoff_without_replaying_immediately(self):
        (self.root/'announcement_ack.json').write_text(json.dumps({'pending': {
            'washer': {'event':self.event,'dueAt':self.now+300}}}))
        self.assertEqual(ack.tick(self.root, self.now+299), 0)
        self.assertEqual(ack.tick(self.root, self.now+300), 1)
        self.assertEqual(json.loads((self.root/'announcement_ack.json').read_text())['pending']['washer']['dueAt'], self.now+900)

    def test_pause_and_calendar_privacy(self):
        ack.record(self.root, self.event, self.now)
        with patch('announcement_followup.active', return_value=True):
            self.assertEqual(ack.tick(self.root, self.now+300), 0)
        ack.record(self.root, dict(self.event, announcementId='calendar-'+'a'*64), self.now)
        with patch('announcement_followup.calendar_disposition', side_effect=lambda event, now: 'private' if event['announcementId'].startswith('calendar-') else 'allowed'):
            self.assertEqual(ack.tick(self.root, self.now+300), 1)  # washer only

    def test_acknowledgement_cancels_snooze(self):
        (self.root/'announcement_followup.json').write_text('{"dueAt":1}')
        ack.acknowledge(self.root)
        self.assertEqual(json.loads((self.root/'announcement_followup.json').read_text()), {})
