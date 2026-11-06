import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import siri_codex as s


class SiriCodex(unittest.TestCase):
    def test_dictation_is_data_not_shell_source(self):
        text='Explain `touch /tmp/not-executed` and $(whoami); do not run them.'
        with tempfile.TemporaryDirectory() as d,patch.object(s,'JOBS',Path(d)/'jobs'),patch.object(s,'WORKSPACE',Path(d)/'workspace'):
            spawn=Mock(return_value=Mock(pid=12345))
            self.assertIn('started',s.start(text,spawn=spawn))
            args=spawn.call_args.args[0]
            self.assertNotIn(text,args)
            self.assertEqual(next((Path(d)/'jobs').glob('*/request.txt')).read_text(),text)
            self.assertNotIn('shell',spawn.call_args.kwargs)

    def test_input_bounds_and_ordinary_approval_policy(self):
        for text in ('',' '*4,'x'*8001,'\x00'):
            with self.assertRaises(ValueError):s.normalize(text)
        cmd=s.command(Path('/tmp/work'),Path('/tmp/result'))
        self.assertIn('on-request',cmd);self.assertIn('workspace-write',cmd)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox',cmd)


if __name__=='__main__':unittest.main()
