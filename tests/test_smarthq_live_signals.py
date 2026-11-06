import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from patch_smarthq_live_signals import patched, TARGET, REPLACEMENT
from washer_notifier import tank_alert_actions


class LiveSignalTests(unittest.TestCase):
    def test_plugin_patch_is_idempotent_and_refuses_unknown_layout(self):
        result = patched(TARGET)
        self.assertIn(REPLACEMENT, result)
        self.assertEqual(patched(result), result)
        with self.assertRaises(ValueError): patched('changed plugin')

    def test_real_push_writer_retains_open_history_and_rejects_unrelated_data(self):
        code = '''import {pathToFileURL} from 'node:url';
const {recordLaundrySignal:r}=await import(pathToFileURL(process.argv[1]));
const p=process.argv[2];
if(r('Oven','other','0x203c','01',p)) throw Error('unrelated device');
if(r('Washer','w','0x203c','invalid',p)) throw Error('invalid signal');
r('Washer','w','0x2012','00',p);
r('Washer','w','0x2012','01',p);
r('Washer','w','0x203c','01',p);
'''
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'heartbeat.json'
            subprocess.run(['node','--input-type=module','-e',code,
                            str(ROOT/'scripts/smarthq_live_laundry_signals.mjs'),str(p)],check=True,capture_output=True)
            data=json.loads(p.read_text())
            self.assertEqual(set(data['devices']), {'w'})
            erds=data['devices']['w']['erds']
            self.assertEqual(erds['0x2012']['value'],'01')
            self.assertTrue(erds['0x2012']['lastOpenedAt'])
            self.assertEqual(erds['0x203c']['value'],'01')

    def test_low_tank_warns_once_and_fresh_refill_rearms(self):
        now=datetime(2026,11,3,12,tzinfo=ZoneInfo('America/Los_Angeles'))
        state={}
        current={'fresh':True,'detergentTankLow':True,'detergentTankObservedAt':now.isoformat()}
        self.assertEqual(tank_alert_actions(state,current,now),['notify_tank_low','announce_tank_low'])
        self.assertEqual(tank_alert_actions(state,current,now),[])
        tank_alert_actions(state,{**current,'detergentTankLow':False,'detergentTankObservedAt':(now-timedelta(days=1)).isoformat()},now)
        self.assertTrue(state['detergentTankLowAnnounced'])
        tank_alert_actions(state,{**current,'detergentTankLow':None},now)
        self.assertTrue(state['detergentTankLowAnnounced'])
        tank_alert_actions(state,{**current,'detergentTankLow':False},now)
        self.assertEqual(tank_alert_actions(state,current,now),['notify_tank_low','announce_tank_low'])
