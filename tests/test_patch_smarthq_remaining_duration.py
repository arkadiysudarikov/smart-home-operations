#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NODE = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"

WASHER_ORIGINAL = """            const seconds = Math.round(minutes * 60); // Don't cap, let it show actual time
            this.infoLog(`Time Remaining - Hex: ${r}, Decimal: ${value}, Minutes: ${minutes}, Seconds: ${seconds}`);
            return seconds;"""
OVEN_ORIGINAL = """            const seconds = minutes * 60;
            this.debugLog(`Cook Time Remaining - Hex: ${r}, Minutes: ${minutes}, Seconds: ${seconds}`);
            return seconds;"""
AUTH_ORIGINAL = """        // If we have HTML in the response, try to handle it
        if (res.data && typeof res.data === 'string') {
            code = await asyncHandleOkResponse(res.data);
        }"""
MFA_URL_ORIGINAL = "                        url: `${LOGIN_URL}/account/active/redirect`,"


class PatchSmartHqRemainingDurationTest(unittest.TestCase):
    def make_plugin(self, root: Path) -> tuple[Path, Path, Path]:
        washer = root / "dist/devices/clothesWasher.js"
        oven = root / "dist/devices/oven.js"
        auth = root / "dist/getAccessToken.js"
        washer.parent.mkdir(parents=True)
        oven.write_text(OVEN_ORIGINAL + "\n")
        washer.write_text(WASHER_ORIGINAL + "\n")
        auth.write_text(MFA_URL_ORIGINAL + "\n" + AUTH_ORIGINAL + "\n")
        (root / "package.json").write_text(json.dumps({"name": "homebridge-smarthq"}) + "\n")
        return washer, oven, auth

    def run_script(self, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "SMART_HOME_SMARTHQ_PLUGIN_ROOT": str(root)}
        return subprocess.run(
            [str(NODE), str(ROOT / "scripts" / "patch_smarthq_remaining_duration.js"), *args],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
            env=env,
        )

    def test_dry_run_does_not_modify_plugin_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            washer, oven, auth = self.make_plugin(Path(tmp))

            result = self.run_script(Path(tmp))

            self.assertEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["applied"])
            self.assertEqual(payload["washer"], "would patch")
            self.assertEqual(payload["oven"], "would patch")
            self.assertEqual(payload["auth"], "would patch")
            self.assertEqual(payload["authMfaUrl"], "would patch")
            self.assertEqual(washer.read_text(), WASHER_ORIGINAL + "\n")
            self.assertEqual(oven.read_text(), OVEN_ORIGINAL + "\n")
            self.assertEqual(auth.read_text(), MFA_URL_ORIGINAL + "\n" + AUTH_ORIGINAL + "\n")

    def test_apply_patches_plugin_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            washer, oven, auth = self.make_plugin(Path(tmp))

            result = self.run_script(Path(tmp), "--apply")

            self.assertEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["applied"])
            self.assertEqual(payload["washer"], "patched")
            self.assertEqual(payload["oven"], "patched")
            self.assertEqual(payload["auth"], "patched")
            self.assertEqual(payload["authMfaUrl"], "patched")
            self.assertIn("HomeKit Seconds", washer.read_text())
            self.assertIn("HomeKit Seconds", oven.read_text())
            self.assertIn("intermediateResp", auth.read_text())
            self.assertIn("new URL('/account/active/redirect'", auth.read_text())


if __name__ == "__main__":
    unittest.main()
