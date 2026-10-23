import contextlib
import fcntl
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import action_server as server
import house_briefing as house

class SiriAcknowledgementTests(unittest.TestCase):
    def seed(self, directory):
        data=directory/'data';data.mkdir()
        (data/'announcement_ack.json').write_text(json.dumps({'pending':{'dryer':{'event':{'message':'Dryer finished'}}}}))
        (data/'announcement_followup.json').write_text(json.dumps({'event':{'message':'Dryer finished'}}))
        return data

    def test_http_aliases_clear_reminders_without_speaker_relay(self):
        for route in ['/action/dr-house-acknowledge','/action/acknowledge-announcements']:
            with self.subTest(route=route), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);data=self.seed(root)
                handler=object.__new__(server.Handler);handler.path=route;handler.command='POST';handler.headers={};handler.client_address=('127.0.0.1',1234)
                with patch.object(server,'ROOT',root),patch.object(server.Handler,'request_origin_allowed',return_value=True),patch.object(server,'run',side_effect=AssertionError('must not call speaker relay')):
                    status,result=handler.route()
                    self.assertEqual(status,200);self.assertTrue(result['ok']);self.assertEqual(result['acknowledged'],1)
                    self.assertEqual(handler.route()[1]['acknowledged'],0)
                self.assertEqual(json.loads((data/'announcement_ack.json').read_text())['pending'],{})
                self.assertEqual(json.loads((data/'announcement_followup.json').read_text()),{})

    def test_cli_ack_succeeds_during_busy_briefing_without_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=self.seed(Path(tmp))
            with (data/'dr_house.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with patch.object(sys,'argv',['house_briefing.py','--mode','acknowledge','--speak']),patch.object(house.alerts,'DATA_DIR',data),patch.object(house.alerts,'running_from_runtime_root',return_value=True),patch.object(house.alerts,'run_indoor_homepod_announcement',side_effect=AssertionError('relay failed')),contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(house.main(),0)
                self.assertEqual(json.loads(output.getvalue())['acknowledged'],1)
