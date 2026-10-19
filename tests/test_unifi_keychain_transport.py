import hashlib
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import patch_unifi_keychain_transport as p

class TransportTests(unittest.TestCase):
    def test_adapter_contract(self):
        result=subprocess.run(['node',str(Path(__file__).with_name('unifi_keychain_events.cjs'))],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_unknown_source_and_version_fail_closed(self):
        with self.assertRaises(ValueError):p.patched(b'unknown',p.VERSION)
        with self.assertRaises(ValueError):p.patched(b'unknown','99.0.0')

    def test_exact_anchors_idempotence_cache_and_capacity_guards(self):
        data='\n'.join([p.OLD_IMPORT,p.OLD_REMOVE,p.OLD_UPDATE,p.OLD_ENSURE]).encode()
        expected=data.decode()
        for old,new in [(p.OLD_IMPORT,p.NEW_IMPORT),(p.OLD_REMOVE,p.NEW_REMOVE),(p.OLD_UPDATE,p.NEW_UPDATE),(p.OLD_ENSURE,p.NEW_ENSURE)]:expected=expected.replace(old,new)
        expected=expected.encode()
        with patch.object(p,'ORIGINAL_HASH',hashlib.sha256(data).hexdigest()),patch.object(p,'PATCHED_HASH',hashlib.sha256(expected).hexdigest()):
            self.assertEqual(p.patched(data,p.VERSION),expected)
            self.assertEqual(p.patched(expected,p.VERSION),expected)
            with self.assertRaises(ValueError):p.patched(data+b' ',p.VERSION)
        self.assertIn('preservedInvalidAccessories',p.NEW_UPDATE)
        self.assertIn('>= 145',p.NEW_ENSURE)

if __name__=='__main__':unittest.main()
