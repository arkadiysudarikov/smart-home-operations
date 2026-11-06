import json
import io
import base64
import urllib.error
from unittest import mock
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from dr_house_ssh import spoken_response, dispatch
import shortcuts_acknowledge as legacy


class SiriReplyTests(unittest.TestCase):
    def test_legacy_mac_key_does_not_gain_unrelated_house_commands(self):
        with mock.patch.object(legacy.subprocess, 'run') as run, mock.patch.object(legacy.urllib.request, 'urlopen') as send:
            for command in ['status','washer-unloaded','codex-status; whoami','codex-request','acknowledge\n']:
                with self.assertRaises(ValueError):legacy.dispatch(command)
        run.assert_not_called();send.assert_not_called()

    def test_legacy_mac_key_forwards_only_to_the_fixed_validating_dispatcher(self):
        with mock.patch.object(legacy.subprocess,'run',return_value=mock.Mock(returncode=0,stdout='Codex finished.')) as run:
            self.assertEqual(legacy.dispatch('codex-status'),'Codex finished.')
        self.assertEqual(run.call_args.kwargs['env']['SSH_ORIGINAL_COMMAND'],'codex-status')
        self.assertTrue(run.call_args.args[0][-1].endswith('/scripts/dr_house_ssh.py'))
        self.assertNotIn('shell',run.call_args.kwargs)

    def test_codex_encoded_request_is_data_and_does_not_reach_house_actions(self):
        text = 'Reply with $(touch /tmp/not-a-command); `whoami` and "quotes".'
        with mock.patch('dr_house_ssh.sys.stdin', io.StringIO(text)), \
             mock.patch('dr_house_ssh.subprocess.run', return_value=mock.Mock(returncode=0, stdout='Request started.')) as run, \
             mock.patch('dr_house_ssh.urllib.request.urlopen') as house:
            self.assertEqual(dispatch('codex-request '+base64.b64encode(text.encode()).decode()), 'Request started.')
        self.assertEqual(run.call_args.kwargs['input'], text)
        self.assertEqual(run.call_args.args[0][-1], str(Path(__file__).resolve().parents[1]/'scripts/siri_codex.py'))
        self.assertNotIn('shell', run.call_args.kwargs)
        house.assert_not_called()

    def test_codex_status_does_not_consume_stdin_and_failed_handoff_is_explicit(self):
        with mock.patch('dr_house_ssh.sys.stdin') as incoming, \
             mock.patch('dr_house_ssh.subprocess.run', return_value=mock.Mock(returncode=1, stdout='')):
            with self.assertRaisesRegex(ValueError, 'could not complete'):
                dispatch('codex-status')
        incoming.read.assert_not_called()

    def test_codex_ssh_command_arguments_are_rejected(self):
        with mock.patch('dr_house_ssh.subprocess.run') as run:
            for command in ['codex-request', 'codex-request; whoami', 'codex-request anything', 'codex-status\n', 'codex-request --sandbox danger-full-access',
                            'codex-request '+base64.b64encode(b'\x00bad').decode(),
                            'codex-request '+base64.b64encode(b'x'*8001).decode()]:
                with self.assertRaises(ValueError):
                    dispatch(command)
        run.assert_not_called()

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
