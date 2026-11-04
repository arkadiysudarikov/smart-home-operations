import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from dr_house_ssh import spoken_response
import dr_house_ssh
from unittest import mock
import subprocess


class SiriReplyTests(unittest.TestCase):
    def test_laundry_reply_does_not_depend_on_http_or_homepod_relay(self):
        with mock.patch.object(dr_house_ssh.subprocess, "run", return_value=
                subprocess.CompletedProcess([], 0, "The dryer has finished.\n", "")) as run, \
                mock.patch.object(dr_house_ssh.urllib.request, "urlopen", side_effect=RuntimeError("relay unavailable")) as http:
            self.assertEqual(dr_house_ssh.dispatch("diagnostics"), "The dryer has finished.")
            http.assert_not_called()
            self.assertNotIn("--speak", run.call_args.args[0])

    def test_laundry_read_failure_is_not_reported_as_success(self):
        with mock.patch.object(dr_house_ssh.subprocess, "run", return_value=
                subprocess.CompletedProcess([], 1, "", "read failed")):
            with self.assertRaises(ValueError):
                dr_house_ssh.dispatch("diagnostics")

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
