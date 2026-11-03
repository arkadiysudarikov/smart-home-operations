import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path
s = importlib.util.spec_from_file_location('guard', Path(__file__).resolve().parents[1] / 'scripts/guard_unifi_storage.py')
g = importlib.util.module_from_spec(s)
s.loader.exec_module(g)

class GuardTests(unittest.TestCase):
    def test_growth_and_low_space_thresholds(self):
        self.assertFalse(g.needs_checkpoint(0, 0))
        self.assertFalse(g.needs_checkpoint(100 * 1024**2, g.GIB))
        self.assertTrue(g.needs_checkpoint(200 * 1024**2, g.GIB))
        self.assertTrue(g.needs_checkpoint(g.GIB, 20 * g.GIB))

    def test_checkpoint_preserves_committed_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / 'test.sqlite'
            db = sqlite3.connect(p)
            db.execute('pragma journal_mode=wal')
            db.execute('pragma wal_autocheckpoint=0')
            db.execute('create table evidence(value text)')
            db.execute('insert into evidence values (?)', ('preserve me',))
            db.commit()
            self.assertGreater(Path(str(p)+'-wal').stat().st_size, 0)
            self.assertEqual(g.checkpoint(p)[0], 0)
            self.assertEqual(Path(str(p)+'-wal').stat().st_size, 0)
            self.assertEqual(db.execute('select value from evidence').fetchone()[0], 'preserve me')
            db.close()
