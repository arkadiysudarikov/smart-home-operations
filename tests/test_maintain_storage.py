#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("maintain_storage", ROOT / "scripts" / "maintain_storage.py")
assert SPEC and SPEC.loader
maintain_storage = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = maintain_storage
SPEC.loader.exec_module(maintain_storage)


class MaintainStorageTest(unittest.TestCase):
    def test_prune_sce_downloads_removes_old_exports_but_keeps_recent_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            download_dir = Path(tmp)
            old_csv = download_dir / "SCE_Usage_UtilityAPI_20200101T010000-0700.csv"
            old_json = download_dir / "UtilityAPI_intervals_20200101T010000-0700.json"
            recent_csv = download_dir / "SCE_Usage_UtilityAPI_29990101T010000-0700.csv"
            recent_json = download_dir / "UtilityAPI_intervals_29990101T010000-0700.json"
            unrelated = download_dir / "notes.txt"
            for path in (old_csv, old_json, recent_csv, recent_json, unrelated):
                path.write_text("x")

            with mock.patch.object(maintain_storage, "SCE_DOWNLOAD_DIR", download_dir):
                deleted, bytes_deleted = maintain_storage.prune_sce_downloads(days=2, keep_recent_pairs=1)

            self.assertEqual(deleted, 2)
            self.assertEqual(bytes_deleted, 2)
            self.assertFalse(old_csv.exists())
            self.assertFalse(old_json.exists())
            self.assertTrue(recent_csv.exists())
            self.assertTrue(recent_json.exists())
            self.assertTrue(unrelated.exists())


if __name__ == "__main__":
    unittest.main()
