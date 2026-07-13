#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "patch_calendar_display_names.js"
NODE = Path.home() / ".local" / "node-v24.16.0-darwin-arm64" / "bin" / "node"


class PatchCalendarDisplayNamesTest(unittest.TestCase):
    def make_plugin(self, root: Path) -> tuple[Path, Path, Path, Path]:
        configs = root / "dist" / "configs"
        configs.mkdir(parents=True)
        calendar = configs / "calendar.config.js"
        event = configs / "event.config.js"
        handler = root / "dist" / "calendar.handler.js"
        accessory = root / "node_modules" / "homebridge-util-accessory-manager" / "dist" / "accessory.js"
        accessory.parent.mkdir(parents=True)
        calendar.write_text(
            "    calendarName;\n"
            "    calendarUrl;\n"
            "        this.calendarName = calendar.calendarName;\n"
            "        this.calendarUrl = calendar.calendarUrl;\n"
        )
        event.write_text(
            "    eventName;\n"
            "    eventTriggerOnUpdates;\n"
            "                .replace(/\\s+/g, ' ');\n"
            "        this.calendarEventNotifications =\n"
        )
        handler.write_text(
            "this._prepareContext(this.calendarConfig.id, this.calendarConfig.calendarName, this.calendarConfig)\n"
            "this._prepareContext(event.id, event.safeEventName, this.calendarConfig, event)\n"
        )
        accessory.write_text(
            "    _setAccessoryInformation(manufacturer, model, serialNumber, version) {\n"
            "        this._accessory.getService(this.$_api.hap.Service.AccessoryInformation)\n"
            "            .setCharacteristic(this.$_api.hap.Characteristic.Manufacturer, manufacturer)\n"
            "    _getService(name, service) {\n"
            "        return (this._accessory.getService(service)\n"
            "            || this._accessory.addService(service, ...[name]))\n"
            "            .setCharacteristic(this.$_api.hap.Characteristic.Name, name);\n"
            "    }\n"
        )
        (root / "package.json").write_text(json.dumps({"name": "homebridge-calendar-scheduler"}) + "\n")
        return calendar, event, handler, accessory

    def run_script(self, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "PATH": f"{NODE.parent}:{os.environ.get('PATH', '')}",
            "SMART_HOME_CALENDAR_PLUGIN_ROOT": str(root),
        }
        return subprocess.run(
            [str(NODE), str(SCRIPT), *args],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_apply_adds_display_alias_support_without_changing_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            calendar, event, handler, accessory = self.make_plugin(Path(tmp))

            payload = json.loads(self.run_script(Path(tmp), "--apply").stdout)

            self.assertTrue(payload["applied"])
            self.assertIn("calendarDisplayName", calendar.read_text())
            self.assertIn("eventDisplayName", event.read_text())
            self.assertIn("calendarConfig.calendarDisplayName", handler.read_text())
            self.assertIn("event.eventDisplayName", handler.read_text())
            self.assertIn("calendarConfig.id", handler.read_text())
            self.assertIn("event.id", handler.read_text())
            self.assertIn("updateDisplayName(name)", accessory.read_text())
            self.assertIn("removeCharacteristic", accessory.read_text())
            self.assertNotIn("ConfiguredName)?.updateValue(name)", accessory.read_text())

            second = json.loads(self.run_script(Path(tmp), "--apply").stdout)
            self.assertTrue(all(status == "already patched" for status in second["accessoryManager"]))


if __name__ == "__main__":
    unittest.main()
