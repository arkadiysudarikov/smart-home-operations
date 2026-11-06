import json
import io
import urllib.error
from unittest import mock
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from dr_house_ssh import spoken_response, dispatch


class SiriReplyTests(unittest.TestCase):
    def test_extracts_spoken_message_from_nested_cli_output(self):
        raw = json.dumps({"ok": True, "stdout": json.dumps({
            "status": "accepted", "message": "The dryer has finished.",
            "readReceipt": "unavailable"})})
        self.assertEqual(spoken_response(raw), "The dryer has finished.")

    def test_acknowledgement_has_plain_response(self):
        self.assertEqual(spoken_response('{"ok":true,"acknowledged":2}'),
                         "Announcements acknowledged.")

    def test_errors_and_missing_reply_do_not_look_successful(self):
        for payload in ({"ok": False}, {"ok": True, "stdout": "{}"},
                        {"ok": True, "stdout": "not a reply"}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                spoken_response(json.dumps(payload))


    def test_unload_delivery_failure_retains_its_actionable_siri_message(self):
        message = 'Unload recorded, but the Home tile update needs attention.'
        body = json.dumps({'ok': False, 'message': message}).encode()
        error = urllib.error.HTTPError('http://127.0.0.1:18765', 503, 'Unavailable', None, io.BytesIO(body))
        with mock.patch('dr_house_ssh.urllib.request.urlopen', side_effect=error):
            with self.assertRaisesRegex(ValueError, 'Home tile update needs attention'):
                dispatch('washer-unloaded')
