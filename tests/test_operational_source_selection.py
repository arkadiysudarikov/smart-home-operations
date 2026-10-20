import errno
import hashlib
import importlib.util
import io
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
def module(name):
    if name in sys.modules:
        return sys.modules[name]
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/f'{name}.py')
    result=importlib.util.module_from_spec(spec);sys.modules[name]=result;spec.loader.exec_module(result);return result
energy=module('analyze_all_energy_readings')
refresh=module('refresh_energy')
health=module('announcement_health')

class SourceSelectionTest(unittest.TestCase):
    def test_partial_fingerprint_is_discarded_before_transient_retry(self):
        class FailingRead(io.BytesIO):
            def read(self,n=-1):
                if self.tell():raise OSError(errno.EDEADLK,'provider lock')
                return super().read(3)
        raw=b'complete source contents';path=Mock();first=FailingRead(raw)
        path.open.side_effect=[first,io.BytesIO(raw)]
        with patch.object(energy.time,'sleep') as sleep:
            self.assertEqual(energy.file_fingerprint(path),hashlib.sha256(raw).hexdigest())
        self.assertTrue(first.closed);self.assertEqual(sleep.call_count,1)

    def test_persistent_and_nontransient_fingerprint_failures_remain_errors(self):
        for code,count in [(errno.EDEADLK,3),(errno.EACCES,1)]:
            path=Mock();path.open.side_effect=OSError(code,'source unavailable')
            with patch.object(energy.time,'sleep'),self.assertRaises(OSError):energy.file_fingerprint(path)
            self.assertEqual(path.open.call_count,count)

    def test_fast_analysis_stays_with_local_imports(self):
        with patch.object(refresh.subprocess,'run') as run:
            command=refresh.analyzer_cmd('python3')
        self.assertNotIn('--scan-external-files',command);run.assert_not_called()

    def test_explicit_full_analysis_retains_external_discovery(self):
        with patch.object(refresh.subprocess,'run',return_value=subprocess.CompletedProcess([],0,stdout='--scan-external-files',stderr='')):
            self.assertIn('--scan-external-files',refresh.analyzer_cmd('python3',scan_external=True))

    def test_local_source_aliases_inspect_the_running_local_plugin(self):
        now=datetime.now(timezone.utc)
        for host in ['m4-office-mini','m4-office-mini.local','localhost']:
            with patch.object(health.socket,'gethostname',return_value='m4-office-mini'),patch.object(health,'load') as load,patch.object(health,'active_plugin',return_value=Path('/reviewed/plugin')),patch.object(health,'inspect_plugin',return_value={'ok':True,'status':'verified'}) as inspect:
                result=health.configured_plugin({'laundry_host':{'enabled':True,'host':host}},now)
            self.assertTrue(result['ok']);inspect.assert_called_once();load.assert_not_called()

    def test_actual_remote_source_never_falls_back_to_local_plugin(self):
        with patch.object(health.socket,'gethostname',return_value='m4-office-mini'),patch.object(health,'load',return_value={'ok':True,'capture':{'capturedAt':'2020-01-01T00:00:00Z'},'plugin':{'ok':True}}),patch.object(health,'active_plugin') as local:
            result=health.configured_plugin({'laundry_host':{'enabled':True,'host':'another-mac.local'}},datetime.now(timezone.utc))
        self.assertFalse(result['ok']);local.assert_not_called()

    def test_fresh_remote_attestation_is_retained(self):
        now=datetime.now(timezone.utc)
        with patch.object(health.socket,'gethostname',return_value='m4-office-mini'),patch.object(health,'load',return_value={'ok':True,'capture':{'capturedAt':now.isoformat()},'plugin':{'ok':True,'status':'verified'}}),patch.object(health,'active_plugin') as local:
            result=health.configured_plugin({'laundry_host':{'enabled':True,'host':'another-mac.local'}},now)
        self.assertTrue(result['ok']);local.assert_not_called()

if __name__=='__main__':unittest.main()
