#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NODE = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"


class UpdateOfficeTahomaIpTest(unittest.TestCase):
    def test_apply_refuses_live_config_update_outside_runtime_root(self) -> None:
        result = subprocess.run(
            [str(NODE), str(ROOT / "scripts" / "update_office_tahoma_ip.js"), "--apply"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stderr)
        self.assertIn("outside the runtime root", payload["error"])


if __name__ == "__main__":
    unittest.main()
