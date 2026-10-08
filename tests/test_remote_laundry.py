import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import remote_laundry


class RemoteLaundryTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.payload = {'host':'m4-office-mini','capture':{'ok':True,'capturedAt':self.now.isoformat()}}

    def test_valid_host_and_original_time_preserved(self):
        self.assertIs(remote_laundry.validate(self.payload,'m4-office-mini',self.now),self.payload)

    def test_wrong_host_rejected(self):
        with self.assertRaises(ValueError): remote_laundry.validate(self.payload,'other',self.now)

    def test_old_capture_rejected(self):
        self.payload['capture']['capturedAt']=(self.now-timedelta(minutes=3)).isoformat()
        with self.assertRaises(ValueError): remote_laundry.validate(self.payload,'m4-office-mini',self.now)

    def test_failed_capture_rejected(self):
        self.payload['capture']['ok']=False
        with self.assertRaises(ValueError): remote_laundry.validate(self.payload,'m4-office-mini',self.now)
