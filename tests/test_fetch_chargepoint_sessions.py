#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("fetch_chargepoint_sessions", ROOT / "scripts" / "fetch_chargepoint_sessions.py")
assert SPEC and SPEC.loader
fetch_chargepoint_sessions = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fetch_chargepoint_sessions
SPEC.loader.exec_module(fetch_chargepoint_sessions)


class FetchChargePointSessionsTest(unittest.TestCase):
    def test_fresh_enough_skip_is_successful_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status_path = root / "latest_chargepoint_refresh.json"
            source_path = root / "chargepoint_sessions.json"
            source_path.write_text(json.dumps({"capturedAt": "2026-06-15T12:00:00-07:00"}) + "\n")

            with (
                mock.patch.object(fetch_chargepoint_sessions, "STATUS_PATH", status_path),
                mock.patch.object(fetch_chargepoint_sessions, "SOURCE_PATH", source_path),
                mock.patch.object(fetch_chargepoint_sessions, "load_config", return_value={"mode": "driver_portal"}),
                mock.patch.object(
                    fetch_chargepoint_sessions,
                    "fetch_driver_portal",
                    return_value={
                        "mode": "driver_portal",
                        "sessions": [],
                        "skipped": True,
                        "status": "fresh_enough",
                        "detail": "recent source is still fresh",
                    },
                ),
            ):
                self.assertEqual(fetch_chargepoint_sessions.main(), 0)

            status = json.loads(status_path.read_text())
            self.assertTrue(status["ok"])
            self.assertTrue(status["skipped"])
            self.assertEqual(status["status"], "fresh_enough")


if __name__ == "__main__":
    unittest.main()
