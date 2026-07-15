#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("action_server", ROOT / "scripts" / "action_server.py")
assert SPEC and SPEC.loader
action_server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(action_server)


class ActionServerTest(unittest.TestCase):
    def test_restart_homebridge_route_writes_structured_audit_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "actions.audit.jsonl"
            self.patch_module(ACTION_AUDIT_PATH=audit_path)
            handler = object.__new__(action_server.Handler)
            handler.path = "/action/restart-homebridge?source=home-tile&reason=manual-recovery"
            handler.command = "POST"
            handler.client_address = ("127.0.0.1", 50123)
            handler.headers = {"User-Agent": "HomeKit-test"}

            with mock.patch.object(
                action_server,
                "restart_homebridge",
                return_value={"ok": True, "scheduled": True, "targetPid": 1234},
            ):
                status, payload = action_server.Handler.route(handler)

            audit = json.loads(audit_path.read_text().strip())

        self.assertEqual(status, 202)
        self.assertTrue(payload["ok"])
        self.assertEqual(audit["action"], "restart-homebridge")
        self.assertEqual(audit["remoteAddress"], "127.0.0.1")
        self.assertEqual(audit["source"], "home-tile")
        self.assertEqual(audit["reason"], "manual-recovery")
        self.assertEqual(audit["targetPid"], 1234)
        self.assertEqual(audit["userAgent"], "HomeKit-test")
        self.assertTrue(audit["recorded"])

    def test_restart_audit_failure_does_not_block_scheduled_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.patch_module(ACTION_AUDIT_PATH=Path(tmp))
            handler = object.__new__(action_server.Handler)
            handler.path = "/action/restart-homebridge"
            handler.command = "POST"
            handler.client_address = ("127.0.0.1", 50123)
            handler.headers = {
                "X-Smart-Home-Source": "homebridge-smart-home-actions",
                "X-Smart-Home-Reason": "homekit-switch:hb-restart",
            }

            with mock.patch.object(
                action_server,
                "restart_homebridge",
                return_value={"ok": True, "scheduled": True, "targetPid": 1234},
            ):
                status, payload = action_server.Handler.route(handler)

        self.assertEqual(status, 202)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["audit"]["recorded"])
        self.assertEqual(payload["audit"]["source"], "homebridge-smart-home-actions")
        self.assertEqual(payload["audit"]["reason"], "homekit-switch:hb-restart")

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

    def test_operational_source_status_accepts_nested_alarm_energy_capture(self) -> None:
        captured = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

        def fake_load_json_file(path: Path) -> dict[str, Any]:
            if path.name == "alarm_energy_readings.json":
                return {}
            if path.name == "latest_alarm_com.json":
                return {"energy": {"capturedAtLocal": captured}}
            return {}

        self.patch_module(load_json_file=fake_load_json_file)

        rows = action_server.operational_source_status()
        alarm = next(row for row in rows if row["source"] == "Alarm.com")

        self.assertEqual(alarm["status"], "fresh")
        self.assertEqual(alarm["detail"], captured)

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

    def test_sce_refresh_command_scans_local_exports(self) -> None:
        command = action_server.sce_refresh_command()

        self.assertEqual(command[:2], ["/bin/zsh", "-lc"])
        self.assertIn("SMART_HOME_SCAN_EXTERNAL_FILES=true", command[2])
        self.assertIn("fetch_sce_green_button_connect.py", command[2])
        self.assertIn("refresh_energy.py --fast", command[2])

    def test_wait_for_energy_refresh_idle_returns_immediately_without_lock_pid(self) -> None:
        self.patch_module(read_refresh_lock_pid=lambda: None)

        result = action_server.wait_for_energy_refresh_idle(timeout_seconds=1, poll_seconds=1)

        self.assertTrue(result["ok"])
        self.assertEqual(result["pid"], None)

    def test_wait_for_energy_refresh_idle_times_out_when_pid_stays_running(self) -> None:
        self.patch_module(read_refresh_lock_pid=lambda: 123, process_is_running=lambda _pid: True)

        result = action_server.wait_for_energy_refresh_idle(timeout_seconds=0, poll_seconds=1)

        self.assertFalse(result["ok"])
        self.assertEqual(result["pid"], 123)

    def test_garage_activity_report_surfaces_recent_events_and_off_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "garage_activity_events.jsonl"
            event_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "timestamp": "2026-06-17T08:00:00-07:00",
                                "type": "activation",
                                "ok": True,
                                "trigger": "Garage Door Contact Opens",
                                "holdUntil": "2026-06-17T08:05:00-07:00",
                            }
                        ),
                        json.dumps(
                            {
                                "timestamp": "2026-06-17T08:05:00-07:00",
                                "type": "expiry",
                                "ok": True,
                                "status": "restored",
                                "restoreResult": {"on": False, "brightness": 0},
                            }
                        ),
                    ]
                )
                + "\n"
            )
            self.patch_module(GARAGE_ACTIVITY_EVENTS_PATH=event_path)

            report = action_server.garage_activity_report({"active": False, "status": "restored"})

            self.assertIn("Garage Door Contact Opens", report["knownTriggers"])
            self.assertEqual(report["recentActivationCount"], 1)
            self.assertEqual(report["lastActivation"]["trigger"], "Garage Door Contact Opens")
            self.assertTrue(report["lightsTurnedOffAfterLastActivity"])

    def test_trigger_garage_light_activity_appends_activation_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            hold_path = data_dir / "garage_light_hold.json"
            event_path = data_dir / "garage_activity_events.jsonl"
            self.patch_module(
                DATA_DIR=data_dir,
                GARAGE_LIGHT_HOLD_STATUS_PATH=hold_path,
                GARAGE_ACTIVITY_EVENTS_PATH=event_path,
                garage_light_status=lambda: {"ok": True, "light": {"on": False, "brightness": 0}},
                set_garage_light_on_100=lambda: {"ok": True, "light": {"on": True, "brightness": 100}},
                schedule_garage_light_hold_check=lambda state=None: None,
            )

            result = action_server.trigger_garage_light_activity(
                trigger="Garage Door Contact Opens",
                source="test",
                remote_addr="127.0.0.1",
            )

            state = json.loads(hold_path.read_text())
            events = [json.loads(line) for line in event_path.read_text().splitlines()]
            self.assertTrue(result["ok"])
            self.assertEqual(state["activationCount"], 1)
            self.assertEqual(state["lastTrigger"], "Garage Door Contact Opens")
            self.assertEqual(events[-1]["type"], "activation")
            self.assertEqual(events[-1]["trigger"], "Garage Door Contact Opens")

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

    def test_read_json_status_recovers_stale_running_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            refresh = Path(tmp) / "latest_energy_refresh.json"
            lock = Path(tmp) / "refresh_energy.lock"
            refresh.write_text(
                json.dumps(
                    {
                        "ok": None,
                        "status": "running",
                        "startedAt": "2026-06-21T10:09:30-07:00",
                        "steps": [],
                    }
                )
                + "\n"
            )
            lock.write_text("999999 2026-06-21T10:09:30-07:00\n")
            self.patch_module(ENERGY_REFRESH_STATUS_PATH=refresh, ENERGY_REFRESH_LOCK_PATH=lock)

            status = action_server.read_json_status(refresh)

            self.assertIsNone(status["ok"])
            self.assertEqual(status["status"], "interrupted")
            self.assertTrue(status["staleRunningRecovered"])
            self.assertEqual(status["staleRefreshPid"], 999999)
            self.assertFalse(lock.exists())
            stored = json.loads(refresh.read_text())
            self.assertEqual(stored["status"], "interrupted")
            self.assertIn("finishedAt", stored)

    def test_read_json_status_finalizes_stale_running_refresh_with_terminal_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            refresh = Path(tmp) / "latest_energy_refresh.json"
            lock = Path(tmp) / "refresh_energy.lock"
            refresh.write_text(
                json.dumps(
                    {
                        "ok": None,
                        "status": "running",
                        "startedAt": "2026-06-22T11:51:00-07:00",
                        "currentStep": None,
                        "steps": [
                            {
                                "name": "fetch_sce",
                                "ok": True,
                                "optional": False,
                                "finishedAt": "2026-06-22T11:51:10-07:00",
                            },
                            {
                                "name": "analyze_energy_automation",
                                "ok": True,
                                "optional": True,
                                "finishedAt": "2026-06-22T11:52:00-07:00",
                            },
                        ],
                    }
                )
                + "\n"
            )
            lock.write_text("999999 2026-06-22T11:51:00-07:00\n")
            self.patch_module(ENERGY_REFRESH_STATUS_PATH=refresh, ENERGY_REFRESH_LOCK_PATH=lock)

            status = action_server.read_json_status(refresh)

            self.assertTrue(status["ok"])
            self.assertEqual(status["status"], "complete")
            self.assertTrue(status["staleRunningRecovered"])
            self.assertEqual(status["stepSummary"], {"total": 2, "complete": 2, "skipped": 0, "failed": 0})
            self.assertFalse(lock.exists())
            stored = json.loads(refresh.read_text())
            self.assertEqual(stored["status"], "complete")
            self.assertEqual(stored["staleRunningRecoveryReason"], "terminal_steps_recorded")

    def test_read_json_status_does_not_complete_partial_stale_running_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            refresh = Path(tmp) / "latest_energy_refresh.json"
            lock = Path(tmp) / "refresh_energy.lock"
            refresh.write_text(
                json.dumps(
                    {
                        "ok": None,
                        "status": "running",
                        "startedAt": "2026-06-22T11:51:00-07:00",
                        "steps": [
                            {
                                "name": "fetch_sce",
                                "ok": True,
                                "optional": False,
                                "finishedAt": "2026-06-22T11:51:10-07:00",
                            }
                        ],
                    }
                )
                + "\n"
            )
            lock.write_text("999999 2026-06-22T11:51:00-07:00\n")
            self.patch_module(ENERGY_REFRESH_STATUS_PATH=refresh, ENERGY_REFRESH_LOCK_PATH=lock)

            status = action_server.read_json_status(refresh)

            self.assertIsNone(status["ok"])
            self.assertEqual(status["status"], "interrupted")
            stored = json.loads(refresh.read_text())
            self.assertEqual(stored["status"], "interrupted")

    def test_action_status_keeps_optional_refresh_failures_online(self) -> None:
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
            self.assertFalse(status["degraded"])
            self.assertEqual(status["status"], "ok")
            self.assertEqual(status["degradedActions"], [])
            self.assertEqual(status["actions"]["refreshEnergy"]["optionalFailures"], ["capture_sense_now"])

    def test_action_status_supersedes_overlapped_reconcile_when_refresh_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            reconcile = data_dir / "latest_energy_reconcile.json"
            refresh = data_dir / "latest_energy_refresh.json"
            reconcile.write_text(
                json.dumps(
                    {
                        "ok": False,
                        "status": None,
                        "finishedAt": "2026-06-21T09:26:57-07:00",
                        "stdout": "refresh_energy already running; skipping overlapping launch\n",
                    }
                )
                + "\n"
            )
            refresh.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "status": "complete",
                        "finishedAt": "2026-06-21T09:51:59-07:00",
                        "requiredFailures": [],
                    }
                )
                + "\n"
            )
            self.patch_module(ACTION_STATUS_PATHS={"refreshEnergy": refresh, "reconcileEnergy": reconcile})

            status = action_server.action_status()

            self.assertTrue(status["ok"])
            self.assertEqual(status["status"], "ok")
            self.assertEqual(status["failedActions"], [])
            self.assertEqual(status["actions"]["reconcileEnergy"]["status"], "superseded")
            self.assertEqual(status["actions"]["reconcileEnergy"]["supersededBy"], "refreshEnergy")

    def test_action_status_supersedes_overlapped_reconcile_when_refresh_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            reconcile = data_dir / "latest_energy_reconcile.json"
            refresh = data_dir / "latest_energy_refresh.json"
            reconcile.write_text(
                json.dumps(
                    {
                        "ok": False,
                        "finishedAt": "2026-06-21T09:26:57-07:00",
                    }
                )
                + "\n"
            )
            refresh.write_text(
                json.dumps(
                    {
                        "ok": None,
                        "status": "running",
                        "startedAt": "2026-06-21T09:58:52-07:00",
                    }
                )
                + "\n"
            )
            self.patch_module(ACTION_STATUS_PATHS={"refreshEnergy": refresh, "reconcileEnergy": reconcile})

            status = action_server.action_status()

            self.assertTrue(status["ok"])
            self.assertEqual(status["status"], "ok")
            self.assertEqual(status["failedActions"], [])
            self.assertEqual(status["actions"]["reconcileEnergy"]["status"], "superseded")

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

    def test_action_status_keeps_blocked_unifi_recovery_online(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recovery = Path(tmp) / "latest_unifi_occupancy_recovery.json"
            recovery.write_text(
                json.dumps(
                    {
                        "ok": False,
                        "checkedAt": "2026-06-21T10:37:42-07:00",
                        "action": "none",
                        "classification": "api",
                        "reason": "UniFi API is not healthy; not restarting child bridge",
                    }
                )
                + "\n"
            )
            self.patch_module(ACTION_STATUS_PATHS={"unifiOccupancyRecovery": recovery})

            status = action_server.action_status()

            self.assertTrue(status["ok"])
            self.assertEqual(status["status"], "ok")
            self.assertEqual(status["failedActions"], [])
            self.assertEqual(status["degradedActions"], [])
            recovery_status = status["actions"]["unifiOccupancyRecovery"]
            self.assertEqual(recovery_status["status"], "blocked")
            self.assertEqual(recovery_status["blockedBy"], "unifi_api")
            self.assertEqual(recovery_status["classification"], "api")
            self.assertEqual(recovery_status["action"], "none")

    def test_alarm_refresh_accepts_complete_cache_repair_when_captures_time_out(self) -> None:
        statuses: list[dict[str, Any]] = []

        class FakeLock:
            def release(self) -> None:
                self.released = True

        fake_lock = FakeLock()

        def fake_run(command: list[str], timeout: int = 45) -> dict[str, Any]:
            if command == ["repair"]:
                return {
                    "ok": True,
                    "returncode": 0,
                    "stdout": json.dumps({"ok": True, "staleCount": 1, "changedCount": 1, "repairs": [{}], "skipped": []}),
                    "stderr": "",
                }
            return {"ok": False, "returncode": None, "stdout": "", "stderr": "timed out"}

        stale_counts = iter([1, 1, 1])
        pids = iter([100, 200])

        self.patch_module(
            load_config=lambda: {"actions": {"alarm_child_bridge_port": 52230}},
            alarm_cache_stale_count=lambda: next(stale_counts),
            listening_pid=lambda _port: next(pids),
            terminate=lambda _pid: {"ok": True},
            wait_for_alarm_child_bridge=lambda _port, _pid: {"ok": True, "pid": 200},
            alarm_cache_refresh_command=lambda: ["capture"],
            alarm_cache_repair_command=lambda: ["repair"],
            run=fake_run,
            write_alarm_cache_refresh_status=lambda payload: statuses.append(payload),
            ALARM_CACHE_REFRESH_LOCK=fake_lock,
        )

        action_server.run_alarm_cache_refresh_background("2026-06-21T10:14:03-07:00")

        self.assertTrue(statuses[-1]["ok"])
        self.assertEqual(statuses[-1]["staleAfter"], 0)
        self.assertFalse(statuses[-1]["captureVerified"])
        self.assertTrue(statuses[-1]["repairVerified"])
        self.assertTrue(fake_lock.released)

    def test_action_status_supersedes_failed_alarm_refresh_when_current_cache_is_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            alarm_refresh = Path(tmp) / "latest_alarm_cache_refresh.json"
            alarm_refresh.write_text(
                json.dumps(
                    {
                        "ok": False,
                        "finishedAt": "2026-06-21T10:29:53-07:00",
                        "staleAfter": 1,
                    }
                )
                + "\n"
            )
            self.patch_module(
                ACTION_STATUS_PATHS={"alarmRefresh": alarm_refresh},
                current_alarm_cache_stale_count=lambda: 0,
            )

            status = action_server.action_status()

            self.assertTrue(status["ok"])
            self.assertEqual(status["status"], "ok")
            self.assertEqual(status["failedActions"], [])
            self.assertEqual(status["actions"]["alarmRefresh"]["status"], "superseded")
            self.assertEqual(status["actions"]["alarmRefresh"]["supersededBy"], "currentAlarmCacheComparison")
            self.assertEqual(status["actions"]["alarmRefresh"]["currentStaleCount"], 0)

    def test_action_status_supersedes_stale_running_alarm_refresh_when_cache_is_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            alarm_refresh = Path(tmp) / "latest_alarm_cache_refresh.json"
            alarm_refresh.write_text(
                json.dumps(
                    {
                        "ok": None,
                        "status": "running",
                        "startedAt": "2026-06-21T10:00:00-07:00",
                    }
                )
                + "\n"
            )
            self.patch_module(
                ACTION_STATUS_PATHS={"alarmRefresh": alarm_refresh},
                current_alarm_cache_stale_count=lambda: 0,
                source_age_hours=lambda _value: 1.0,
            )

            status = action_server.action_status()

            self.assertTrue(status["ok"])
            self.assertEqual(status["actions"]["alarmRefresh"]["status"], "superseded")
            self.assertEqual(status["actions"]["alarmRefresh"]["supersededBy"], "currentAlarmCacheComparison")

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

    def test_gate_test_background_preserves_finished_producer_status(self) -> None:
        class FakeLock:
            def release(self) -> None:
                self.released = True

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            status_path = data_dir / "latest_alarm_gate_test.json"
            report_dir = data_dir / "reports"
            report_dir.mkdir()
            status_path.write_text(
                json.dumps(
                    {
                        "ok": False,
                        "status": "timeout",
                        "startedAt": "2026-06-15T17:00:00-07:00",
                        "finishedAt": "2026-06-15T17:10:00-07:00",
                    }
                )
                + "\n"
            )
            lock = FakeLock()
            self.patch_module(
                GATE_TEST_STATUS_PATH=status_path,
                REPORT_DIR=report_dir,
                GATE_TEST_LOCK=lock,
                run=lambda command, timeout: {"ok": False, "returncode": 2, "stdout": "status path", "stderr": ""},
            )

            action_server.run_gate_test_background("2026-06-15T17:00:00-07:00")

            payload = json.loads(status_path.read_text())
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["status"], "timeout")
            self.assertEqual(payload["finishedAt"], "2026-06-15T17:10:00-07:00")
            self.assertEqual(payload["returncode"], 2)
            self.assertTrue(lock.released)

    def test_gate_test_background_marks_unfinished_producer_as_failed(self) -> None:
        class FakeLock:
            def release(self) -> None:
                self.released = True

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            status_path = data_dir / "latest_alarm_gate_test.json"
            report_dir = data_dir / "reports"
            report_dir.mkdir()
            status_path.write_text(
                json.dumps({"ok": None, "status": "running", "startedAt": "2026-06-15T17:00:00-07:00"}) + "\n"
            )
            lock = FakeLock()
            self.patch_module(
                GATE_TEST_STATUS_PATH=status_path,
                REPORT_DIR=report_dir,
                GATE_TEST_LOCK=lock,
                run=lambda command, timeout: {"ok": False, "returncode": None, "stdout": "", "stderr": "timed out"},
            )

            action_server.run_gate_test_background("2026-06-15T17:00:00-07:00")

            payload = json.loads(status_path.read_text())
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["status"], "failed")
            self.assertIsNotNone(payload["finishedAt"])
            self.assertEqual(payload["stderr"], "timed out")
            self.assertTrue(lock.released)

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

    def test_main_starts_separate_read_only_dashboard_listener(self) -> None:
        class FakeServer:
            instances = []

            def __init__(self, address: tuple[str, int], handler: type) -> None:
                self.address = address
                self.handler = handler
                self.shutdown_called = False
                self.close_called = False
                self.instances.append(self)

            def serve_forever(self) -> None:
                return None

            def shutdown(self) -> None:
                self.shutdown_called = True

            def server_close(self) -> None:
                self.close_called = True

        class FakeThread:
            instances = []

            def __init__(self, *, target, name: str, daemon: bool) -> None:
                self.target = target
                self.name = name
                self.daemon = daemon
                self.started = False
                self.instances.append(self)

            def start(self) -> None:
                self.started = True

        self.patch_module(ROOT=Path("/repo"), RUNTIME_ROOT=Path("/runtime"))
        with (
            mock.patch.object(
                action_server,
                "load_config",
                return_value={
                    "actions": {
                        "bind_host": "127.0.0.1",
                        "port": 18765,
                        "dashboard_enabled": True,
                        "dashboard_bind_host": "0.0.0.0",
                        "dashboard_port": 18766,
                    }
                },
            ),
            mock.patch.object(action_server, "ThreadingHTTPServer", FakeServer),
            mock.patch.object(action_server.threading, "Thread", FakeThread),
            mock.patch.object(action_server, "schedule_garage_light_hold_check"),
            mock.patch.object(action_server.os, "chdir"),
            mock.patch.object(sys, "argv", ["action_server.py", "--force-outside-runtime"]),
        ):
            self.assertEqual(action_server.main(), 0)

        self.assertEqual(FakeServer.instances[0].address, ("127.0.0.1", 18765))
        self.assertIs(FakeServer.instances[0].handler, action_server.Handler)
        self.assertEqual(FakeServer.instances[1].address, ("0.0.0.0", 18766))
        self.assertIs(FakeServer.instances[1].handler, action_server.ReadOnlyDisplayHandler)
        self.assertTrue(FakeThread.instances[0].started)
        self.assertTrue(FakeServer.instances[1].shutdown_called)
        self.assertTrue(FakeServer.instances[1].close_called)

    def test_screen_override_is_private_and_persistent_until_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "display_awake_override.json"
            self.patch_module(DISPLAY_AWAKE_OVERRIDE_PATH=path, DATA_DIR=Path(tmp))

            enabled = action_server.set_screens_override(True)
            enabled_payload = json.loads(path.read_text())
            disabled = action_server.set_screens_override(False)
            disabled_payload = json.loads(path.read_text())
            mode = os.stat(path).st_mode & 0o777

        self.assertTrue(enabled["enabled"])
        self.assertTrue(enabled_payload["enabled"])
        self.assertFalse(disabled["enabled"])
        self.assertFalse(disabled_payload["enabled"])
        self.assertEqual(mode, 0o600)

    def test_action_status_includes_sanitized_display_detail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status_path = Path(tmp) / "latest_display_awake.json"
            status_path.write_text(json.dumps({"ok": True, "mode": "shadow", "targets": {"office": {"wouldHold": True}}}))
            self.patch_module(
                ACTION_STATUS_PATHS={"displayAwake": status_path},
                DISPLAY_AWAKE_STATUS_PATH=status_path,
            )

            status = action_server.action_status()

        self.assertEqual(status["actions"]["displayAwake"]["detail"]["mode"], "shadow")
        self.assertTrue(status["actions"]["displayAwake"]["detail"]["targets"]["office"]["wouldHold"])

    def test_display_observability_reports_healthy_launchd_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            generated = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            status_path = root / "latest_display_awake.json"
            summary_path = root / "latest_display_awake_summary.json"
            events_path = root / "display_awake_events.jsonl"
            status_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "status": "shadow",
                        "mode": "shadow",
                        "generatedAt": generated,
                        "health": {"status": "healthy", "setupRequired": [], "degradedReasons": []},
                        "unifi": {"ok": True},
                        "mappingConfigured": True,
                        "enrollment": {"watch": True, "iphone": True},
                        "presence": {
                            "confirmedRoom": "office",
                            "source": "watch",
                            "devices": {
                                "watch": {"fresh": True, "ageSeconds": 12},
                                "iphone": {"fresh": True, "ageSeconds": 25},
                            },
                        },
                        "targets": {
                            "m2-office-mini": {
                                "eligible": True,
                                "wouldHold": True,
                                "reasons": ["presence_room"],
                                "probe": {"reachable": True, "idleSeconds": 42},
                            }
                        },
                    }
                )
            )
            summary_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "eventCount": 2,
                        "targets": {"m2-office-mini": {"predictedHoldSeconds": 3600}},
                    }
                )
            )
            events_path.write_text(json.dumps({"timestamp": generated, "presence": {"confirmedRoom": "office"}}) + "\n")
            self.patch_module(
                DISPLAY_AWAKE_STATUS_PATH=status_path,
                DISPLAY_AWAKE_SUMMARY_PATH=summary_path,
                DISPLAY_AWAKE_EVENTS_PATH=events_path,
            )
            with (
                mock.patch.object(action_server, "load_config", return_value={"display_awake": {"poll_seconds": 30}}),
                mock.patch.object(
                    action_server,
                    "run",
                    return_value={
                        "ok": True,
                        "stdout": "state = running\npid = 123\nruns = 4\nlast exit code = 0\n",
                        "stderr": "",
                    },
                ),
            ):
                result = action_server.display_awake_observability()
                page = action_server.render_display_page().decode()
                read_only_page = action_server.render_display_page(read_only=True).decode()

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "shadow")
        self.assertTrue(result["unifi"]["ok"])
        self.assertEqual(result["pollSeconds"], 30)
        self.assertEqual(result["launchd"]["pid"], 123)
        self.assertEqual(result["summary"]["eventCount"], 2)
        self.assertEqual(len(result["recentEvents"]), 1)
        self.assertIn("Display Awake", page)
        self.assertIn("M2 Office Mini", page)
        self.assertIn("Presence matches room", page)
        self.assertIn("Shadow mode records these decisions", page)
        self.assertIn("Screens Awake", page)
        self.assertNotIn("Screens Awake 1h", page)
        self.assertNotIn("setInterval", page)
        self.assertIn("office", page)
        self.assertIn("Read-only phone view", read_only_page)
        self.assertNotIn("data-action", read_only_page)
        self.assertNotIn("/energy", read_only_page)

    def test_read_only_display_handler_rejects_actions_and_other_routes(self) -> None:
        class FakeHandler:
            path = "/action/screens-awake"

            def send_json(self, status: int, payload: dict) -> None:
                self.status = status
                self.payload = payload

            def send_html(self, status: int, body: bytes) -> None:
                self.status = status
                self.body = body

        handler = FakeHandler()
        action_server.ReadOnlyDisplayHandler.do_GET(handler)
        self.assertEqual(handler.status, 404)

        action_server.ReadOnlyDisplayHandler.do_POST(handler)
        self.assertEqual(handler.status, 405)
        self.assertIn("actions are disabled", handler.payload["error"])

        handler.path = "/displays"
        with mock.patch.object(action_server, "render_display_page", return_value=b"read only") as render:
            action_server.ReadOnlyDisplayHandler.do_GET(handler)
        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.body, b"read only")
        render.assert_called_once_with(read_only=True)

    def test_display_duration_uses_compact_human_units(self) -> None:
        self.assertEqual(action_server.display_duration(None), "—")
        self.assertEqual(action_server.display_duration(42), "42s")
        self.assertEqual(action_server.display_duration(125), "2m 05s")
        self.assertEqual(action_server.display_duration(7320), "2h 02m")

    def test_display_observability_marks_stale_status_unhealthy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status_path = Path(tmp) / "latest_display_awake.json"
            status_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "generatedAt": "2026-01-01T00:00:00-08:00",
                        "unifi": {"ok": True},
                        "health": {"status": "healthy"},
                    }
                )
            )
            self.patch_module(
                DISPLAY_AWAKE_STATUS_PATH=status_path,
                DISPLAY_AWAKE_SUMMARY_PATH=Path(tmp) / "summary.json",
                DISPLAY_AWAKE_EVENTS_PATH=Path(tmp) / "events.jsonl",
            )
            with (
                mock.patch.object(action_server, "load_config", return_value={"display_awake": {"poll_seconds": 30}}),
                mock.patch.object(action_server, "display_launchd_status", return_value={"loaded": True}),
            ):
                result = action_server.display_awake_observability()

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "stale")

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

    def test_send_json_ignores_disconnect_during_headers(self) -> None:
        class FakeHandler:
            def send_response(self, status: int) -> None:
                self.status = status

            def send_header(self, name: str, value: str) -> None:
                return None

            def end_headers(self) -> None:
                raise BrokenPipeError()

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

    def test_send_html_ignores_disconnect_during_headers(self) -> None:
        class FakeHandler:
            def send_response(self, status: int) -> None:
                self.status = status

            def send_header(self, name: str, value: str) -> None:
                return None

            def end_headers(self) -> None:
                raise ConnectionResetError()

        handler = FakeHandler()

        action_server.Handler.send_html(handler, 200, b"ok")

        self.assertEqual(handler.status, 200)


if __name__ == "__main__":
    unittest.main()
