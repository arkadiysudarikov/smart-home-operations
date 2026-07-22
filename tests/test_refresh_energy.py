#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("refresh_energy", ROOT / "scripts" / "refresh_energy.py")
assert SPEC and SPEC.loader
refresh_energy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = refresh_energy
SPEC.loader.exec_module(refresh_energy)


class RefreshEnergyTest(unittest.TestCase):
    def test_refresh_plan_keeps_post_capture_snapshot_before_observability(self) -> None:
        source = (ROOT / "scripts" / "refresh_energy.py").read_text()

        self.assertEqual(source.count('("snapshot_post_capture", [py, "scripts/smart_home_snapshot.py"'), 2)
        self.assertLess(source.index('"snapshot_post_capture"'), source.index('"analyze_energy_observability"'))

    def test_refresh_plan_captures_direct_smarthq_before_laundry_notifiers(self) -> None:
        source = (ROOT / "scripts" / "refresh_energy.py").read_text()

        self.assertEqual(source.count('"capture_smarthq_laundry"'), 2)
        self.assertEqual(source.count('"recover_smarthq_laundry"'), 2)
        self.assertLess(source.index('"capture_smarthq_laundry"'), source.index('"recover_smarthq_laundry"'))
        self.assertLess(source.index('"capture_smarthq_laundry"'), source.index('"washer_notifier"'))
        self.assertLess(source.index('"recover_smarthq_laundry"'), source.index('"washer_notifier"'))
        self.assertEqual(source.count('"combo_notifier"'), 2)

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

    def test_recent_status_accepts_nested_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            path.write_text(
                json.dumps(
                    {
                        "energy": {
                            "capturedAtLocal": datetime.now(timezone.utc).astimezone().isoformat(),
                        }
                    }
                )
                + "\n"
            )

            self.assertTrue(refresh_energy.is_recent_status(path, 3600, "capturedAtLocal", "energy.capturedAtLocal"))

    def test_recent_sce_api_status_rejects_stale_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original_data_dir = refresh_energy.DATA_DIR
            refresh_energy.DATA_DIR = Path(tmp)
            try:
                path = Path(tmp) / "latest_sce_api.json"
                path.write_text(
                    json.dumps(
                        {
                            "ok": True,
                            "finishedAt": datetime.now(timezone.utc).astimezone().isoformat(),
                            "coverageEnd": (
                                datetime.now(timezone.utc).astimezone() - timedelta(days=8)
                            ).isoformat(),
                        }
                    )
                    + "\n"
                )

                self.assertFalse(refresh_energy.is_fresh_sce_api_status(path))
            finally:
                refresh_energy.DATA_DIR = original_data_dir

    def test_recent_sce_api_status_rejects_coverage_behind_local_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original_data_dir = refresh_energy.DATA_DIR
            refresh_energy.DATA_DIR = Path(tmp)
            try:
                api_end = datetime.now(timezone.utc).astimezone() - timedelta(hours=6)
                local_end = api_end + timedelta(hours=3)
                Path(tmp, "latest_sce_api.json").write_text(
                    json.dumps(
                        {
                            "ok": True,
                            "finishedAt": datetime.now(timezone.utc).astimezone().isoformat(),
                            "coverageEnd": api_end.isoformat(),
                        }
                    )
                    + "\n"
                )
                Path(tmp, "latest_combined_energy_monitor.json").write_text(
                    json.dumps({"sources": {"sce": {"coverageEnd": local_end.isoformat()}}}) + "\n"
                )

                self.assertFalse(refresh_energy.is_fresh_sce_api_status(Path(tmp) / "latest_sce_api.json"))
            finally:
                refresh_energy.DATA_DIR = original_data_dir

    def test_recent_sce_api_status_accepts_current_best_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original_data_dir = refresh_energy.DATA_DIR
            refresh_energy.DATA_DIR = Path(tmp)
            try:
                api_end = datetime.now(timezone.utc).astimezone() - timedelta(hours=6)
                Path(tmp, "latest_sce_api.json").write_text(
                    json.dumps(
                        {
                            "ok": True,
                            "finishedAt": datetime.now(timezone.utc).astimezone().isoformat(),
                            "coverageEnd": api_end.isoformat(),
                        }
                    )
                    + "\n"
                )

                self.assertTrue(refresh_energy.is_fresh_sce_api_status(Path(tmp) / "latest_sce_api.json"))
            finally:
                refresh_energy.DATA_DIR = original_data_dir

    def test_recent_stale_coverage_status_is_retried_after_status_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original_data_dir = refresh_energy.DATA_DIR
            refresh_energy.DATA_DIR = Path(tmp)
            try:
                path = Path(tmp) / "latest_sce_api.json"
                path.write_text(
                    json.dumps(
                        {
                            "ok": None,
                            "status": "utilityapi_coverage_stale",
                            "finishedAt": datetime.now(timezone.utc).astimezone().isoformat(),
                        }
                    )
                    + "\n"
                )

                self.assertTrue(refresh_energy.is_fresh_sce_api_status(path))
            finally:
                refresh_energy.DATA_DIR = original_data_dir

    def test_summarize_steps_counts_optional_failures_without_hiding_them(self) -> None:
        summary = refresh_energy.summarize_steps(
            [
                {"name": "snapshot", "ok": True},
                {"name": "alarm", "ok": False, "optional": True},
                {"name": "chargepoint", "ok": True, "skipped": True, "optional": True},
            ]
        )

        self.assertEqual(summary, {"total": 3, "complete": 2, "skipped": 1, "failed": 1})

    def test_finalize_status_marks_interrupted_with_finished_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original_data_dir = refresh_energy.DATA_DIR
            original_report_dir = refresh_energy.REPORT_DIR
            original_status_path = refresh_energy.STATUS_PATH
            refresh_energy.DATA_DIR = Path(tmp)
            refresh_energy.REPORT_DIR = Path(tmp) / "reports"
            refresh_energy.STATUS_PATH = Path(tmp) / "latest_energy_refresh.json"
            try:
                payload = {"ok": None, "status": "running", "startedAt": "2026-06-21T10:00:00-07:00"}

                status = refresh_energy.finalize_status(payload, [], "fast", status="interrupted")

                self.assertIsNone(status["ok"])
                self.assertEqual(status["status"], "interrupted")
                self.assertEqual(status["mode"], "fast")
                self.assertIn("finishedAt", status)
                self.assertEqual(status["stepSummary"], {"total": 0, "complete": 0, "skipped": 0, "failed": 0})
                stored = json.loads(refresh_energy.STATUS_PATH.read_text())
                self.assertEqual(stored["status"], "interrupted")
            finally:
                refresh_energy.DATA_DIR = original_data_dir
                refresh_energy.REPORT_DIR = original_report_dir
                refresh_energy.STATUS_PATH = original_status_path

    def test_finalize_status_marks_required_failures_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original_data_dir = refresh_energy.DATA_DIR
            original_report_dir = refresh_energy.REPORT_DIR
            original_status_path = refresh_energy.STATUS_PATH
            refresh_energy.DATA_DIR = Path(tmp)
            refresh_energy.REPORT_DIR = Path(tmp) / "reports"
            refresh_energy.STATUS_PATH = Path(tmp) / "latest_energy_refresh.json"
            try:
                status = refresh_energy.finalize_status(
                    {"ok": None, "status": "running"},
                    [{"name": "fetch_sce", "ok": False, "optional": False}],
                    "full",
                )

                self.assertFalse(status["ok"])
                self.assertEqual(status["status"], "failed")
                self.assertEqual(status["requiredFailures"], ["fetch_sce"])
                self.assertEqual(status["optionalFailures"], [])
            finally:
                refresh_energy.DATA_DIR = original_data_dir
                refresh_energy.REPORT_DIR = original_report_dir
                refresh_energy.STATUS_PATH = original_status_path

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
