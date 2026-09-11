import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import house_briefing as house

NOW = "2026-09-11T16:00:00-07:00"


class BriefingTests(unittest.TestCase):
    def context(self):
        return {"generatedAt": NOW, "sampleAt": NOW, "liveLoadKw": 5, "thresholdKw": 3.5,
                "candidates": [{"source": "Sense", "capturedAt": NOW, "devices": [{"name": "Dryer", "watts": 3000}, {"name": "Solar", "watts": 9000}]}]}

    def test_high_is_evidence_backed(self):
        text = house.energy_message(self.context(), NOW, True)
        self.assertIn("elevated demand", text)
        self.assertIn("Dryer at 3.0", text)
        self.assertNotIn("Solar", text)
        self.assertIn("does not establish the full cause", text)

    def test_stale_source_never_names_cause(self):
        data = self.context()
        data["sampleAt"] = "2026-09-10T16:00:00-07:00"
        self.assertIn("deferred", house.energy_message(data, NOW, True))
        data = self.context()
        data["candidates"][0]["capturedAt"] = data["sampleAt"] = NOW
        data["candidates"][0]["capturedAt"] = "2026-09-10T16:00:00-07:00"
        self.assertNotIn("Dryer", house.energy_message(data, NOW, True))

    def test_normal_does_not_explain_nonexistent_alert(self):
        data = self.context()
        data["liveLoadKw"] = 1
        text = house.energy_message(data, NOW, True)
        self.assertIn("not currently active", text)
        self.assertNotIn("Dryer", text)

    def test_laundry_requires_fresh_heartbeat_and_never_infers_finished(self):
        laundry = {"ok": True, "capturedAt": NOW, "devices": {"washer": {"cycleActive": True, "remainingSeconds": 2400, "apiLastSuccessAt": NOW}}}
        self.assertIn("40 minutes", house.status_message({}, laundry, self.context(), NOW))
        laundry["devices"]["washer"]["apiLastSuccessAt"] = "2026-09-10T16:00:00-07:00"
        text = house.status_message({}, laundry, {}, NOW)
        self.assertNotIn("40 minutes", text)
        self.assertNotIn("finished", text)
