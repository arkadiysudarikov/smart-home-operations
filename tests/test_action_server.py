#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
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


if __name__ == "__main__":
    unittest.main()
