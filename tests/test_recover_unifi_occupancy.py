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
sys.path.insert(0, str(ROOT / 'scripts'))
SPEC = importlib.util.spec_from_file_location("recover_unifi_occupancy", ROOT / "scripts" / "recover_unifi_occupancy.py")
assert SPEC and SPEC.loader
recover_unifi_occupancy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recover_unifi_occupancy)


class RecoverUniFiOccupancyTest(unittest.TestCase):
    def test_keychain_probe_uses_only_reads_and_does_not_export_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'config').mkdir()
            (root/'config/unifi_api_key.json').write_text(json.dumps({'controller':'https://controller.local','service':'test','account':'test'}))
            platform={'unifi':{'controller':'https://controller.local','username':'old','password':'unused','unifios':True}}
            with mock.patch.object(recover_unifi_occupancy,'ROOT',root), mock.patch('display_awake_manager.keychain_secret',return_value='SYNTHETIC-KEY'), mock.patch.object(recover_unifi_occupancy,'request_json',side_effect=[(True,200,{},None),(True,200,[],None)]) as request:
                result=recover_unifi_occupancy.probe_unifi_api(platform)
            self.assertTrue(result['ok']);self.assertEqual(result['authSource'],'keychain-api-key')
            self.assertEqual(request.call_count,2)
            self.assertTrue(all(call.kwargs.get('method','GET')=='GET' for call in request.call_args_list))
            self.assertTrue(all(call.kwargs['extra_headers']=={'X-API-KEY':'SYNTHETIC-KEY'} for call in request.call_args_list))
            self.assertNotIn('SYNTHETIC-KEY',json.dumps(result))

    def test_mismatched_or_unavailable_key_never_falls_back_to_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'config').mkdir();pin=root/'config/unifi_api_key.json'
            platform={'unifi':{'controller':'https://controller.local','username':'old','password':'unused','unifios':True}}
            for text in ['{',json.dumps({'controller':'https://other.local'}),json.dumps({'controller':'https://controller.local','service':'test','account':'test'})]:
                pin.write_text(text)
                with mock.patch.object(recover_unifi_occupancy,'ROOT',root), mock.patch('display_awake_manager.keychain_secret',return_value=None), mock.patch.object(recover_unifi_occupancy,'request_json') as request:
                    self.assertFalse(recover_unifi_occupancy.probe_unifi_api(platform)['ok'])
                    request.assert_not_called()

    def test_live_healthy_key_overrides_historical_auth_warning_without_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);hb=root/'hb.json';hb.write_text(json.dumps({'platforms':[{'platform':'UnifiOccupancy'}]}))
            with mock.patch.object(recover_unifi_occupancy,'running_from_runtime_root',return_value=True), mock.patch.object(recover_unifi_occupancy,'load_config',return_value={}), mock.patch.object(recover_unifi_occupancy,'homebridge_config_path',return_value=hb), mock.patch.object(recover_unifi_occupancy,'latest_snapshot',return_value={'homebridge':{'logs':{'recentWarnings':['[homebridge-unifi-occupancy] HTTP 401','[homebridge-unifi-occupancy] HTTP 504']}}}), mock.patch.object(recover_unifi_occupancy,'probe_unifi_api',return_value={'ok':True,'authOk':True,'apiOk':True,'classification':'healthy'}), mock.patch.object(recover_unifi_occupancy,'DATA_DIR',root), mock.patch.object(recover_unifi_occupancy,'REPORT_DIR',root), mock.patch.object(recover_unifi_occupancy,'STATUS_PATH',root/'status.json'), mock.patch.object(recover_unifi_occupancy,'restart_child_bridge') as restart, mock.patch.object(sys,'argv',['recover_unifi_occupancy.py','--probe-only']):
                self.assertEqual(recover_unifi_occupancy.main(),0)
                result=json.loads((root/'status.json').read_text())
                self.assertTrue(result['ok']);self.assertEqual(result['classification'],'healthy')
                self.assertEqual(result['action'],'none');restart.assert_not_called()

    def test_detects_api_warning_without_treating_sense_401_as_unifi_auth(self) -> None:
        snapshot = {
            "homebridge": {
                "logs": {
                    "recentWarnings": [
                        '[homebridge-unifi-occupancy] ERROR: Failed to load device fingerprints StatusCodeError: 504 - {"error":{"code":504,"message":"Gateway Timeout"}}',
                        "[Sense Energy Meter] Error event on sense: Unexpected server response: 401.",
                    ]
                }
            }
        }

        self.assertTrue(recover_unifi_occupancy.has_unifi_api_warning(snapshot))
        self.assertFalse(recover_unifi_occupancy.has_unifi_auth_warning(snapshot))

    def test_counts_active_unifi_occupancy_from_snapshot(self) -> None:
        snapshot = {
            "homebridge": {
                "logs": {
                    "unifiOccupancy": {
                        "trackedAccessories": 3,
                        "active": ["Level 2 m2-office-mini", "Express iPad"],
                    }
                }
            }
        }

        self.assertEqual(recover_unifi_occupancy.unifi_active_count(snapshot), 2)
        self.assertEqual(recover_unifi_occupancy.unifi_tracked_count(snapshot), 3)

    def test_finds_unifi_platform_config(self) -> None:
        config = {
            "platforms": [
                {"platform": "Alarmdotcom"},
                {"platform": "UnifiOccupancy", "_bridge": {"port": 52746}},
            ]
        }

        self.assertEqual(recover_unifi_occupancy.unifi_platform(config)["_bridge"]["port"], 52746)

    def test_restart_decision_requires_api_warning_by_default(self) -> None:
        self.assertFalse(recover_unifi_occupancy.should_restart_for_stale_occupancy(False, 0, {}))
        self.assertTrue(recover_unifi_occupancy.should_restart_for_stale_occupancy(True, 5, {}))
        self.assertTrue(
            recover_unifi_occupancy.should_restart_for_stale_occupancy(
                False,
                0,
                {"restart_when_no_tracked_accessories": True},
            )
        )

    def test_main_refuses_live_recovery_outside_runtime_root_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status_path = Path(tmp) / "latest_unifi_occupancy_recovery.json"
            with (
                mock.patch.object(recover_unifi_occupancy, "ROOT", Path("/repo")),
                mock.patch.object(recover_unifi_occupancy, "RUNTIME_ROOT", Path("/runtime")),
                mock.patch.object(recover_unifi_occupancy, "STATUS_PATH", status_path),
                mock.patch.object(recover_unifi_occupancy, "DATA_DIR", Path(tmp)),
                mock.patch.object(recover_unifi_occupancy, "load_config") as load_config,
                mock.patch.object(sys, "argv", ["recover_unifi_occupancy.py"]),
            ):
                self.assertEqual(recover_unifi_occupancy.main(), 1)

            payload = json.loads(status_path.read_text())
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["action"], "none")
            self.assertIn("outside the runtime root", payload["error"])
            load_config.assert_not_called()

    def test_force_outside_runtime_allows_disabled_config_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status_path = Path(tmp) / "latest_unifi_occupancy_recovery.json"
            with (
                mock.patch.object(recover_unifi_occupancy, "ROOT", Path("/repo")),
                mock.patch.object(recover_unifi_occupancy, "RUNTIME_ROOT", Path("/runtime")),
                mock.patch.object(recover_unifi_occupancy, "STATUS_PATH", status_path),
                mock.patch.object(recover_unifi_occupancy, "DATA_DIR", Path(tmp)),
                mock.patch.object(
                    recover_unifi_occupancy,
                    "load_config",
                    return_value={"unifi_occupancy_recovery": {"enabled": False}},
                ),
                mock.patch.object(sys, "argv", ["recover_unifi_occupancy.py", "--force-outside-runtime"]),
            ):
                self.assertEqual(recover_unifi_occupancy.main(), 0)

            payload = json.loads(status_path.read_text())
            self.assertTrue(payload["ok"])
            self.assertFalse(payload["enabled"])


if __name__ == "__main__":
    unittest.main()
