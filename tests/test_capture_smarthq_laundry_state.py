import unittest
import subprocess
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CaptureSmartHQLaundryStateTests(unittest.TestCase):
    def test_decodes_observed_door_and_tank_without_inventing_unknown_values(self):
        code = '''const {laundrySignals:s}=require(process.argv[1]);
console.log(JSON.stringify([s({}),s({erds:{'0x2012':{value:'00',lastOpenedAt:'observed'},'0x203c':{value:'01'}}}),s({erds:{'0x2012':{value:'01'},'0x203c':{value:'02'}}})]));'''
        p=subprocess.run(['node','-e',code,str(ROOT/'scripts/capture_smarthq_laundry_state.js')],capture_output=True,text=True,check=True)
        unknown,low,full=json.loads(p.stdout)
        self.assertIsNone(unknown['doorOpen'])
        self.assertIsNone(unknown['detergentTankLow'])
        self.assertTrue(low['doorOpen'])
        self.assertTrue(low['detergentTankLow'])
        self.assertFalse(full['doorOpen'])
        self.assertFalse(full['detergentTankLow'])
    def test_combo_uses_combination_accessory_and_washer_service(self):
        source = (ROOT / "scripts" / "capture_smarthq_laundry_state.js").read_text()

        self.assertIn('names.has("Combination Washer Dryer")', source)
        self.assertIn(
            'combo: { accessoryName: "Combination Washer Dryer", mainServiceName: "Washer/Dryer" }',
            source,
        )
        self.assertIn("apiLastSuccessAt", source)
        self.assertIn("smarthq_erd_heartbeat.json", source)


if __name__ == "__main__":
    unittest.main()
