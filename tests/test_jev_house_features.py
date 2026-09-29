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

    def test_solar_requires_ten_minutes_and_fresh_samples(self):
        e=evidence()
        with patch('generate_alerts.solar_surplus_sample',return_value=('now',True)), patch('announcement_pause.active',return_value=False):
            f.scheduled(self.data,e,{},self.now,{},None)
            self.assertFalse(f.enrich(self.data,e,self.now)['solar_window'])
            for offset in range(120,721,120): f.scheduled(self.data,e,{},self.now+offset,{},None)
            self.assertTrue(f.enrich(self.data,e,self.now+720)['solar_window'])
            self.assertFalse(f.enrich(self.data,e,self.now+1000)['solar_window'])

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
