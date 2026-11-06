import json
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import analyze_meter_reconciliation as m
import analyze_combined_energy_monitor as combined
import capture_envoy_direct as envoy

NOW=datetime(2026,11,5,20,tzinfo=timezone.utc)
AT=NOW.isoformat()


class SolarScopeTests(unittest.TestCase):
    def test_signed_storage_adjustment_handles_charging_and_discharging(self):
        self.assertEqual(m.site_load_kw(3,-2),1)
        self.assertEqual(m.site_load_kw(0,2),2)
        self.assertEqual(m.site_load_kw(1,0),1)

    def pair(self, **changes):
        return {'envoyMeasurementAt':AT,'envoyStorageAt':AT,'senseMeasurementAt':AT,
            'envoyProductionKw':2,'envoyStorageKw':-1.9,'senseSolarKw':.12,**changes}

    def test_combined_feed_explains_gap_without_claiming_wiring_verified(self):
        result=m.solar_scope_diagnostic(self.pair(),NOW)
        self.assertEqual(result['status'],'consistent_with_combined_feed')
        self.assertAlmostEqual(result['netGenerationErrorKw'],.02)
        self.assertFalse(result['physicalTopologyVerified'])
        self.assertEqual(m.solar_scope_diagnostic(self.pair(envoyStorageKw=1,senseSolarKw=3.02),NOW)['status'],'consistent_with_combined_feed')

    def test_missing_stale_unmatched_and_invalid_readings_do_not_explain_gap(self):
        for changes in [{'envoyStorageKw':None},{'envoyStorageKw':True},{'senseSolarKw':float('nan')},
                        {'envoyMeasurementAt':(NOW-timedelta(minutes=6)).isoformat()},
                        {'senseMeasurementAt':None},{'envoyStorageAt':(NOW-timedelta(seconds=11)).isoformat()}]:
            with self.subTest(changes=changes):
                self.assertNotEqual(m.solar_scope_diagnostic(self.pair(**changes),NOW)['status'],'consistent_with_combined_feed')
        self.assertEqual(m.solar_scope_diagnostic(self.pair(envoyStorageKw=0),NOW)['status'],'no_scope_match')

    def test_reading_timestamp_not_capture_time_gates_live_warning(self):
        pair={**self.pair(),'envoyStatus':'live','envoyCapturedAt':AT,'senseCapturedAt':AT,
              'envoyMeasurementAt':(NOW-timedelta(hours=1)).isoformat()}
        self.assertIsNone(combined.live_solar_gap(pair,NOW))
        pair['envoyMeasurementAt']=AT
        self.assertAlmostEqual(combined.live_solar_gap(pair,NOW),1.88)

    def test_meter_identity_and_zero_are_preserved_not_inferred_from_list_order(self):
        payload={'status':'live','finishedAt':AT,'probes':[
            {'reachable':True,'productionStatus':401},
            {'productionStatus':200,'production':{'production':[
                {'type':'inverters','wNow':9000},
                {'type':'eim','measurementType':'production','wNow':0,'readingTime':NOW.timestamp()}],
                'consumption':[{'type':'eim','measurementType':'total-consumption','wNow':3000}]},
             'meterInfo':[{'eid':42,'measurementType':'storage','state':'enabled'}],
             'meters':[{'eid':8,'activePower':99000},{'eid':42,'activePower':-1000,'timestamp':NOW.timestamp()}]}]}
        sense={'capturedAt':AT,'sampleAt':AT,'watts':None,'solarWatts':0,
               'devices':[{'id':'solar','watts':99999}]}
        with patch.object(m,'load_json_file',side_effect=[payload,sense]):
            result=m.live_envoy_sense_pairing()
        self.assertEqual(result['envoyProductionKw'],0)
        self.assertEqual(result['envoyStorageKw'],-1)
        self.assertEqual(result['senseSolarKw'],0)
        self.assertIsNone(result['senseLoadKw'])
        self.assertEqual(result['envoySiteLoadKw'],2)

    def test_capture_preserves_root_solar_grid_epoch_and_signed_aux(self):
        code="""
const {realtimeMeasurements:f}=require(process.argv[1]);
console.log(JSON.stringify([f({epoch:1793908800,solar_w:22,grid_w:715,aux:{solar:[10,-32]}},true),f({epoch:0,solar_w:false,grid_w:NaN},true),f({epoch:1793908800,solar_w:500},false)]));
"""
        result=subprocess.run(['node','-e',code,str(ROOT/'scripts/capture_sense_now.js')],capture_output=True,text=True,check=True)
        good,bad,offline=json.loads(result.stdout)
        self.assertEqual(good['solarWatts'],22);self.assertEqual(good['gridWatts'],715)
        self.assertEqual(good['solarChannelWatts'],[10,-32]);self.assertEqual(datetime.fromisoformat(good['sampleAt'].replace('Z','+00:00')),NOW)
        self.assertIsNone(bad['sampleAt']);self.assertIsNone(bad['solarWatts']);self.assertIsNone(bad['gridWatts'])
        self.assertIsNone(offline['solarWatts']);self.assertIsNone(offline['sampleAt'])

    def test_capture_gets_read_only_meter_identity_for_storage(self):
        replies=[(200,{},None),(200,{},None),(200,[],None),
                 (200,[{'eid':42,'measurementType':'storage'}],None)]
        with patch.object(envoy,'request_json',side_effect=replies) as get:
            result=envoy.probe_host('example.local','secret')
        self.assertEqual(result['meterInfo'][0]['measurementType'],'storage')
        self.assertEqual(get.call_args_list[-1].args[0],'https://example.local/ivp/meters')


if __name__=='__main__':unittest.main()
