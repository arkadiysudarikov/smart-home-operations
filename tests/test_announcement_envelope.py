import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from announcement_envelope import encode, extract, START, END


class EnvelopeTests(unittest.TestCase):
    def test_only_payload_is_returned(self):
        self.assertEqual(extract(encode("The washer has finished.")), ["The washer has finished."])

    def test_ordinary_mail_never_matches(self):
        for body in ("Your private email body", "Subject: homeannounce\nPrivate email body", "", START + "truncated", "unrelated" + END):
            self.assertEqual(extract(body), [])

    def test_surrounding_mail_cannot_enter_payload(self):
        self.assertEqual(extract("Private before\n" + encode("Test announcement.") + "\nPrivate after"), ["Test announcement."])

    def test_rejects_invalid_payloads(self):
        for text in ("", "x" * 1501, START + "injected", "injected" + END):
            with self.assertRaises(ValueError):
                encode(text)
        self.assertEqual(extract(START + "line one\nprivate line" + END), [])
