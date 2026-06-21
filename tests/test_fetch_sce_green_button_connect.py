#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "fetch_sce_green_button_connect",
    ROOT / "scripts" / "fetch_sce_green_button_connect.py",
)
assert SPEC and SPEC.loader
fetch_sce = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch_sce)


class FetchSceGreenButtonConnectTest(unittest.TestCase):
    def patch_module(self, **replacements: object) -> None:
        self._restore = getattr(self, "_restore", {})
        for name, replacement in replacements.items():
            if name not in self._restore:
                self._restore[name] = getattr(fetch_sce, name)
            setattr(fetch_sce, name, replacement)

    def tearDown(self) -> None:
        for name, original in getattr(self, "_restore", {}).items():
            setattr(fetch_sce, name, original)

    def test_utilityapi_historical_collection_defaults_to_disabled(self) -> None:
        self.patch_module(load_config=lambda: {"utilityapi_api_token": "token"})

        with mock.patch.dict(os.environ, {}, clear=True):
            config = fetch_sce.configured_utilityapi()

        self.assertFalse(config["auto_historical_collection"])

    def test_green_button_connect_nested_config_is_supported(self) -> None:
        self.patch_module(
            load_config=lambda: {
                "green_button_connect": {
                    "resource_url": "https://sce.example/resource",
                    "access_token": "token",
                }
            }
        )

        with mock.patch.dict(os.environ, {}, clear=True):
            resource_url, access_token = fetch_sce.configured_request()

        self.assertEqual(resource_url, "https://sce.example/resource")
        self.assertEqual(access_token, "token")

    def test_green_button_registration_plan_reports_missing_parts(self) -> None:
        plan = fetch_sce.green_button_registration_plan(
            {
                "green_button_connect": {
                    "redirect_uri": "https://example.test/callback",
                    "client_id": "client",
                }
            }
        )

        self.assertEqual(plan["localCallbackUrl"], "https://example.test/callback")
        self.assertTrue(plan["clientConfigured"])
        self.assertFalse(plan["tokenConfigured"])
        self.assertFalse(plan["resourceConfigured"])

    def test_utilityapi_historical_collection_can_be_enabled_by_config(self) -> None:
        self.patch_module(
            load_config=lambda: {
                "utilityapi_api_token": "token",
                "utilityapi_auto_historical_collection": True,
            }
        )

        with mock.patch.dict(os.environ, {}, clear=True):
            config = fetch_sce.configured_utilityapi()

        self.assertTrue(config["auto_historical_collection"])

    def test_utilityapi_historical_collection_can_be_enabled_by_env(self) -> None:
        self.patch_module(
            load_config=lambda: {
                "utilityapi_api_token": "token",
                "utilityapi_auto_historical_collection": False,
            }
        )

        with mock.patch.dict(os.environ, {"UTILITYAPI_AUTO_HISTORICAL_COLLECTION": "true"}, clear=True):
            config = fetch_sce.configured_utilityapi()

        self.assertTrue(config["auto_historical_collection"])

    def test_stale_download_does_not_trigger_collection_when_disabled(self) -> None:
        triggered = False

        def trigger_collection(*_args: object) -> dict[str, object]:
            nonlocal triggered
            triggered = True
            return {"ok": True}

        self.patch_module(
            fetch_utilityapi_intervals=lambda _config: {
                "path": Path("/tmp/SCE_Usage_UtilityAPI_test.csv"),
                "rowCount": 1,
                "coverageStart": "2026-06-15T00:00:00-07:00",
                "coverageEnd": "2026-06-16T00:00:00-07:00",
                "requestedEnd": "2026-06-18",
                "meters": ["meter-1"],
                "authorizations": [],
            },
            coverage_age_hours=lambda _coverage_end: 48.0,
            trigger_historical_collection=trigger_collection,
        )

        result = fetch_sce.fetch_with_auto_historical_collection(
            {
                "api_token": "token",
                "base_url": "https://utilityapi.example",
                "meter_uids": ["meter-1"],
                "auto_historical_collection": False,
                "stale_hours": 36,
            }
        )

        self.assertFalse(triggered)
        self.assertNotIn("historicalCollection", result)
        self.assertEqual(result["rowCount"], 1)

    def test_utilityapi_payment_required_is_degraded_not_failed(self) -> None:
        statuses: list[dict[str, object]] = []
        self.patch_module(
            configured_request=lambda: (None, None),
            configured_utilityapi=lambda: {"api_token": "token"},
            fetch_with_auto_historical_collection=lambda _config: (_ for _ in ()).throw(
                urllib.error.HTTPError("https://utilityapi.example", 402, "Payment Required", {}, None)
            ),
            write_status=statuses.append,
        )

        rc = fetch_sce.main()

        self.assertEqual(rc, 0)
        self.assertEqual(statuses[-1]["ok"], None)
        self.assertEqual(statuses[-1]["status"], "utilityapi_payment_required")
        self.assertIn("Do not use paid UtilityAPI collection", statuses[-1]["requiredAction"])
        self.assertIn("SCE Green Button", statuses[-1]["requiredAction"])

    def test_other_utilityapi_http_errors_still_fail(self) -> None:
        statuses: list[dict[str, object]] = []
        self.patch_module(
            configured_request=lambda: (None, None),
            configured_utilityapi=lambda: {"api_token": "token"},
            fetch_with_auto_historical_collection=lambda _config: (_ for _ in ()).throw(
                urllib.error.HTTPError("https://utilityapi.example", 500, "Internal Server Error", {}, None)
            ),
            write_status=statuses.append,
        )

        rc = fetch_sce.main()

        self.assertEqual(rc, 1)
        self.assertEqual(statuses[-1]["ok"], False)
        self.assertEqual(statuses[-1]["status"], "utilityapi_http_error")

    def test_historical_collection_payment_required_preserves_fetched_intervals(self) -> None:
        self.patch_module(
            fetch_utilityapi_intervals=lambda _config: {
                "path": Path("/tmp/SCE_Usage_UtilityAPI_test.csv"),
                "rowCount": 1,
                "coverageStart": "2026-06-15T00:00:00-07:00",
                "coverageEnd": "2026-06-16T00:00:00-07:00",
                "requestedEnd": "2026-06-18",
                "meters": ["meter-1"],
                "authorizations": [],
            },
            coverage_age_hours=lambda _coverage_end: 48.0,
            api_post_json=lambda *_args: (_ for _ in ()).throw(
                urllib.error.HTTPError("https://utilityapi.example", 402, "Payment Required", {}, None)
            ),
        )

        result = fetch_sce.fetch_with_auto_historical_collection(
            {
                "api_token": "token",
                "base_url": "https://utilityapi.example",
                "meter_uids": ["meter-1"],
                "auto_historical_collection": True,
                "stale_hours": 36,
            }
        )

        self.assertEqual(result["rowCount"], 1)
        self.assertEqual(result["coverageEnd"], "2026-06-16T00:00:00-07:00")
        self.assertEqual(result["historicalCollection"]["status"], "payment_required")
        self.assertEqual(result["historicalCollection"]["statusCode"], 402)

    def test_main_reports_downloaded_intervals_when_collection_payment_required(self) -> None:
        statuses: list[dict[str, object]] = []
        csv_path = Path("/tmp/SCE_Usage_UtilityAPI_test.csv")
        csv_path.write_text("start,end\n")
        self.patch_module(
            configured_request=lambda: (None, None),
            configured_utilityapi=lambda: {"api_token": "token"},
            fetch_with_auto_historical_collection=lambda _config: {
                "path": csv_path,
                "rowCount": 1,
                "coverageStart": "2026-06-15T00:00:00-07:00",
                "coverageEnd": "2026-06-16T00:00:00-07:00",
                "requestedEnd": "2026-06-18",
                "coverageAgeHours": 48.0,
                "historicalCollection": {
                    "triggered": True,
                    "status": "payment_required",
                    "ok": False,
                    "statusCode": 402,
                    "requiredAction": "Check UtilityAPI billing or collection entitlement, then rerun Refresh SCE.",
                },
            },
            write_status=statuses.append,
        )

        rc = fetch_sce.main()

        self.assertEqual(rc, 0)
        self.assertEqual(statuses[-1]["ok"], True)
        self.assertEqual(statuses[-1]["status"], "utilityapi_downloaded")
        self.assertEqual(statuses[-1]["coverageEnd"], "2026-06-16T00:00:00-07:00")
        self.assertEqual(statuses[-1]["autoHistoricalCollection"]["status"], "payment_required")


if __name__ == "__main__":
    unittest.main()
