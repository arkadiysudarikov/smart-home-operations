import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import jev_energy_monitor as j
import jev_laundry_review as transport

NOW = datetime(2026,9,28,19,tzinfo=timezone.utc)


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.sample = {'at': NOW.isoformat(), 'source': 'sense_whole_house', 'load_kw': .3,
                       'verified': True, 'home': True, 'hvac': False, 'ev': False}

    def test_dry_run_never_calls_or_writes(self):
        ask = Mock()
        with patch.object(j,'sample_from',return_value=self.sample):
            report = j.tick(self.data,{'enabled':True},NOW.timestamp(),ask=ask)
        ask.assert_not_called()
        self.assertEqual(report['status'],'insufficient_history')
        self.assertFalse((self.data/'jev_energy_state.json').exists())

    def test_advisory_persisted_before_call_and_cooldown(self):
        def ask(payload):
            self.assertEqual(json.loads((self.data/'jev_energy_state.json').read_text())['lastCall'],NOW.timestamp())
            self.assertNotIn('at',payload['state'])
            return {'model':'jev-1.13.0','answers':{'energy_review':{'type':'choice','choice':'insufficient_data'}}}
        with patch.object(j,'sample_from',return_value=self.sample):
            report = j.tick(self.data,{'enabled':True},NOW.timestamp(),mutate=True,ask=ask)
            self.assertEqual(report['calls'],1)
            no_call=Mock()
            j.tick(self.data,{'enabled':True},NOW.timestamp()+60,mutate=True,ask=no_call)
            no_call.assert_not_called()

    def test_stale_power_and_disabled_are_silent(self):
        for config,sample in [({'enabled':False},self.sample),({'enabled':True},{**self.sample,'verified':False})]:
            ask=Mock()
            with patch.object(j,'sample_from',return_value=sample):
                j.tick(self.data,config,NOW.timestamp(),mutate=True,ask=ask)
            ask.assert_not_called()

    def test_unknown_context_is_not_added_as_verified_history(self):
        with patch.object(j,'sample_from',return_value={**self.sample,'hvac':None}):
            report=j.tick(self.data,{'enabled':True},NOW.timestamp())
        self.assertEqual(report['historySamples'],0)
        self.assertFalse(report['contextKnown'])

    def test_adapter_missing_ev_is_unknown_not_virtual_switch_off(self):
        j.save(self.data/'sense_now_latest.json', {'ok':True,'online':True,
            'capturedAt':NOW.isoformat(),'watts':300,'devices':[]})
        j.save(self.data/'display_awake_presence_state.json',
            {'homePresent':True,'lastConfirmedAt':NOW.timestamp()})
        j.save(self.data/'latest.json', {'captured_at':NOW.isoformat(),
            'homeEvents':{'currentCharacteristics':{
                'thermostat':{'plugin':'homebridge-node-alarm-dot-com',
                    'characteristic':'CurrentHeatingCoolingState','value':0},
                'car':{'accessory':'🔋 Car Charging','characteristic':'On','value':False}}}})
        j.save(self.data/'latest_homekit_virtual_sensors.json',
            {'generatedAt':NOW.isoformat(),'freshness':{'alarmPortalGeneratedAt':NOW.isoformat()}})
        sample=j.sample_from(self.data,NOW.timestamp())
        self.assertIsNone(sample['ev'])
        self.assertIs(sample['hvac'],False)
        self.assertTrue(sample['home'])
        self.assertEqual(sample['load_kw'],.3)
        j.save(self.data/'sense_now_latest.json', {'ok':True,'online':True,
            'capturedAt':NOW.isoformat(),'watts':300,
            'devices':[{'id':'category-ev','watts':0}]})
        self.assertIs(j.sample_from(self.data,NOW.timestamp())['ev'],False)
        stale=j.sample_from(self.data,NOW.timestamp()+181)
        self.assertFalse(stale['verified'])
        self.assertIsNone(stale['home'])
        self.assertIsNone(stale['hvac'])
        self.assertIsNone(stale['ev'])

    def seed_candidate(self):
        history=[{**self.sample,'at':(NOW-timedelta(days=n)).isoformat(),'load_kw':2} for n in range(1,8)]
        episode={'since':(NOW-timedelta(minutes=16)).isoformat(),'last':(NOW-timedelta(minutes=1)).isoformat(),
                 'signature':['sense_whole_house',True,False,False]}
        j.save(self.data/'jev_energy_state.json',{'history':history,'detector':{'episode':episode}})

    def test_candidate_persists_before_delivery_and_never_repeats(self):
        self.seed_candidate()
        response={'model':'jev-1.13.0','answers':{'low_energy':{'type':'choice','choice':'unusual_low',
                  'confidence':1.,'probabilities':{'unusual_low':1.,'expected_low':0.,'review':0.}}}}
        def deliver(message,identifier):
            state=json.loads((self.data/'jev_energy_state.json').read_text())
            self.assertTrue(state['detector']['episode']['attempted'])
            self.assertEqual(identifier,'energy_low')
            return {'status':'accepted'}
        send=Mock(side_effect=deliver)
        with patch.object(j,'sample_from',return_value=self.sample),patch.object(j,'active',return_value=False),patch.object(j,'datetime',wraps=datetime) as clock:
            clock.now.return_value=NOW
            report=j.tick(self.data,{'enabled':True},NOW.timestamp(),mutate=True,ask=lambda _:response,deliver=send)
            self.assertEqual(report['delivery'],'accepted')
            j.tick(self.data,{'enabled':True},NOW.timestamp()+60,mutate=True,ask=Mock(),deliver=send)
        send.assert_called_once()

    def test_context_changes_during_call_no_delivery(self):
        self.seed_candidate()
        send=Mock()
        with patch.object(j,'sample_from',side_effect=[self.sample,{**self.sample,'home':False}]):
            report=j.tick(self.data,{'enabled':True},NOW.timestamp(),mutate=True,ask=lambda _: {},deliver=send)
        self.assertEqual(report['status'],'changed_during_request')
        send.assert_not_called()

    def test_api_failure_is_not_immediately_retried(self):
        ask=Mock(side_effect=RuntimeError('synthetic failure'))
        with patch.object(j,'sample_from',return_value=self.sample):
            report=j.tick(self.data,{'enabled':True},NOW.timestamp(),mutate=True,ask=ask)
            self.assertEqual(report['status'],'jev_unavailable')
            j.tick(self.data,{'enabled':True},NOW.timestamp()+60,mutate=True,ask=ask)
        ask.assert_called_once()


class BudgetTests(unittest.TestCase):
    def test_budget_exhaustion_never_sends(self):
        with tempfile.TemporaryDirectory() as directory:
            data=Path(directory)
            month=datetime.now(timezone.utc).strftime('%Y-%m')
            (data/'jev_budget.json').write_text(json.dumps({month:1000000}))
            with patch.object(transport,'DATA',data),patch.object(transport.subprocess,'run',return_value=SimpleNamespace(stdout=b'synthetic-not-a-real-key')),patch.object(transport.urllib.request,'build_opener') as network:
                with self.assertRaisesRegex(RuntimeError,'allocation'):
                    transport.call_jev(transport.REQUEST)
                network.assert_not_called()

    def test_oversized_request_never_reads_key_or_sends(self):
        with patch.object(transport.subprocess,'run') as key:
            with self.assertRaisesRegex(RuntimeError,'size'):
                    transport.call_jev({'state':'x'*9000})
            key.assert_not_called()


if __name__=='__main__': unittest.main()
