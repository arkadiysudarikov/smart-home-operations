#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NODE = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"


class AlarmUiScriptsTest(unittest.TestCase):
    def run_script(self, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(NODE), str(ROOT / "scripts" / script), *args],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

    def test_sensor_saver_apply_refuses_from_source_checkout(self) -> None:
        result = self.run_script("apply_alarm_sensor_saver_ui.js")

        self.assertEqual(result.returncode, 1)
        self.assertIn("outside the runtime root", result.stderr)

    def test_sensor_saver_probe_refuses_from_source_checkout(self) -> None:
        result = self.run_script("probe_alarm_sensor_saver_ui.js")

        self.assertEqual(result.returncode, 1)
        self.assertIn("outside the runtime root", result.stderr)

    def test_energy_settings_probe_refuses_from_source_checkout(self) -> None:
        result = self.run_script("probe_alarm_energy_settings_ui.js")

        self.assertEqual(result.returncode, 1)
        self.assertIn("outside the runtime root", result.stderr)

    def test_ui_probe_outputs_are_redacted(self) -> None:
        scripts = [
            "apply_alarm_sensor_saver_ui.js",
            "probe_alarm_sensor_saver_ui.js",
            "probe_alarm_energy_settings_ui.js",
        ]

        for script in scripts:
            source = (ROOT / "scripts" / script).read_text()
            self.assertIn("function redactArtifact", source)
            self.assertIn("[redacted-email]", source)
            self.assertIn("[redacted-phone]", source)

    def test_sensor_probe_does_not_persist_raw_html_sample(self) -> None:
        source = (ROOT / "scripts" / "probe_alarm_sensor_saver_ui.js").read_text()

        self.assertNotIn("innerHTML.slice", source)


if __name__ == "__main__":
    unittest.main()
