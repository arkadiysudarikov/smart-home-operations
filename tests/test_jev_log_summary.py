import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from jev_operations import log_summary


class LogSummaryTests(unittest.TestCase):
    def test_protocol_payload_numbers_do_not_count_as_http_failures(self):
        now = datetime(2026, 11, 25, 12, tzinfo=ZoneInfo("America/Los_Angeles")).timestamp()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "homebridge.log"
            path.write_text(
                "[11/25/2026, 11:59:00 AM] [Matter/MessageChannel] payload: 1401429ff401af\n"
                "[11/25/2026, 11:59:01 AM] [SmartHQ] Error: HTTP 429 Too Many Requests\n"
                "[11/25/2026, 11:59:02 AM] [UniFi] Response status: 401\n"
            )
            result = log_summary(path, now)
        self.assertEqual(result["rate_limits"], 1)
        self.assertEqual(result["authentication"], 1)
