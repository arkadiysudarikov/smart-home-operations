import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import announcement_followup as follow
import calendar_announcements as cal
import house_briefing as house
import dr_house_ssh


class FollowupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        p = patch.object(follow.alerts, "DATA_DIR", self.root)
        p.start()
        self.addCleanup(p.stop)
        self.now = datetime(2026, 9, 16, 19, tzinfo=timezone.utc).timestamp()
        self.event = dict(at=datetime.fromtimestamp(self.now, timezone.utc).isoformat(),
                          announcementId="washer", message="Washer finished.", ok=True)
        self.log(self.event)

    def log(self, *events):
        (self.root / "homepod_announcement_events.jsonl").write_text("\n".join(json.dumps(e) for e in events))

    def test_repeat_is_recorded_text_not_a_fresh_claim(self):
        self.assertEqual(follow.request("repeat", self.now), "Earlier announcement: Washer finished.")

    def test_unknown_failed_skipped_stale_future_and_unsafe_fail_closed(self):
        for changes in ({"announcementId": "email"}, {"announcementId": "dr_house_status"},
                        {"ok": False}, {"skipped": True}, {"at": "bad"},
                        {"at": datetime.fromtimestamp(self.now + 1, timezone.utc).isoformat()},
                        {"message": "x" * 1401}, {"message": "HOMESAFE-7D3B9A21:injected"}):
            self.assertFalse(follow.approved(dict(self.event, **changes), self.now))
        self.assertFalse(follow.approved(self.event, self.now + 601))

    def test_control_replies_do_not_replace_last_announcement(self):
        self.log(self.event, dict(self.event, announcementId="dr_house_snooze", message="Acknowledgment"))
        self.assertEqual(follow.recent(self.now), self.event)

    def test_dry_snooze_does_not_write(self):
        follow.request("snooze", self.now)
        self.assertFalse((self.root / "announcement_followup.json").exists())

    def test_snooze_once_consumed_before_send(self):
        follow.request("snooze", self.now, mutate=True)
        path = self.root / "announcement_followup.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with patch.object(follow, "active", return_value=False), patch.object(follow.alerts, "run_indoor_homepod_announcement") as send:
            send.side_effect = lambda *args: self.assertEqual(json.loads(path.read_text()), {})
            self.assertEqual(follow.tick(self.now + 599), "waiting")
            send.assert_not_called()
            self.assertEqual(follow.tick(self.now + 600), "attempted")
            self.assertEqual(follow.tick(self.now + 601), "empty")
            send.assert_called_once()

    def test_uncertain_send_never_retries(self):
        follow.request("snooze", self.now, mutate=True)
        with patch.object(follow, "active", return_value=False), patch.object(follow.alerts, "run_indoor_homepod_announcement", side_effect=RuntimeError("uncertain")) as send:
            with self.assertRaises(RuntimeError):
                follow.tick(self.now + 600)
            self.assertEqual(follow.tick(self.now + 601), "empty")
            send.assert_called_once()

    def test_late_or_paused_snooze_drops(self):
        with patch.object(follow.alerts, "run_indoor_homepod_announcement") as send:
            follow.request("snooze", self.now, mutate=True)
            self.assertEqual(follow.tick(self.now + 721), "expired_or_quiet")
            follow.request("snooze", self.now, mutate=True)
            with patch.object(follow, "active", return_value=True):
                self.assertEqual(follow.tick(self.now + 600), "suppressed")
            send.assert_not_called()

    def test_quiet_hours_do_not_speak(self):
        night = datetime(2026, 9, 17, 5, tzinfo=timezone.utc).timestamp()
        self.event["at"] = datetime.fromtimestamp(night, timezone.utc).isoformat()
        self.log(self.event)
        follow.request("snooze", night, mutate=True)
        with patch.object(follow.alerts, "run_indoor_homepod_announcement") as send:
            self.assertEqual(follow.tick(night + 600), "expired_or_quiet")
            send.assert_not_called()

    def test_calendar_exact_event_and_presence_required(self):
        now = datetime.now(timezone.utc)
        appointment = dict(id="test", calendar="Jeanne", start=(now + timedelta(hours=1)).isoformat(), status=0)
        event = dict(self.event, announcementId="calendar-" + cal.occurrence(appointment))
        config = self.root / "calendar.json"
        config.write_text(json.dumps({"phones": {"Jeanne": "phone"}}))
        with patch.object(cal, "CONFIG", config), patch.object(cal, "invoke", return_value={"generatedAt": now.isoformat(), "events": [appointment]}), patch.object(cal, "active_clients", return_value=[]) as clients:
            self.assertFalse(follow.can_disclose(event, now.timestamp()))
            clients.return_value = [dict(mac="phone", last_seen=now.timestamp())]
            self.assertTrue(follow.can_disclose(event, now.timestamp()))
            appointment["status"] = 3
            self.assertFalse(follow.can_disclose(event, now.timestamp()))

    def test_explanation_uses_recorded_transition_not_current_energy(self):
        self.assertIn("cleared", follow.explain(dict(self.event, announcementId="energy_high_clear")))
        self.assertIn("not saved", follow.explain(dict(self.event, announcementId="household_reminder")))

    def test_calendar_departure_suppresses_queued_replay(self):
        follow.request("snooze", self.now, mutate=True)
        with patch.object(follow, "can_disclose", return_value=False), patch.object(follow, "active", return_value=False), patch.object(follow.alerts, "run_indoor_homepod_announcement") as send:
            self.assertEqual(follow.tick(self.now + 600), "suppressed")
            send.assert_not_called()

    def test_modes_in_both_allowlists(self):
        for mode in ("repeat", "snooze", "why"):
            self.assertIn(mode, house.MODES)
            self.assertIn(mode, dr_house_ssh.MODES)
