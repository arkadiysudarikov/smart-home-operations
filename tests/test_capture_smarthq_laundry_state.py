import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CaptureSmartHQLaundryStateTests(unittest.TestCase):
    def test_combo_uses_combination_accessory_and_washer_service(self):
        source = (ROOT / "scripts" / "capture_smarthq_laundry_state.js").read_text()

        self.assertIn('names.has("Combination Washer Dryer")', source)
        self.assertIn(
            'combo: { accessoryName: "Combination Washer Dryer", mainServiceName: "Washer" }',
            source,
        )


if __name__ == "__main__":
    unittest.main()
