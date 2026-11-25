import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("opportunities", ROOT / "scripts/analyze_energy_automation_opportunities.py")
opportunities = importlib.util.module_from_spec(spec)
spec.loader.exec_module(opportunities)


class OpportunityTests(unittest.TestCase):
    def test_review_and_report_use_observed_rules(self):
        capture = {"garageLightRules": [{"isPaused": False}] * 4}
        review = {"reviewedAt": "2026-11-25T17:02:00Z", "source": "live_Apple_Home_UI",
                  "rules": [{"trigger": "garage-motion", "target": "garage-light", "action": "on",
                             "enabled": True, "window": "always"}], "coverage": {"scope": "partial"},
                  "complete": False}
        def load(path):
            return review if path.name == "home_automation_review.json" else capture if path.name == "alarm_com_automation_rules.json" else {}
        with mock.patch.object(opportunities, "load_json", side_effect=load):
            payload = opportunities.build_payload()
        garage = next(x for x in payload["opportunities"] if x["area"] == "Garage lighting")
        self.assertIn("4 enabled", garage["finding"])
        self.assertEqual(payload["homeAppObserved"]["rules"], review["rules"])
        self.assertFalse(payload["homeAppObserved"]["complete"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(opportunities, "DATA_DIR", root), mock.patch.object(opportunities, "REPORT_DIR", root), mock.patch.object(opportunities, "OUT_JSON", root / "out.json"), mock.patch.object(opportunities, "OUT_REPORT", root / "out.md"):
                opportunities.write_report(payload)
            report = (root / "out.md").read_text()
            self.assertIn("garage-motion", report)
            self.assertNotIn("Garage Activity", report)
