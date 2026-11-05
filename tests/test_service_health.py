import json
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import service_health as h
import jev_house_advisor as j
import jev_house_features as f
from test_jev_house_advisor import evidence, response

NOW=datetime(2026,11,5,20,tzinfo=timezone.utc)
AT=NOW.isoformat()


class ServiceHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.data=Path(self.temp.name)/'data';self.data.mkdir()

    def write(self,name,value):
        h.save(self.data/name,value)

    def test_missing_and_stale_sources_are_unknown_not_healthy(self):
        report=h.build(self.data,NOW.timestamp())
        self.assertTrue(all(v is None for v in report['faults'].values()))
        self.assertFalse(report['coverageComplete'])
        self.write('latest_alarm_com.json',{'generatedAt':(NOW-timedelta(minutes=4)).isoformat(),
            'troubleConditions':{'ok':True,'checkedAt':AT,'count':0}})
        self.assertIsNone(h.build(self.data,NOW.timestamp())['faults']['alarm'])

    def test_security_count_is_bounded_by_fresh_verified_source_and_privacy(self):
        self.write('latest_alarm_com.json',{'generatedAt':AT,'troubleConditions':{
            'ok':True,'checkedAt':AT,'count':3,'rows':[{'description':'PRIVATE DEVICE','id':'PRIVATE ID'}]}})
        report=h.build(self.data,NOW.timestamp());e=h.evidence(report)
        self.assertIs(e['service_faults']['alarm'],True)
        self.assertEqual(e['security_trouble_count'],3)
        for text in ('PRIVATE','DEVICE',AT):self.assertNotIn(text,json.dumps(e))
        self.assertIsNone(h.evidence(h.build(self.data,NOW.timestamp()+181))['security_trouble_count'])

    def test_sce_uses_newest_actual_coverage_not_success_timestamp(self):
        old=(NOW-timedelta(days=100)).isoformat()
        newer=(NOW-timedelta(days=20)).isoformat()
        self.write('latest_sce_api.json',{'finishedAt':AT,'ok':True,'coverageEnd':old})
        self.write('latest_combined_energy_monitor.json',{'generatedAt':AT,'sources':{'sce':{'coverageEnd':newer}}})
        report=h.build(self.data,NOW.timestamp())
        self.assertIs(report['faults']['sce'],True)
        sce=next(s for s in report['services'] if s['key']=='sce')
        self.assertEqual((sce['apiCoverageEnd'],sce['importedCoverageEnd']),(old,newer))
        self.write('latest_combined_energy_monitor.json',{'generatedAt':AT,'sources':{'sce':{'coverageEnd':AT}}})
        self.assertIs(h.build(self.data,NOW.timestamp())['faults']['sce'],False)

    def test_reachable_bridge_is_not_device_verification_and_mopar_disabled(self):
        self.write('latest.json',{'captured_at':AT,'homebridge':{'config':{
            'platforms':[{'platform':'Tahoma','childBridgePort':123}],
            'disabledPlugins':['homebridge-mopar']},'ports':[{'port':123}]}})
        services={s['key']:s for s in h.build(self.data,NOW.timestamp())['services']}
        self.assertEqual(services['tahoma']['status'],'bridge_reachable')
        self.assertEqual(services['mopar']['status'],'disabled')
        self.assertEqual(services['homepods']['status'],'manual_verification')

    def test_model_cannot_invent_service_or_maintenance_issues(self):
        e=evidence(service_faults={'alarm':True,'sce':None},security_trouble_count=3)
        accepted=j.validated(response(service='sce',maintenance='security',attention='services'),e)
        self.assertEqual(accepted,{'maintenance':'security','attention':'services'})
        text=j.attention_text(e,{'attention':'none'})
        self.assertIn('Alarm.com reports device trouble',text)
        self.assertIn('named devices',j.CHECKS['alarm'])
        self.assertNotIn('energy',f.supported(e)['defer'])

    def test_actual_source_request_stays_bounded(self):
        e=j.facts(self.data,NOW.timestamp())
        payload=j.request(e)
        import jev_energy_monitor as monitor
        extra=monitor.review_request({'load_kw':1,'home':True,'hvac':False,'ev':False},2)
        payload['state'].update(extra['state']);payload['questions'].update(extra['questions'])
        self.assertLessEqual(len(json.dumps(payload,allow_nan=False).encode()),8192)

    def test_service_fault_changes_require_comparable_known_values(self):
        e=evidence(service_faults={'alarm':True})
        f.checkpoint(self.data,e,NOW.timestamp())
        with patch('generate_alerts.solar_surplus_sample',return_value=None):
            self.assertIn('services',f.enrich(self.data,{**e,'service_faults':{'alarm':False}},NOW.timestamp()+1)['changes'])
            self.assertNotIn('services',f.enrich(self.data,{**e,'service_faults':{'alarm':None}},NOW.timestamp()+1)['changes'])

    def test_unchanged_automatic_recap_is_not_repeated_next_day(self):
        from unittest.mock import Mock
        at=datetime(2026,11,6,3,5,tzinfo=timezone.utc).timestamp() # 19:05 Pacific
        send=Mock(return_value={'status':'accepted'});config={'evening_recap_enabled':True}
        with patch('generate_alerts.solar_surplus_sample',return_value=None),patch('announcement_pause.active',return_value=False):
            f.scheduled(self.data,evidence(),{'recap':'issues'},at,config,send)
            self.assertEqual(f.scheduled(self.data,evidence(load_kw=6),{'recap':'issues'},at+86400,config,send),'unchanged')
            f.scheduled(self.data,evidence(openings=2),{'recap':'issues'},at+86400,config,send)
        self.assertEqual(send.call_count,2)
        with patch('generate_alerts.solar_surplus_sample',return_value=None),patch('announcement_pause.active',return_value=False):
            # A cleared/changed episode can recur without remaining muted forever.
            f.scheduled(self.data,evidence(openings=0),{},at+2*86400-3600,config,None)
            f.scheduled(self.data,evidence(openings=2),{'recap':'issues'},at+2*86400,config,send)
        self.assertEqual(send.call_count,3)


if __name__=='__main__':unittest.main()
