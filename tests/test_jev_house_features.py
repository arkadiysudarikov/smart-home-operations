import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch, Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import jev_house_features as f
import jev_house_advisor as a
from test_jev_house_advisor import evidence, response

class Features(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.data=Path(self.temp.name)
        self.now=datetime(2026,9,30,2,5,tzinfo=timezone.utc).timestamp()

    def test_tariff_hint_is_conditional_expiring_and_keeps_bills_private(self):
        source='https://www.sce.com/save-money/rates-financing/residential-rate-plans/time-of-use-plans'
        profile={'plan':'TOU-D-PRIME','billing':'solar_billing_plan','scheduleSource':source,
            'scheduleVerifiedAt':self.solar_at(self.now),'accountBillAt':self.solar_at(self.now-120*86400),
            'accountBillSha256':'a'*64,'privateAccount':'PRIVATE ACCOUNT'}
        f.save(self.data/'verified_tariff_profile.json',profile)
        with patch('generate_alerts.solar_surplus_sample',return_value=None):
            # Test setup is September; winter advice must not leak into summer.
            e=f.enrich(self.data,evidence(),self.now)
            self.assertFalse(e['tariff_hint_available'])
            winter=datetime(2026,11,5,20,tzinfo=timezone.utc).timestamp()
            profile['scheduleVerifiedAt']=self.solar_at(winter)
            f.save(self.data/'verified_tariff_profile.json',profile)
            e=f.enrich(self.data,evidence(),winter)
            self.assertTrue(e['tariff_hint_available']);self.assertTrue(e['tariff_low_window_now'])
            text=f.render('laundrytime',e,{})
            self.assertIn('If that plan is unchanged',text)
            self.assertIn('not a cheapest-hour guarantee',text)
            self.assertNotIn('PRIVATE',str(a.request(e)))
            self.assertNotIn(source,str(a.request(e)))
            self.assertFalse(f.enrich(self.data,evidence(),winter+31*86400)['tariff_hint_available'])

    def test_model_cannot_invent_solar_or_defer_urgent(self):
        e=evidence(solar_window=False)
        c=a.validated(response(timing='solar',defer='energy'),e)
        self.assertNotIn('timing',c); self.assertNotIn('defer',c)

    def test_changes_require_comparable_count(self):
        e=evidence(); f.checkpoint(self.data,e,self.now)
        with patch('generate_alerts.solar_surplus_sample',return_value=None):
            n=f.enrich(self.data,{**e,'openings':2,'reporting_openings':4},self.now+20)
            self.assertEqual(n['changes'],[])
            n=f.enrich(self.data,{**e,'high':False},self.now+20)
            self.assertEqual(n['changes'],['energy'])

    def test_unknown_timing_does_not_invent_cheapest_hour(self):
        self.assertIn('No best laundry time',f.render('laundrytime',evidence(),{}))
        self.assertIn('not a guaranteed cheapest',f.render('laundrytime',evidence(solar_window=True),{'timing':'solar'}))

    def test_oddity_keeps_all_verified_combinations(self):
        result=f.render('unusual',evidence(),{'oddity':'cooling'})
        self.assertIn('Cooling',result); self.assertIn('not confirmed finished',result)

    def solar_at(self, seconds):
        return datetime.fromtimestamp(seconds,timezone.utc).isoformat()

    def test_solar_requires_ten_minutes_of_meter_readings(self):
        for offset in range(0,601,120):
            with patch('generate_alerts.solar_surplus_sample',return_value=(self.solar_at(self.now+offset),True)):
                f.scheduled(self.data,evidence(),{},self.now+offset,{},None)
                self.assertEqual(f.enrich(self.data,evidence(),self.now+offset)['solar_window'],offset>=600)
        with patch('generate_alerts.solar_surplus_sample',return_value=None):
            self.assertFalse(f.enrich(self.data,evidence(),self.now+1000)['solar_window'])

    def test_polling_cannot_inflate_slow_meter_history(self):
        for offset in range(0,601,120):
            at=self.solar_at(self.now+offset*.6)
            with patch('generate_alerts.solar_surplus_sample',return_value=(at,True)):
                f.scheduled(self.data,evidence(),{},self.now+offset,{},None)
                self.assertFalse(f.enrich(self.data,evidence(),self.now+offset)['solar_window'])

    def test_repeated_rollback_gap_and_legacy_samples_do_not_qualify(self):
        for offset in range(0,241,120):
            with patch('generate_alerts.solar_surplus_sample',return_value=(self.solar_at(self.now),True)):
                f.scheduled(self.data,evidence(),{},self.now+offset,{},None)
        state=f.load(self.data/'jev_solar_window.json')
        self.assertEqual(state['since'],state['last_sample'])
        for offset in (120,720):
            with patch('generate_alerts.solar_surplus_sample',return_value=(self.solar_at(self.now+offset),True)):
                f.scheduled(self.data,evidence(),{},self.now+offset,{},None)
                self.assertFalse(f.enrich(self.data,evidence(),self.now+offset)['solar_window'])
        f.save(self.data/'jev_solar_window.json',{'active':True,'since':self.solar_at(self.now-1000),'last':self.solar_at(self.now)})
        with patch('generate_alerts.solar_surplus_sample',return_value=(self.solar_at(self.now),True)):
            f.scheduled(self.data,evidence(),{},self.now,{},None)
            self.assertFalse(f.enrich(self.data,evidence(),self.now)['solar_window'])

    def test_recap_once_occupied_evening_and_unpaused(self):
        send=Mock(return_value={'status':'accepted'}); config={'evening_recap_enabled':True}
        with patch('generate_alerts.solar_surplus_sample',return_value=None),patch('announcement_pause.active',return_value=False):
            f.scheduled(self.data,evidence(owner_home=False),{'recap':'issues'},self.now,config,send)
            send.assert_not_called()
            f.scheduled(self.data,evidence(),{'recap':'issues'},self.now,config,send)
            f.scheduled(self.data,evidence(),{'recap':'issues'},self.now+60,config,send)
            send.assert_called_once()
        with patch('generate_alerts.solar_surplus_sample',return_value=None),patch('announcement_pause.active',return_value=True):
            f.scheduled(self.data,evidence(),{'recap':'issues'},self.now+86400,config,send)
            send.assert_called_once()

    def test_recap_silent_outside_window_or_without_model(self):
        send=Mock()
        with patch('generate_alerts.solar_surplus_sample',return_value=None),patch('announcement_pause.active',return_value=False):
            f.scheduled(self.data,evidence(),{},self.now,{'evening_recap_enabled':True},send)
            f.scheduled(self.data,evidence(),{'recap':'issues'},self.now+7200,{'evening_recap_enabled':True},send)
            send.assert_not_called()

if __name__=='__main__': unittest.main()
