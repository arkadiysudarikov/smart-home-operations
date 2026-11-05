import json
import sys
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import home_operations as h
import announcement_ack as ack


class Operations(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.data=Path(self.temp.name)/'data';self.data.mkdir()
        self.now=datetime(2026,11,5,20,tzinfo=timezone.utc).timestamp()

    def put(self,file,value):h.save(self.data/file,value)
    def at(self,offset=0):return datetime.fromtimestamp(self.now+offset,timezone.utc).isoformat()

    def envoy(self,at,storage=-200,net=-1300):
        def row(kind,watts):return {'measurementType':kind,'wNow':watts,'readingTime':at,'type':'eim','activeCount':1}
        return {'ok':True,'probes':[{'productionStatus':200,'production':{
            'production':[row('production',4000)],'consumption':[row('total-consumption',2700),row('net-consumption',net)],
            'storage':[row('storage',storage)]}}]}

    def test_solar_requires_fresh_synchronized_readings_and_no_battery_export(self):
        self.assertTrue(h.solar_sample(self.envoy(self.now),self.now)['surplus'])
        self.assertFalse(h.solar_sample(self.envoy(self.now,storage=800),self.now)['surplus'])
        self.assertIsNone(h.solar_sample(self.envoy(self.now-181),self.now))
        x=self.envoy(self.now);x['probes'][0]['production']['storage'][0]['wNow']=float('nan')
        self.assertIsNone(h.solar_sample(x,self.now))

    def test_iq_battery_meter_is_used_instead_of_inactive_legacy_acb_zero(self):
        x=self.envoy(self.now);p=x['probes'][0]
        p['production']['storage']=[{'type':'acb','activeCount':0,'wNow':0,'readingTime':0}]
        p.update(metersStatus=200,meterInfo=[{'eid':42,'measurementType':'storage','state':'enabled','meteringStatus':'normal'}],
                 meters=[{'eid':42,'timestamp':self.now,'instantaneousDemand':900}])
        self.assertFalse(h.solar_sample(x,self.now)['surplus'])
        p['meters'][0]['instantaneousDemand']=-200
        self.assertTrue(h.solar_sample(x,self.now)['surplus'])
        x=self.envoy(self.now);x['probes'][0]['production']['storage'][0]['readingTime']-=31
        self.assertIsNone(h.solar_sample(x,self.now))

    def test_laundry_opportunity_needs_real_ten_minute_window_and_idle_appliances(self):
        state={}
        for t in range(0,601,60):
            self.put('latest_envoy_direct.json',self.envoy(self.now+t))
            self.put('latest_smarthq_laundry_state.json',{'ok':True,'capturedAt':self.at(t),'devices':{
                k:{'apiLastSuccessAt':self.at(t),'cycleActive':False} for k in ['washer','dryer','combo']}})
            advice=h.laundry_advice(self.data,state,self.now+t,{'solar_advice_enabled':True})
            self.assertEqual(advice['eligible'],t>=600)
        old=state['solar']['since']
        advice=h.laundry_advice(self.data,state,self.now+660,{'solar_advice_enabled':True})
        self.assertEqual(state['solar']['last']-old,600) # rereading the same meter cannot grow the window
        self.assertFalse(h.laundry_advice(self.data,state,self.now+900,{'solar_advice_enabled':True})['eligible'])

    def test_health_debounces_once_and_rearms_only_after_recovery(self):
        issues={'relay':'Check relay.'};state={}
        for t in range(0,601,60):state,due=h.incidents(state,issues,self.now+t)
        self.assertEqual(due,issues);state['relay']['notified']=True
        state,due=h.incidents(state,issues,self.now+660);self.assertEqual(due,{})
        state,due=h.incidents(state,{},self.now+720);self.assertEqual(state,{})
        state,due=h.incidents(state,issues,self.now+780);self.assertEqual(due,{})

    def test_unknown_native_home_and_stale_alarm_never_report_full_coverage(self):
        report=h.consistency(self.data,{},self.now)
        self.assertFalse(report['complete']);self.assertEqual(report['findings'],[])
        self.assertEqual(report['coverage']['appleHome'],'stale_manual_review')

    def test_conflicting_garage_timers_and_unguarded_away_are_detected_without_controls(self):
        self.put('latest_automation_consistency_sources.json',{'generatedAt':self.at(),'automationRules':{
            'ok':True,'garageLightRules':[{'isPaused':False,'followUpAction':'Turn OFF after 5 mins'}]}})
        self.put('home_automation_review.json',{'reviewedAt':self.at(),'rules':[
            {'enabled':True,'trigger':'last-departure','action':'arm-away','target':'panel'}]})
        result=h.consistency(self.data,{},self.now)
        self.assertEqual({x['id'] for x in result['findings']},{'garage_fixed_off','departure_dogs'})
        self.assertFalse(result['controlsExecuted'])

    def test_manual_refill_resolves_only_old_cloud_episode(self):
        def washer(updated):return {'ok':True,'capturedAt':self.at(),'devices':{'washer':{
            'detergentTankLow':True,'detergentTankObservedAt':self.at(),'detergentTankSourceUpdatedAt':updated}}}
        self.put('latest_smarthq_laundry_state.json',washer(self.at(-100)))
        self.put('maintenance_resolutions.json',{'washerTankRefilledAt':self.at(-50)})
        self.assertIs(h.condition(self.data,'detergent_tank_low',self.now),False)
        self.put('latest_smarthq_laundry_state.json',washer(self.at()))
        self.assertIs(h.condition(self.data,'detergent_tank_low',self.now),True)

    def test_routine_repeats_are_combined_and_resolved_energy_is_removed(self):
        (self.data.parent/'config').mkdir();h.save(self.data.parent/'config/smart_operations.json',{'enabled':True})
        self.put('latest_smarthq_laundry_state.json',{'ok':True,'capturedAt':self.at(),'devices':{
            'washer':{'detergentTankLow':True,'detergentTankObservedAt':self.at()}}})
        self.put('latest_energy_high_context.json',{'generatedAt':self.at(),'sampleAt':self.at(),'liveLoadKw':8,'thresholdKw':5})
        for i in ['detergent_tank_low','energy_high_on']:
            ack.record(self.data,{'announcementId':i,'message':i,'ok':True},self.now-300)
        with patch('generate_alerts.run_indoor_homepod_announcement') as send,patch('announcement_followup.active',return_value=False):
            self.assertEqual(ack.tick(self.data,self.now),2)
            send.assert_called_once();self.assertIn('detergent_tank_low',send.call_args.args[0])
        self.put('latest_energy_high_context.json',{'generatedAt':self.at(),'sampleAt':self.at(),'liveLoadKw':2,'thresholdKw':5})
        with patch('generate_alerts.run_indoor_homepod_announcement'),patch('announcement_followup.active',return_value=False):ack.tick(self.data,self.now+1)
        self.assertNotIn('energy_high_on',h.load(self.data/'announcement_ack.json')['pending'])

    def test_digest_is_quiet_away_and_includes_only_actionable_episodes(self):
        self.put('latest_announcement_health.json',{'generatedAt':self.at(),'faults':['Announcement relay failed or log invalid']})
        self.put('latest_smarthq_laundry_state.json',{'capturedAt':self.at()})
        self.put('latest_automation_consistency_sources.json',{'generatedAt':self.at(),'automationRules':{'ok':True}})
        send=Mock(return_value={'status':'accepted'});notify=Mock()
        with patch('announcement_presence.allowed',return_value=False):
            h.tick(self.data,self.now,{'health_hold_seconds':0},deliver=send,notify=notify)
        send.assert_not_called();notify.assert_not_called()
        with patch('announcement_presence.allowed',return_value=True),patch('announcement_pause.active',return_value=False):
            h.tick(self.data,self.now+1,{'health_hold_seconds':0},deliver=send,notify=notify)
            h.tick(self.data,self.now+2,{'health_hold_seconds':0},deliver=send,notify=notify)
        send.assert_called_once();notify.assert_called_once()


if __name__=='__main__':unittest.main()
