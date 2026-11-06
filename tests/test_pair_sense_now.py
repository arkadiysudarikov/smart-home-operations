import sys
import unittest
import tempfile
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import pair_sense_now as pair
import analyze_meter_reconciliation as meter

NOW = datetime(2026,11,6,22,tzinfo=timezone.utc)


class CurrentMeterPairTest(unittest.TestCase):
    def sense(self):
        return {'ok':True,'online':True,'watts':1000,'capturedAt':NOW.isoformat(),
                'sampleAt':NOW.isoformat(),'devices':[]}

    def readings(self):
        return {key:{'capturedAt':NOW.isoformat(),'kw':value} for key,value in
                [('Consumption Total',3),('Consumption Net',1),('Production',2),('Storage',-1)]}

    def test_aligned_measurements_keep_signed_battery_accounting(self):
        result=pair.build_pair(self.sense(),self.readings(),NOW)
        self.assertEqual(result['status'],'matched')
        self.assertEqual(result['envoyNonBatteryLoadKw'],2)
        self.assertEqual(result['envoySolarAfterStorageKw'],1)

    def test_download_time_cannot_refresh_old_or_future_sensor_samples(self):
        for at in [NOW-timedelta(minutes=10), NOW+timedelta(seconds=1)]:
            sense={**self.sense(),'sampleAt':at.isoformat()}
            result=pair.build_pair(sense,self.readings(),NOW)
            self.assertEqual(result['status'],'stale_or_missing_sense')
            self.assertNotIn('senseKw',result)

    def test_misaligned_or_nonfinite_meter_cannot_make_comparison(self):
        for change in [{'capturedAt':(NOW-timedelta(seconds=91)).isoformat()}, {'kw':float('nan')}]:
            readings=self.readings()
            readings['Consumption Total'].update(change)
            result=pair.build_pair(self.sense(),readings,NOW)
            self.assertEqual(result['status'],'partial_or_unmatched')
            self.assertNotIn('envoyMinusSenseKw',result)
            self.assertNotIn('envoyNonBatteryLoadKw',result)

    def test_reconciliation_rejects_legacy_pairing_file_from_july(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'pair.json'
            p.write_text(json.dumps({'senseCapturedAtLocal':'2026-07-22T14:18:13-07:00','senseKw':5}))
            with mock.patch.object(meter,'SENSE_NOW_PAIRING_PATH',p):
                self.assertEqual(meter.load_sense_now_pairing(),{})
