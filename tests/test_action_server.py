#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("action_server", ROOT / "scripts" / "action_server.py")
assert SPEC and SPEC.loader
action_server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(action_server)


class ActionServerTest(unittest.TestCase):
    def patch_module(self, **replacements: Any) -> None:
        self._restore = getattr(self, "_restore", {})
        for name, replacement in replacements.items():
            if name not in self._restore:
                self._restore[name] = getattr(action_server, name)
            setattr(action_server, name, replacement)

    def tearDown(self) -> None:
        for name, original in getattr(self, "_restore", {}).items():
            setattr(action_server, name, original)

    def test_chargepoint_fresh_enough_skip_without_false_ok_displays_as_fresh(self) -> None:
        def fake_load_json_file(path: Path) -> dict[str, Any]:
            if path.name == "latest_chargepoint_refresh.json":
                return {"ok": None, "status": "fresh_enough", "mode": "driver_portal"}
            return {}

        self.patch_module(load_json_file=fake_load_json_file)

        rows = action_server.operational_source_status()
        chargepoint = next(row for row in rows if row["source"] == "ChargePoint")

        self.assertEqual(chargepoint["status"], "fresh")
        self.assertEqual(chargepoint["detail"], "driver_portal")

    def test_read_json_status_uses_checked_at_as_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            path.write_text(json.dumps({"ok": True, "checkedAt": "2026-06-15T15:00:00-07:00"}) + "\n")

            status = action_server.read_json_status(path)

            self.assertEqual(status["startedAt"], "2026-06-15T15:00:00-07:00")
            self.assertEqual(status["finishedAt"], "2026-06-15T15:00:00-07:00")

    def test_read_json_status_marks_restored_garage_hold_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "garage_light_hold.json"
            path.write_text(json.dumps({"status": "restored", "finishedAt": "2026-06-15T15:00:00-07:00"}) + "\n")

            status = action_server.read_json_status(path)

            self.assertTrue(status["ok"])

    def test_main_refuses_to_expose_actions_outside_runtime_root_by_default(self) -> None:
        self.patch_module(ROOT=Path("/repo"), RUNTIME_ROOT=Path("/runtime"))
        stderr = io.StringIO()
        with (
            contextlib.redirect_stderr(stderr),
            unittest.mock.patch.object(action_server, "load_config") as load_config,
            unittest.mock.patch.object(sys, "argv", ["action_server.py"]),
        ):
            self.assertEqual(action_server.main(), 1)

        load_config.assert_not_called()

    def test_force_outside_runtime_allows_server_startup_path(self) -> None:
        class FakeServer:
            def __init__(self, address: tuple[str, int], handler: type) -> None:
                self.address = address
                self.handler = handler

            def serve_forever(self) -> None:
                return None

        self.patch_module(ROOT=Path("/repo"), RUNTIME_ROOT=Path("/runtime"))
        with (
            unittest.mock.patch.object(
                action_server,
                "load_config",
                return_value={"actions": {"bind_host": "127.0.0.1", "port": 0}},
            ),
            unittest.mock.patch.object(action_server, "ThreadingHTTPServer", FakeServer),
            unittest.mock.patch.object(action_server, "schedule_garage_light_hold_check") as schedule_check,
            unittest.mock.patch.object(action_server.os, "chdir"),
            unittest.mock.patch.object(sys, "argv", ["action_server.py", "--force-outside-runtime"]),
        ):
            self.assertEqual(action_server.main(), 0)

        schedule_check.assert_called_once()


if __name__ == "__main__":
    unittest.main()
