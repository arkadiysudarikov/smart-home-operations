#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NODE = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"


class AlarmActionScriptsTest(unittest.TestCase):
    def test_panel_command_refuses_outside_runtime_root_before_alarm_login(self) -> None:
        result = subprocess.run(
            [str(NODE), str(ROOT / "scripts" / "set_alarm_panel.js"), "--mode", "off"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertIn("outside the runtime root", payload["error"])

    def test_light_status_refuses_outside_runtime_root_before_alarm_login(self) -> None:
        result = subprocess.run(
            [str(NODE), str(ROOT / "scripts" / "set_alarm_light.js"), "--status"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertIn("outside the runtime root", payload["error"])


if __name__ == "__main__":
    unittest.main()
