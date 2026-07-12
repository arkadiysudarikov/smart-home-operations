#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "patch_alarm_alias_names.js"
NODE = Path.home() / ".local" / "node-v24.16.0-darwin-arm64" / "bin" / "node"


class PatchAlarmAliasNamesTest(unittest.TestCase):
    def run_script(self, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "PATH": f"{NODE.parent}:{os.environ.get('PATH', '')}",
            "SMART_HOME_ALARM_PLUGIN_ROOT": str(root),
        }
        return subprocess.run(
            [str(NODE), str(SCRIPT), *args],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_apply_removes_unsupported_configured_name_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            handler = root / "dist" / "handlers" / "BaseHandler.js"
            handler.parent.mkdir(parents=True)
            handler.write_text(
                "            service.getCharacteristic(hap.Characteristic.Name)?.updateValue(alias);\n"
                "            service.getCharacteristic(hap.Characteristic.ConfiguredName)?.updateValue(alias);\n"
            )
            (root / "package.json").write_text(json.dumps({"name": "homebridge-node-alarm-dot-com"}) + "\n")

            payload = json.loads(self.run_script(root, "--apply").stdout)
            content = handler.read_text()

            self.assertEqual(payload["handler"], "patched")
            self.assertIn("removeCharacteristic", content)
            self.assertNotIn("ConfiguredName)?.updateValue(alias)", content)
            second = json.loads(self.run_script(root, "--apply").stdout)
            self.assertEqual(second["handler"], "already patched")


if __name__ == "__main__":
    unittest.main()
