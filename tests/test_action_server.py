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
from unittest import mock


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

    def test_read_json_status_preserves_refresh_failure_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "latest_energy_refresh.json"
            path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "status": "complete",
                        "stepSummary": {"total": 3, "complete": 2, "skipped": 1, "failed": 1},
                        "requiredFailures": [],
                        "optionalFailures": ["capture_sense_now"],
                    }
                )
                + "\n"
            )

            status = action_server.read_json_status(path)

            self.assertEqual(status["stepSummary"]["failed"], 1)
            self.assertEqual(status["optionalFailures"], ["capture_sense_now"])

    def test_action_status_marks_optional_refresh_failures_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            refresh = data_dir / "latest_energy_refresh.json"
            refresh.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "status": "complete",
                        "stepSummary": {"total": 2, "complete": 1, "skipped": 0, "failed": 1},
                        "optionalFailures": ["capture_sense_now"],
                    }
                )
                + "\n"
            )
            self.patch_module(ACTION_STATUS_PATHS={"refreshEnergy": refresh})

            status = action_server.action_status()

            self.assertTrue(status["ok"])
            self.assertTrue(status["degraded"])
            self.assertEqual(status["status"], "degraded")
            self.assertEqual(status["degradedActions"], ["refreshEnergy"])

    def test_action_status_marks_failed_child_not_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            refresh = data_dir / "latest_energy_refresh.json"
            refresh.write_text(json.dumps({"ok": False, "status": "failed"}) + "\n")
            self.patch_module(ACTION_STATUS_PATHS={"refreshEnergy": refresh})

            status = action_server.action_status()

            self.assertFalse(status["ok"])
            self.assertEqual(status["status"], "failed")
            self.assertEqual(status["failedActions"], ["refreshEnergy"])

    def test_energy_status_marks_optional_refresh_failures_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            config_dir = root / "config"
            data_dir.mkdir()
            config_dir.mkdir()
            refresh = data_dir / "latest_energy_refresh.json"
            refresh.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "status": "complete",
                        "stepSummary": {"total": 2, "complete": 1, "skipped": 0, "failed": 1},
                        "optionalFailures": ["capture_sense_now"],
                    }
                )
                + "\n"
            )
            self.patch_module(ROOT=root, DATA_DIR=data_dir, ENERGY_REFRESH_STATUS_PATH=refresh, SCE_API_STATUS_PATH=data_dir / "latest_sce_api.json")

            status = action_server.energy_status()

            self.assertTrue(status["ok"])
            self.assertTrue(status["degraded"])
            self.assertEqual(status["status"], "degraded")
            self.assertEqual(status["degradedSources"], ["refresh"])

    def test_main_refuses_to_expose_actions_outside_runtime_root_by_default(self) -> None:
        self.patch_module(ROOT=Path("/repo"), RUNTIME_ROOT=Path("/runtime"))
        stderr = io.StringIO()
        with (
            contextlib.redirect_stderr(stderr),
            mock.patch.object(action_server, "load_config") as load_config,
            mock.patch.object(sys, "argv", ["action_server.py"]),
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
            mock.patch.object(
                action_server,
                "load_config",
                return_value={"actions": {"bind_host": "127.0.0.1", "port": 0}},
            ),
            mock.patch.object(action_server, "ThreadingHTTPServer", FakeServer),
            mock.patch.object(action_server, "schedule_garage_light_hold_check") as schedule_check,
            mock.patch.object(action_server.os, "chdir"),
            mock.patch.object(sys, "argv", ["action_server.py", "--force-outside-runtime"]),
        ):
            self.assertEqual(action_server.main(), 0)

        schedule_check.assert_called_once()

    def test_send_json_ignores_client_disconnect(self) -> None:
        class BrokenWriter:
            def write(self, body: bytes) -> None:
                raise BrokenPipeError()

        class FakeHandler:
            wfile = BrokenWriter()

            def send_response(self, status: int) -> None:
                self.status = status

            def send_header(self, name: str, value: str) -> None:
                return None

            def end_headers(self) -> None:
                return None

        handler = FakeHandler()

        action_server.Handler.send_json(handler, 200, {"ok": True})

        self.assertEqual(handler.status, 200)

    def test_send_html_ignores_client_disconnect(self) -> None:
        class BrokenWriter:
            def write(self, body: bytes) -> None:
                raise ConnectionResetError()

        class FakeHandler:
            wfile = BrokenWriter()

            def send_response(self, status: int) -> None:
                self.status = status

            def send_header(self, name: str, value: str) -> None:
                return None

            def end_headers(self) -> None:
                return None

        handler = FakeHandler()

        action_server.Handler.send_html(handler, 200, b"ok")

        self.assertEqual(handler.status, 200)


if __name__ == "__main__":
    unittest.main()
