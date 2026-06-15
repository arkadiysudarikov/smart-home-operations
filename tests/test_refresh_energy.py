#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("refresh_energy", ROOT / "scripts" / "refresh_energy.py")
assert SPEC and SPEC.loader
refresh_energy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = refresh_energy
SPEC.loader.exec_module(refresh_energy)


class RefreshEnergyTest(unittest.TestCase):
    def test_recent_status_rejects_explicit_non_true_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            path.write_text(
                json.dumps({"ok": None, "finishedAt": datetime.now(timezone.utc).astimezone().isoformat()}) + "\n"
            )

            self.assertFalse(refresh_energy.is_recent_status(path, 3600, "finishedAt"))

    def test_recent_status_accepts_payload_without_ok_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            path.write_text(json.dumps({"capturedAt": datetime.now(timezone.utc).astimezone().isoformat()}) + "\n")

            self.assertTrue(refresh_energy.is_recent_status(path, 3600, "capturedAt"))


if __name__ == "__main__":
    unittest.main()
