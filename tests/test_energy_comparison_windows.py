import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
def module(name):
    spec=importlib.util.spec_from_file_location(name, ROOT/'scripts'/f'{name}.py')
    result=importlib.util.module_from_spec(spec);sys.modules[name]=result;spec.loader.exec_module(result);return result
cp=module('analyze_chargepoint_pairing')
combined=module('analyze_combined_energy_monitor')
ops=module('analyze_energy_automation_opportunities')

class EnergyWindowTest(unittest.TestCase):
    def test_history_total_cannot_be_divided_by_a_different_seven_day_window(self):
        alarm={'capturedAtLocal':'2026-10-20T12:00:00-07:00','dailyKwh':[{'date':'2026-10-19','meter':'Energy Clamp','kwh':50}], 'periodKwh':[{'period':'7d','meter':'Energy Clamp','kwh':300}]}
        sessions=[{'startAt':'2026-07-01T21:00:00-07:00','endAt':'2026-07-02T06:00:00-07:00','energyKwh':738}]
        with patch.object(cp,'load_alarm',return_value=alarm): result=cp.build_alarm_context(sessions)
        self.assertEqual(result['visibleChargepointKwh'],738)
        self.assertIsNone(result['recentChargepointShareOfAlarm7d'])
        self.assertFalse(result['alarm7dComparisonVerified'])
        self.assertIsNone(result['daily'][0]['alarmEnergyClampKwh'])

    def test_same_day_without_verified_completeness_still_does_not_make_a_share(self):
        with patch.object(cp,'load_alarm',return_value={'dailyKwh':[{'date':'2026-10-19','meter':'Energy Clamp','kwh':50}], 'periodKwh':[{'period':'7d','meter':'Energy Clamp','kwh':300}]}):
            result=cp.build_alarm_context([{'startAt':'2026-10-19T10:00:00-07:00','endAt':'2026-10-19T12:00:00-07:00','energyKwh':10}])
        self.assertIsNone(result['recentChargepointShareOfAlarm7d'])

    def test_cached_legacy_share_does_not_create_current_high_ev_alert(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp)
            (data/'latest_chargepoint_pairs.json').write_text(json.dumps({'alarm':{'recentChargepointShareOfAlarm7d':2.324}}))
            with patch.object(combined,'DATA_DIR',data),patch.object(combined,'CONFIG_PATH',data/'config.json'),patch.object(combined,'live_envoy_source',return_value={}),patch.object(combined,'live_sense_source',return_value={}):
                result=combined.build_payload()
        self.assertFalse(any(x.get('title')=='Recent EV charging share is high' for x in result['alerts']))
        self.assertFalse(any('232.4%' in x for x in result['insights']))

    def test_recommendation_does_not_treat_total_history_as_a_current_share(self):
        with patch.object(ops,'load_json',return_value={}): result=ops.build_payload()
        ev=next(x for x in result['opportunities'] if x['area']=='EV charging')
        self.assertIn('unverified',ev['finding'])
        self.assertNotIn('30%',ev['finding'])

if __name__=='__main__':unittest.main()
