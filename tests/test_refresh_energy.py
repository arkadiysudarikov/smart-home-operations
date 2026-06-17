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

    def test_summarize_steps_counts_optional_failures_without_hiding_them(self) -> None:
        summary = refresh_energy.summarize_steps(
            [
                {"name": "snapshot", "ok": True},
                {"name": "alarm", "ok": False, "optional": True},
                {"name": "chargepoint", "ok": True, "skipped": True, "optional": True},
            ]
        )

        self.assertEqual(summary, {"total": 3, "complete": 2, "skipped": 1, "failed": 1})

    def test_auto_alarm_cache_refresh_skips_when_cache_is_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original_data_dir = refresh_energy.DATA_DIR
            refresh_energy.DATA_DIR = Path(tmp)
            try:
                (Path(tmp) / "latest_alarm_homebridge_state.json").write_text(json.dumps({"staleCount": 0}) + "\n")

                step = refresh_energy.maybe_auto_refresh_alarm_cache()

                self.assertTrue(step["ok"])
                self.assertTrue(step["skipped"])
                self.assertEqual(step["reason"], "Alarm.com/Homebridge cache is already clean")
            finally:
                refresh_energy.DATA_DIR = original_data_dir

    def test_auto_alarm_cache_refresh_skips_when_recent_refresh_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original_data_dir = refresh_energy.DATA_DIR
            refresh_energy.DATA_DIR = Path(tmp)
            try:
                (Path(tmp) / "latest_alarm_homebridge_state.json").write_text(json.dumps({"staleCount": 2}) + "\n")
                (Path(tmp) / "latest_alarm_cache_refresh.json").write_text(
                    json.dumps({"ok": True, "finishedAt": datetime.now(timezone.utc).astimezone().isoformat()}) + "\n"
                )

                step = refresh_energy.maybe_auto_refresh_alarm_cache()

                self.assertTrue(step["ok"])
                self.assertTrue(step["skipped"])
                self.assertEqual(step["reason"], "Alarm cache refresh is already running or was triggered recently")
            finally:
                refresh_energy.DATA_DIR = original_data_dir

    def test_auto_alarm_cache_refresh_posts_action_when_stale(self) -> None:
        class Response:
            status = 202

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self) -> bytes:
                return b'{"ok": true, "scheduled": true}'

        with tempfile.TemporaryDirectory() as tmp:
            original_data_dir = refresh_energy.DATA_DIR
            original_urlopen = refresh_energy.urllib.request.urlopen
            calls = []

            def fake_urlopen(request: object, timeout: int) -> Response:
                calls.append((request, timeout))
                return Response()

            refresh_energy.DATA_DIR = Path(tmp)
            refresh_energy.urllib.request.urlopen = fake_urlopen
            try:
                (Path(tmp) / "latest_alarm_homebridge_state.json").write_text(json.dumps({"staleCount": 2}) + "\n")

                step = refresh_energy.maybe_auto_refresh_alarm_cache()

                self.assertTrue(step["ok"])
                self.assertEqual(step["returncode"], 202)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0][1], 5)
                self.assertEqual(calls[0][0].full_url, "http://127.0.0.1:18765/action/refresh-alarm-cache")
            finally:
                refresh_energy.DATA_DIR = original_data_dir
                refresh_energy.urllib.request.urlopen = original_urlopen


if __name__ == "__main__":
    unittest.main()
