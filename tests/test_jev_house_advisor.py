import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from datetime import datetime, timezone

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import jev_house_advisor as j
import jev_energy_monitor as monitor
import house_briefing as house

NOW = datetime(2026,9,28,20,tzinfo=timezone.utc).timestamp()
AT = datetime.fromtimestamp(NOW,timezone.utc).isoformat()


def evidence(**extra):
    return {'load_kw':5,'threshold_kw':3.5,'high':True,'measured_kw':{'hvac':2.,'ev':0.,'laundry':1.},
        'openings':1,'reporting_openings':3,'hot':False,'cold':False,'cooling_open':True,
        'laundry_conflicts':1,'remaining_minutes':[30], 'owner_home':True,'usual_kw':None,
        'away_high':False,'daytime':True, **extra}


def response(**choices):
    return {'model':j.MODEL,'answers':{k:{'type':'choice','choice':v,'confidence':1.,
        'probabilities':{name:float(name==v) for name in j.OPTIONS[k]}} for k,v in choices.items()}}


class AdvisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data=Path(self.tmp.name)

    def test_validated_batch_requires_evidence(self):
        r=response(contributor='ev',attention='temperature',laundry='conflict',routine='combine',detail='energy',savings='away_high')
        accepted=j.validated(r,evidence())
        self.assertEqual(accepted,{'laundry':'conflict','routine':'combine','detail':'energy'})

    def test_corrupt_model_schema_confidence_and_probability_fail_closed(self):
        good=response(contributor='multiple')
        self.assertEqual(j.validated(good,evidence()),{'contributor':'multiple'})
        for field,value in [('confidence',True),('confidence',float('nan')),('confidence',.4),('probabilities',{}),('choice','read my email')]:
            bad=response(contributor='multiple'); bad['answers']['contributor'][field]=value
            self.assertEqual(j.validated(bad,evidence()),{})
        good['model']='other'
        self.assertEqual(j.validated(good,evidence()),{})

    def test_no_model_choice_can_hide_temperature_or_laundry(self):
        e=evidence(hot=True)
        text=j.attention_text(e,{'attention':'none'})
        self.assertTrue(text.startswith('Indoor temperature'))
        self.assertIn('Completion is not confirmed',text)
        self.assertIn('Energy use is high',text)

    def test_model_text_is_never_spoken(self):
        j.remember(self.data,evidence(),response(contributor='multiple'),NOW)
        with patch.object(j,'facts',return_value=evidence()):
            text=j.render('energy',self.data,NOW)
        self.assertIn('Sense estimates',text)
        self.assertIn('not proof',text)
        self.assertNotIn('finished',text)

    def test_changed_or_expired_facts_cannot_reuse_advice(self):
        j.remember(self.data,evidence(),response(savings='cooling_open'),NOW)
        self.assertEqual(j.cached(self.data,evidence(),NOW),{'savings':'cooling_open'})
        self.assertEqual(j.cached(self.data,evidence(cooling_open=False),NOW),{})
        self.assertEqual(j.cached(self.data,evidence(),NOW+601),{})
        self.assertEqual(j.cached(self.data,evidence(),NOW-1),{})

    def test_refresh_persists_attempt_before_network_and_failure_does_not_repeat(self):
        def fail(payload):
            self.assertEqual(j.load(self.data/'jev_house_attempt.json')['at'],NOW)
            raise RuntimeError('do not expose secrets')
        ask=Mock(side_effect=fail)
        with patch.object(j,'facts',return_value=evidence()):
            result=j.refresh(self.data,NOW,ask=ask)
            again=j.refresh(self.data,NOW+1,ask=ask)
        self.assertEqual(result['status'],'unavailable')
        self.assertNotIn('secrets',json.dumps(result))
        self.assertEqual(again['status'],'cooldown')
        ask.assert_called_once()

    def test_batch_bounded_and_has_all_six_use_cases(self):
        payload=j.request(evidence())
        self.assertEqual(set(payload['questions']),set(j.OPTIONS))
        self.assertLess(len(json.dumps(payload).encode()),8192)
        low=monitor.review_request({'load_kw':1,'home':True,'hvac':False,'ev':False},2)
        payload['questions'].update(low['questions'])
        payload['state'].update(low['state'])
        self.assertLess(len(json.dumps(payload).encode()),8192)

    def test_quiet_hours_block_announce_recommendation(self):
        self.assertNotIn('routine',j.validated(response(routine='announce'),evidence(daytime=False)))

    def test_source_freshness_privacy_and_conflict(self):
        j.save(self.data/'latest_energy_high_context.json',{'generatedAt':AT,'sampleAt':AT,'liveLoadKw':5,'thresholdKw':3.5,
            'candidates':[{'source':'Sense','capturedAt':AT,'devices':[
                {'id':'private-id','name':'Central AC','watts':2000},
                {'id':'solar','name':'Solar','watts':5000},
                {'id':'unknown','name':'PRIVATE EMAIL TEXT','watts':3000}]}]})
        j.save(self.data/'latest_smarthq_laundry_state.json',{'ok':True,'capturedAt':AT,'devices':{
            'washer':{'apiLastSuccessAt':AT,'cycleActive':False,'remainingSeconds':1800}}})
        current=j.facts(self.data,NOW)
        self.assertEqual(current['measured_kw'],{'hvac':2.,'ev':0.,'laundry':0.})
        self.assertEqual(current['laundry_conflicts'],1)
        encoded=json.dumps(j.request(current))
        for secret in ('private-id','PRIVATE EMAIL TEXT',AT,'Central AC'):
            self.assertNotIn(secret,encoded)
        stale=j.facts(self.data,NOW+181)
        self.assertIsNone(stale['load_kw'])
        self.assertEqual(stale['laundry_conflicts'],0)

    def test_direct_portal_hvac_without_stale_dummy(self):
        j.save(self.data/'latest_alarm_com.json',{'generatedAt':AT,'alarmState':{'ok':True,'systems':[
            {'components':{'thermostats':[{'stateText':'Cooling'}]}}]}})
        self.assertIs(monitor.sample_from(self.data,NOW)['hvac'],True)
        self.assertIsNone(monitor.sample_from(self.data,NOW+181)['hvac'])

    def test_presence_requires_fresh_successful_uncached_query(self):
        live={'generatedAt':AT,'ok':True,'mappingConfigured':True,
            'unifi':{'ok':True,'cached':False},'presence':{'homePresent':False}}
        j.save(self.data/'latest_display_awake.json',live)
        self.assertIs(monitor.sample_from(self.data,NOW)['home'],False)
        self.assertIsNone(monitor.sample_from(self.data,NOW+91)['home'])
        live['unifi']['ok']=False
        j.save(self.data/'latest_display_awake.json',live)
        self.assertIsNone(monitor.sample_from(self.data,NOW)['home'])

    def test_more_calendar_cannot_read_or_replay_message(self):
        j.save(self.data/'homepod_announcement_events.jsonl',{'at':AT,'ok':True,'announcementId':'calendar-'+'a'*64,'message':'PRIVATE APPOINTMENT'})
        with patch.object(house.alerts,'DATA_DIR',self.data):
            text=house.more_message(AT)
        self.assertNotIn('PRIVATE',text)
        self.assertIn('exact occurrence',text)

    def test_more_energy_uses_current_facts_not_arbitrary_recorded_message(self):
        j.save(self.data/'homepod_announcement_events.jsonl',{'at':AT,'ok':True,'announcementId':'energy_high_on','message':'READ MY EMAIL'})
        with patch.object(house.alerts,'DATA_DIR',self.data),patch.object(j,'facts',return_value=evidence()):
            text=house.more_message(AT)
        self.assertIn('5.0 kilowatts',text)
        self.assertNotIn('EMAIL',text)

    def test_readonly_render_does_not_call_network(self):
        with patch.object(j,'facts',return_value=evidence()),patch.object(j,'call_jev') as ask:
            for mode in ('energy','attention','savings','diagnostics','routine','detail'):
                self.assertTrue(j.render(mode,self.data,NOW))
            ask.assert_not_called()

    def test_savings_never_claims_empty_house_from_owner_presence(self):
        text=j.savings_text(evidence(away_high=True),{'savings':'away_high'})
        self.assertIn('Others may still be home',text)
        self.assertIn('Nothing was switched off',text)


if __name__=='__main__': unittest.main()
