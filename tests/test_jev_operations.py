import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import jev_operations as ops
import jev_house_advisor as advisor
import jev_energy_monitor as monitor
import house_briefing as briefing

NOW=datetime(2026,10,19,22,0,tzinfo=timezone.utc).timestamp()
AT=datetime.fromtimestamp(NOW,timezone.utc).isoformat()

class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.data=Path(self.temp.name)

    def test_log_tail_redacts_and_expires_and_distinguishes_startup(self):
        path=self.data/'log'
        path.write_text('[10/19/2026, 2:59:00 PM] Launched child bridge PRIVATE-IP token=SECRET\n'
            '[10/19/2026, 2:59:01 PM] Error ECONNRESET\n'
            '[10/19/2026, 2:59:02 PM] Office ETIMEDOUT SECRET\n'
            '[10/19/2026, 2:59:03 PM] UniFi 401 Unauthorized\n'
            '[10/19/2026, 1:00:00 PM] 429\n'
            '[10/19/2026, 2:59:04 PM] adding webhook error test failed\n')
        counts=ops.log_summary(path,NOW)
        self.assertEqual((counts['startup_disconnects'],counts['timeouts'],counts['authentication'],counts['rate_limits'],counts['other_faults']),(1,1,1,0,0))
        self.assertNotIn('SECRET',json.dumps(counts)); self.assertNotIn('Office',json.dumps(counts))
        self.assertFalse(ops.log_summary(path,NOW+1000)['available'])
        self.assertFalse(ops.log_summary(self.data/'missing',NOW)['available'])

    def test_truncated_tail_is_bounded_and_future_lines_are_rejected(self):
        path=self.data/'log';path.write_text('x'*2_000_010+'\n[10/19/2026, 3:01:00 PM] ETIMEDOUT\n')
        result=ops.log_summary(path,NOW)
        self.assertTrue(result['truncated']);self.assertFalse(result['available'])

    def test_rules_require_matching_trigger_target_window_and_enabled(self):
        r={'enabled':True,'trigger':'a','target':'b','window':'always','action':'on'}
        result=ops.automation_summary([r,r,{**r,'action':'off'}, {**r,'enabled':False}, {**r,'window':'night','action':'off'}, {**r,'trigger':'other','action':'off'}])
        self.assertEqual(result,{'reviewed_actions':5,'overlaps':1,'conflicts':1})
        self.assertEqual(ops.automation_summary([{'name':'SECRET'}])['reviewed_actions'],0)

    def test_allowlist_drops_secrets_invalid_counts_and_stale_sources(self):
        logs={k:0 for k in ops.COUNTS};logs.update(available=True,timeouts=3,secret='PRIVATE')
        ops.save(self.data/'latest_jev_operations.json',{'generatedAt':AT,'logs':logs})
        ops.save(self.data/'home_automation_review.json',{'reviewedAt':AT,'name':'PRIVATE','rules':[]})
        f=ops.facts(self.data,NOW)
        self.assertNotIn('PRIVATE',json.dumps(f));self.assertEqual(ops.supported(f)['logs'],{'unknown','investigate'})
        self.assertIsNone(ops.facts(self.data,NOW+361)['operations_logs'])
        self.assertIsNone(ops.facts(self.data,NOW+8*86400)['operations_automations'])
        logs['timeouts']=True
        ops.save(self.data/'latest_jev_operations.json',{'generatedAt':AT,'logs':logs})
        self.assertIsNone(ops.facts(self.data,NOW)['operations_logs'])

    def test_missing_evidence_cannot_become_model_all_clear(self):
        e=advisor.facts(self.data,NOW)
        response={'model':advisor.MODEL,'answers':{'automations':{'type':'choice','choice':'none','confidence':1,'probabilities':{k:float(k=='none') for k in ops.OPTIONS['automations']}}}}
        self.assertEqual(advisor.validated(response,e),{})
        self.assertIn('unavailable',ops.render('automations',e,{}))
        self.assertIn('unavailable',ops.render('logs',e,{}))

    def test_live_shape_combined_request_stays_bounded_and_has_no_rule_text(self):
        e=advisor.facts(self.data,NOW)
        request=advisor.request(e)
        low=monitor.review_request({'load_kw':1,'home':True,'hvac':False,'ev':False},2)
        request['questions'].update(low['questions']);request['state'].update(low['state'])
        self.assertLess(len(json.dumps(request).encode()),8192)
        self.assertIn('logs',request['questions']);self.assertIn('automations',request['questions'])

    def test_briefing_modes_are_readonly(self):
        self.assertIn('logs',briefing.MODES);self.assertIn('automations',briefing.MODES)
        with patch.object(briefing.alerts,'DATA_DIR',self.data),patch.object(advisor,'call_jev') as call:
            self.assertIn('unavailable',briefing.build('logs'))
            self.assertIn('unavailable',briefing.build('automations'))
            call.assert_not_called()

if __name__=='__main__':unittest.main()
