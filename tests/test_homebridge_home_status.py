from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "homebridge-home-status" / "index.js"
NODE = Path.home() / ".local" / "node-v24.16.0-darwin-arm64" / "bin" / "node"


class HomeStatusDashboardPluginTest(unittest.TestCase):
    def run_node(self, source: str) -> dict:
        result = subprocess.run(
            [str(NODE), "-e", source],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(result.stdout)

    def test_discovers_only_passive_sensor_characteristics(self) -> None:
        source = f"""
const plugin = require({json.dumps(str(PLUGIN))});
const payload = {{homeEvents: {{currentCharacteristics: {{
  occupancy: {{platform: 'UnifiOccupancy', accessory: 'Office Mac', service: 'Office Mac', characteristic: 'OccupancyDetected', value: 1}},
  temperature: {{platform: 'DysonPureCoolPlatform', accessory: 'Garage Temperature', service: 'Garage Temperature', characteristic: 'CurrentTemperature', value: 72}},
  contact: {{platform: 'Alarmdotcom', accessory: 'Entry Door', service: 'Entry Door', characteristic: 'ContactSensorState', value: 0}},
  battery: {{platform: 'enphaseEnvoy', accessory: 'Battery 1', service: 'Battery 1', characteristic: 'StatusLowBattery', value: 1}},
  action: {{platform: 'SmartHomeActions', accessory: 'Restart Hub', service: 'Restart Hub', characteristic: 'On', value: false}},
  switch: {{platform: 'Alarmdotcom', accessory: 'Entry Light', service: 'Entry Light', characteristic: 'On', value: true}}
}}}}}};
const sensors = plugin.discoverSensors(payload);
console.log(JSON.stringify({{count: sensors.length, sensors, names: sensors.map(plugin.displayName)}}));
"""
        payload = self.run_node(source)

        self.assertEqual(payload["count"], 4)
        self.assertEqual(
            {item["sourceCharacteristic"] for item in payload["sensors"]},
            {"OccupancyDetected", "CurrentTemperature", "ContactSensorState", "StatusLowBattery"},
        )
        self.assertIn("Battery 1 Battery Low", payload["names"])

    def test_normalizes_homekit_sensor_values(self) -> None:
        source = f"""
const plugin = require({json.dumps(str(PLUGIN))});
const values = [
  plugin.normalizeValue({{sourceCharacteristic: 'OccupancyDetected'}}, true),
  plugin.normalizeValue({{sourceCharacteristic: 'ContactSensorState'}}, 1),
  plugin.normalizeValue({{sourceCharacteristic: 'CurrentRelativeHumidity'}}, 130),
  plugin.normalizeValue({{sourceCharacteristic: 'CurrentAmbientLightLevel'}}, 0)
];
console.log(JSON.stringify({{values}}));
"""
        self.assertEqual(self.run_node(source)["values"], [1, 1, 100, 0.0001])


if __name__ == "__main__":
    unittest.main()
