#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import unittest
import urllib.error
from pathlib import Path


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
        self.assertIn("UtilityAPI billing", statuses[-1]["requiredAction"])

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


if __name__ == "__main__":
    unittest.main()
