#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("display_awake_manager", ROOT / "scripts" / "display_awake_manager.py")
assert SPEC and SPEC.loader
display_awake = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(display_awake)


WATCH_MAC = "02:00:00:00:00:01"
IPHONE_MAC = "02:00:00:00:00:02"
AP_ONE_MAC = "02:00:00:00:01:01"
AP_TWO_MAC = "02:00:00:00:02:02"


def client(mac: str, kind: str, ap_mac: str, *, last_seen: float = 1_000.0) -> dict:
    watch = kind == "watch"
    return {
        "mac": mac,
        "display_name": "Personal device",
        "model_name": "Apple Watch Ultra" if watch else "Apple iPhone",
        "fingerprint": {"dev_cat": 45 if watch else 44},
        "ap_mac": ap_mac,
        "last_seen": last_seen,
        "status": "online",
    }


class FakeProcess:
    def __init__(self) -> None:
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self) -> None:
        self.terminated = True


class DisplayAwakeManagerTest(unittest.TestCase):
    def test_candidates_are_sanitized_and_tokenized(self) -> None:
        candidates = display_awake.sanitized_candidates(
            [client(WATCH_MAC, "watch", AP_ONE_MAC), client(IPHONE_MAC, "iphone", AP_TWO_MAC)],
            {AP_ONE_MAC: "Level 1", AP_TWO_MAC: "Level 2"},
        )

        serialized = json.dumps(candidates)
        self.assertEqual({item["kind"] for item in candidates}, {"watch", "iphone"})
        self.assertNotIn(WATCH_MAC, serialized)
        self.assertNotIn(IPHONE_MAC, serialized)
        self.assertTrue(all(len(item["candidate"]) == 12 for item in candidates))

    def test_enrollment_writes_private_identifiers_but_returns_only_labels(self) -> None:
        clients = [client(WATCH_MAC, "watch", AP_ONE_MAC), client(IPHONE_MAC, "iphone", AP_TWO_MAC)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "enrollment.json"
            result = display_awake.enroll_devices(
                clients,
                watch_token=display_awake.candidate_token(WATCH_MAC),
                iphone_token=display_awake.candidate_token(IPHONE_MAC),
                path=path,
            )

            self.assertEqual(result["enrolled"], ["iphone", "watch"])
            self.assertNotIn(WATCH_MAC, json.dumps(result))
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(display_awake.load_enrollment(path), {"watch": WATCH_MAC, "iphone": IPHONE_MAC})

    def test_sanitize_removes_identifiers_recursively(self) -> None:
        payload = display_awake.sanitize(
            {"mac": WATCH_MAC, "safe": {"wifiMac": IPHONE_MAC, "room": "office"}, "items": [{"bssid": AP_ONE_MAC}]}
        )

        self.assertEqual(payload, {"safe": {"room": "office"}, "items": [{}]})

    def test_event_state_ignores_continuously_changing_idle_and_age_values(self) -> None:
        first = {
            "mode": "shadow",
            "presence": {"confirmedRoom": "office", "devices": {"watch": {"ageSeconds": 10}}},
            "targets": {
                "office": {
                    "probe": {"reachable": True, "locked": False, "idleSeconds": 10},
                    "wouldHold": True,
                    "leaseActive": False,
                    "reasons": ["presence_room", "recent_activity"],
                    "ineligibleReasons": [],
                }
            },
        }
        second = json.loads(json.dumps(first))
        second["presence"]["devices"]["watch"]["ageSeconds"] = 40
        second["targets"]["office"]["probe"]["idleSeconds"] = 40

        self.assertEqual(display_awake.event_state(first), display_awake.event_state(second))

    def test_observability_summary_accumulates_predicted_and_enforced_time(self) -> None:
        first_status = {
            "presence": {"confirmedRoom": "office", "source": "watch"},
            "targets": {
                "office": {
                    "wouldHold": True,
                    "leaseActive": True,
                    "reasons": ["presence_room"],
                    "ineligibleReasons": [],
                }
            },
        }
        second_status = {
            "presence": {"confirmedRoom": "bar", "source": "iphone"},
            "targets": {
                "office": {
                    "wouldHold": False,
                    "leaseActive": False,
                    "reasons": [],
                    "ineligibleReasons": ["locked"],
                }
            },
        }

        first = display_awake.build_observability_summary(
            {}, first_status, now=1_000, max_sample_gap_seconds=120
        )
        summary = display_awake.build_observability_summary(
            first, second_status, now=1_060, max_sample_gap_seconds=120
        )
        office = summary["targets"]["office"]

        self.assertEqual(office["predictedHoldSeconds"], 60)
        self.assertEqual(office["leaseActiveSeconds"], 60)
        self.assertEqual(office["wouldHoldTransitions"], 1)
        self.assertEqual(office["leaseTransitions"], 1)
        self.assertEqual(office["reasonEventCounts"], {"presence_room": 1})
        self.assertEqual(office["ineligibleEventCounts"], {"locked": 1})
        self.assertEqual(summary["presence"]["roomTransitions"], 1)
        self.assertEqual(summary["presence"]["sourceTransitions"], 1)

    def test_observability_summary_drops_controller_downtime(self) -> None:
        status = {
            "presence": {"confirmedRoom": "office", "source": "watch"},
            "targets": {
                "office": {
                    "wouldHold": True,
                    "leaseActive": False,
                    "reasons": ["presence_room"],
                    "ineligibleReasons": [],
                }
            },
        }
        first = display_awake.build_observability_summary(
            {}, status, now=1_000, max_sample_gap_seconds=120
        )
        restarted = display_awake.build_observability_summary(
            first, status, now=2_000, max_sample_gap_seconds=120
        )

        self.assertEqual(restarted["targets"]["office"]["predictedHoldSeconds"], 0)
        self.assertEqual(restarted["observationSeconds"], 0)
        self.assertEqual(restarted["droppedGapSeconds"], 1_000)

    def test_presence_observations_use_enrollment_and_room_mapping(self) -> None:
        observations = display_awake.presence_observations(
            [client(WATCH_MAC, "watch", AP_ONE_MAC), client(IPHONE_MAC, "iphone", AP_TWO_MAC)],
            {AP_ONE_MAC: "Level 1", AP_TWO_MAC: "Level 2"},
            {"watch": WATCH_MAC, "iphone": IPHONE_MAC},
            {"Level 1": "office", "Level 2": "bar"},
            now=1_030,
            fresh_seconds=90,
        )

        self.assertEqual(observations["watch"]["room"], "office")
        self.assertEqual(observations["iphone"]["room"], "bar")
        self.assertTrue(observations["watch"]["fresh"])

    def test_watch_is_preferred_and_zone_requires_two_polls(self) -> None:
        tracker = display_awake.PresenceTracker(confirmation_polls=2, grace_seconds=600)
        observations = {
            "watch": {"fresh": True, "room": "office"},
            "iphone": {"fresh": True, "room": "bar"},
        }

        first = tracker.update(observations, 1_000)
        second = tracker.update(observations, 1_030)

        self.assertIsNone(first["confirmedRoom"])
        self.assertEqual(second["confirmedRoom"], "office")
        self.assertEqual(second["source"], "watch")

    def test_iphone_fallback_and_disconnect_grace(self) -> None:
        tracker = display_awake.PresenceTracker(
            confirmation_polls=1,
            grace_seconds=600,
            state={"confirmedRoom": "bar", "lastConfirmedAt": 1_000},
        )
        fallback = tracker.update(
            {"watch": {"fresh": False}, "iphone": {"fresh": True, "room": "bar"}},
            1_100,
        )
        grace = tracker.update(
            {"watch": {"fresh": False}, "iphone": {"fresh": False}},
            1_500,
        )
        expired = tracker.update(
            {"watch": {"fresh": False}, "iphone": {"fresh": False}},
            1_701,
        )

        self.assertEqual(fallback["source"], "iphone")
        self.assertEqual(grace["confirmedRoom"], "bar")
        self.assertTrue(grace["graceActive"])
        self.assertIsNone(expired["confirmedRoom"])

    def test_ambiguous_access_point_keeps_confirmed_room_during_grace(self) -> None:
        tracker = display_awake.PresenceTracker(
            confirmation_polls=2,
            grace_seconds=600,
            state={"confirmedRoom": "office", "lastConfirmedAt": 1_000},
        )

        result = tracker.update(
            {"watch": {"fresh": True, "room": None}, "iphone": {"fresh": False}},
            1_300,
        )

        self.assertEqual(result["confirmedRoom"], "office")
        self.assertEqual(result["source"], "watch")

    def test_policy_uses_presence_activity_light_and_override(self) -> None:
        target = {"id": "office", "ac_only": False}
        base_probe = {"reachable": True, "consoleUser": "user", "locked": False, "idleSeconds": 4_000}

        presence = display_awake.evaluate_policy(
            target=target,
            probe=base_probe,
            target_room="office",
            presence_room="office",
            light_on=False,
            manual_override=False,
            activity_hold_seconds=1_800,
            light_activity_hold_seconds=7_200,
        )
        light = display_awake.evaluate_policy(
            target=target,
            probe=base_probe,
            target_room="office",
            presence_room=None,
            light_on=True,
            manual_override=False,
            activity_hold_seconds=1_800,
            light_activity_hold_seconds=7_200,
        )
        override = display_awake.evaluate_policy(
            target=target,
            probe=base_probe,
            target_room="office",
            presence_room=None,
            light_on=False,
            manual_override=True,
            activity_hold_seconds=1_800,
            light_activity_hold_seconds=7_200,
        )

        self.assertIn("presence_room", presence["reasons"])
        self.assertIn("light_plus_activity", light["reasons"])
        self.assertIn("manual_override", override["reasons"])
        self.assertTrue(presence["hold"] and light["hold"] and override["hold"])

    def test_policy_safety_gates_override_all_hold_reasons(self) -> None:
        result = display_awake.evaluate_policy(
            target={"id": "laptop", "ac_only": True, "require_lid_open": True},
            probe={
                "reachable": True,
                "consoleUser": "user",
                "locked": True,
                "onAcPower": False,
                "lidClosed": True,
                "idleSeconds": 0,
            },
            target_room="office",
            presence_room="office",
            light_on=True,
            manual_override=True,
            activity_hold_seconds=1_800,
            light_activity_hold_seconds=7_200,
        )

        self.assertFalse(result["hold"])
        self.assertEqual(set(result["ineligibleReasons"]), {"locked", "battery_power", "lid_closed"})

    def test_parse_probe_output(self) -> None:
        parsed = display_awake.parse_probe_output(
            "consoleUser=user\nidleNs=2500000000\nlocked=true\npower=Now drawing from 'AC Power'\n"
            "lidClosed=false\nwifiMac=02:00:00:00:00:03\nnativeDisplayAssertion=1\n"
        )

        self.assertEqual(parsed["idleSeconds"], 2.5)
        self.assertTrue(parsed["locked"])
        self.assertTrue(parsed["onAcPower"])
        self.assertFalse(parsed["lidClosed"])
        self.assertTrue(parsed["nativeDisplayAssertion"])

    def test_light_state_reads_exact_accessory(self) -> None:
        cached = [
            {
                "displayName": "Bar Light",
                "services": [{"characteristics": [{"constructorName": "On", "value": True}]}],
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "cachedAccessories.test").write_text(json.dumps(cached))
            states = display_awake.read_light_states([{"accessory": "Bar Light"}], Path(tmp))

        self.assertEqual(states, {"Bar Light": True})

    def test_lease_manager_renews_and_stops_only_owned_processes(self) -> None:
        created: list[tuple[list[str], FakeProcess]] = []
        clock = [1_000.0]

        def fake_popen(command, **_kwargs):
            process = FakeProcess()
            created.append((command, process))
            return process

        manager = display_awake.LeaseManager(
            lease_seconds=150,
            refresh_seconds=90,
            popen=fake_popen,
            now=lambda: clock[0],
        )
        target = {"id": "bar", "host": "bar.local"}

        manager.tick(target, True)
        clock[0] += 30
        manager.tick(target, True)
        clock[0] += 61
        manager.tick(target, True)
        manager.tick(target, False)

        self.assertEqual(len(created), 2)
        self.assertIn("/usr/bin/caffeinate", created[0][0])
        self.assertTrue(all(process.terminated for _, process in created))

    def test_shadow_cycle_never_calls_lease_tick(self) -> None:
        config = {
            "default_mode": "shadow",
            "targets": [{"id": "local", "host": "local", "room": "office", "local": True}],
            "lights": [],
        }
        manager = display_awake.DisplayAwakeManager(config)
        manager.leases.tick = mock.Mock(side_effect=AssertionError("shadow mode invoked a lease"))
        manager.leases.stop = mock.Mock()
        written: dict[str, dict] = {}

        with (
            mock.patch.object(display_awake, "read_mode", return_value="shadow"),
            mock.patch.object(display_awake, "load_enrollment", return_value={}),
            mock.patch.object(display_awake, "load_room_mapping", return_value={}),
            mock.patch.object(display_awake, "query_unifi_clients", return_value=([], {})),
            mock.patch.object(
                display_awake,
                "probe_targets",
                return_value={"local": {"reachable": True, "consoleUser": "user", "locked": False, "idleSeconds": 0}},
            ),
            mock.patch.object(display_awake, "read_light_states", return_value={}),
            mock.patch.object(display_awake, "read_override", return_value=False),
            mock.patch.object(display_awake, "read_json", return_value={}),
            mock.patch.object(display_awake, "append_event"),
            mock.patch.object(display_awake, "write_private_json", side_effect=lambda path, payload: written.update({str(path): payload})),
        ):
            status = manager.cycle(now=1_000)

        self.assertEqual(status["mode"], "shadow")
        self.assertTrue(status["targets"]["local"]["wouldHold"])
        self.assertEqual(status["health"]["status"], "setup_required")
        manager.leases.tick.assert_not_called()
        manager.leases.stop.assert_called_once_with("local")

    def test_installer_and_drift_checks_include_launch_agent(self) -> None:
        installer = (ROOT / "scripts" / "install_monitor.sh").read_text()
        snapshot = (ROOT / "scripts" / "smart_home_snapshot.py").read_text()

        self.assertIn("com.arkadiy.smart-home-display-awake.plist", installer)
        self.assertIn("com.arkadiy.smart-home-display-awake.plist", snapshot)
        self.assertIn('"scripts/display_awake_manager.py"', snapshot)


if __name__ == "__main__":
    unittest.main()
