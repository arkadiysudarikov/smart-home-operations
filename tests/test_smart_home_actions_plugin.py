#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PATH = ROOT / "plugins" / "homebridge-smart-home-actions" / "index.js"
NODE = Path.home() / ".local" / "node-v24.16.0-darwin-arm64" / "bin" / "node"


class SmartHomeActionsPluginTest(unittest.TestCase):
    def test_known_actions_use_current_display_names(self) -> None:
        script = """
const plugin = require(process.argv[1]);
const actions = plugin.normalizeConfiguredActions([
  { id: "check", name: "Run Home Check", path: "/custom-check", timeoutMs: 42 },
  { id: "office-restart", name: "Restart Office Shades", path: "/action/restart-office-tahoma" },
  { id: "custom", name: "Custom Action", path: "/custom" },
]);
process.stdout.write(JSON.stringify(actions));
"""
        result = subprocess.run(
            [str(NODE), "-e", script, str(PLUGIN_PATH)],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": f"{NODE.parent}:{os.environ.get('PATH', '')}"},
        )

        actions = json.loads(result.stdout)
        by_id = {item["id"]: item for item in actions}
        self.assertEqual(by_id["check"]["name"], "⚙️ Home Check")
        self.assertEqual(by_id["check"]["path"], "/custom-check")
        self.assertEqual(by_id["check"]["timeoutMs"], 42)
        self.assertEqual(by_id["custom"]["name"], "Custom Action")
        self.assertEqual(by_id["hb-restart"]["name"], "⚙️ Restart Hub")
        self.assertEqual(by_id["mute-alerts"]["name"], "⚙️ Pause Alerts")
        self.assertEqual(by_id["refresh-sce"]["name"], "⚙️ Refresh SCE")
        self.assertEqual(by_id["reconcile-energy"]["name"], "⚙️ Refresh Energy")
        self.assertEqual(by_id["alarm-refresh"]["name"], "⚙️ Refresh Alarm")
        self.assertEqual(by_id["garage-activity"]["name"], "⚙️ Garage Timer")
        self.assertEqual(by_id["screens-awake"]["name"], "⚙️ Screens Awake")
        self.assertEqual(by_id["screens-auto"]["name"], "⚙️ Screens Auto")
        self.assertEqual(by_id["screens-awake"]["path"], "/action/screens-awake")
        self.assertEqual(by_id["screens-auto"]["path"], "/action/screens-auto")
        self.assertEqual(by_id["panel-home"]["name"], "🛡️ Armed")
        self.assertEqual(by_id["panel-stay"]["name"], "🛡️ Armed Stay")
        self.assertEqual(by_id["panel-off"]["name"], "🛡️ Off")
        self.assertEqual(by_id["panel-home"]["path"], "/action/panel-home")
        self.assertEqual(by_id["panel-stay"]["path"], "/action/panel-stay")
        self.assertEqual(by_id["panel-off"]["path"], "/action/panel-off")
        self.assertNotIn("office-restart", by_id)

    def test_service_names_are_refreshed_for_cached_accessories(self) -> None:
        source = PLUGIN_PATH.read_text()

        self.assertIn("service.displayName = action.name", source)
        self.assertIn("service.addOptionalCharacteristic(this.Characteristic.ConfiguredName)", source)
        self.assertIn("service.setCharacteristic(this.Characteristic.ConfiguredName, action.name)", source)
        self.assertNotIn("getCharacteristic(this.Characteristic.ConfiguredName)", source)
        self.assertIn("this.Characteristic.Name, action.name", source)
        self.assertIn("this.api.updatePlatformAccessories([accessory])", source)
        self.assertIn('"X-Smart-Home-Source": PLUGIN_NAME', source)
        self.assertIn('"X-Smart-Home-Reason": `homekit-switch:${action.id || "custom"}`', source)

    def test_startup_replay_protection_suppresses_only_early_on_events(self) -> None:
        script = """
const plugin = require(process.argv[1]);
const deadline = 10000;
process.stdout.write(JSON.stringify({
  defaultMs: plugin.DEFAULT_STARTUP_REPLAY_PROTECTION_MS,
  earlyOn: plugin.shouldSuppressStartupReplay(true, 9999, deadline),
  earlyOff: plugin.shouldSuppressStartupReplay(false, 9999, deadline),
  atDeadline: plugin.shouldSuppressStartupReplay(true, deadline, deadline),
  afterDeadline: plugin.shouldSuppressStartupReplay(true, 10001, deadline),
}));
"""
        result = subprocess.run(
            [str(NODE), "-e", script, str(PLUGIN_PATH)],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": f"{NODE.parent}:{os.environ.get('PATH', '')}"},
        )

        behavior = json.loads(result.stdout)
        self.assertEqual(behavior["defaultMs"], 60_000)
        self.assertTrue(behavior["earlyOn"])
        self.assertFalse(behavior["earlyOff"])
        self.assertFalse(behavior["atDeadline"])
        self.assertFalse(behavior["afterDeadline"])

    def test_cached_switch_state_is_cleared_before_set_listener_is_armed(self) -> None:
        source = PLUGIN_PATH.read_text()

        clear_index = source.index("onCharacteristic.updateValue(false);")
        set_listener_index = source.index('.on("set", async (value, callback) => {')
        self.assertLess(clear_index, set_listener_index)
        self.assertIn("startupReplayProtectionUntil = Date.now()", source)
        self.assertIn("ignored during startup replay protection", source)


if __name__ == "__main__":
    unittest.main()
