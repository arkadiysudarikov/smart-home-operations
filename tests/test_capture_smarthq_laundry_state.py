import unittest
import subprocess
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CaptureSmartHQLaundryStateTests(unittest.TestCase):
    def test_tank_poll_uses_case_sensitive_route_and_never_reuses_old_full_value(self):
        code = '''const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {readDetergentTank:r}=require(process.argv[1]);
(async()=>{const d=fs.mkdtempSync(path.join(os.tmpdir(),'tank-test-'));
try {
const tokenPath=path.join(d,'token'),heartbeatPath=path.join(d,'heartbeat');
fs.writeFileSync(tokenPath,JSON.stringify({tokenSet:{access_token:'test-only'}}));
fs.writeFileSync(heartbeatPath,JSON.stringify({devices:{w:{nickname:'Washer'}}}));
const options={tokenPath,heartbeatPath,now:()=> 'observed'};
let calls=0;
const low=await r({...options,fetcher:async(u,o)=>{calls++;if(!u.endsWith('/erd/0x203C')||o.redirect!=='error')throw Error('bad route');return {ok:true,json:async()=>({value:'01',time:'changed'})}}});
const full=await r({...options,fetcher:async()=>({ok:true,json:async()=>({value:'02'})})});
const failed=await r({...options,fetcher:async()=>({ok:false,status:401})});
const unknown=await r({...options,fetcher:async()=>({ok:true,json:async()=>({value:'ff'})})});
console.log(JSON.stringify({low,full,failed,unknown,calls}));
}finally{fs.rmSync(d,{recursive:true,force:true})}})();'''
        result=subprocess.run(['node','-e',code,str(ROOT/'scripts/capture_smarthq_laundry_state.js')],capture_output=True,text=True,check=True)
        d=json.loads(result.stdout)
        self.assertTrue(d['low']['detergentTankLow'])
        self.assertEqual(d['low']['detergentTankObservedAt'],'observed')
        self.assertFalse(d['full']['detergentTankLow'])
        self.assertIsNone(d['failed']['detergentTankLow'])
        self.assertIsNone(d['unknown']['detergentTankObservedAt'])
        self.assertEqual(d['calls'],1)

    def test_decodes_observed_door_and_tank_without_inventing_unknown_values(self):
        code = '''const {laundrySignals:s}=require(process.argv[1]);
console.log(JSON.stringify([s({}),s({erds:{'0x2012':{value:'00',lastOpenedAt:'observed'},'0x203c':{value:'01'}}}),s({erds:{'0x2012':{value:'01'},'0x203c':{value:'02'}}})]));'''
        p=subprocess.run(['node','-e',code,str(ROOT/'scripts/capture_smarthq_laundry_state.js')],capture_output=True,text=True,check=True)
        unknown,low,full=json.loads(p.stdout)
        self.assertIsNone(unknown['doorOpen'])
        self.assertIsNone(unknown['detergentTankLow'])
        self.assertTrue(low['doorOpen'])
        self.assertTrue(low['detergentTankLow'])
        self.assertFalse(full['doorOpen'])
        self.assertFalse(full['detergentTankLow'])
    def test_combo_uses_combination_accessory_and_washer_service(self):
        source = (ROOT / "scripts" / "capture_smarthq_laundry_state.js").read_text()

        self.assertIn('names.has("Combination Washer Dryer")', source)
        self.assertIn(
            'combo: { accessoryName: "Combination Washer Dryer", mainServiceName: "Washer/Dryer" }',
            source,
        )
        self.assertIn("apiLastSuccessAt", source)
        self.assertIn("smarthq_erd_heartbeat.json", source)


if __name__ == "__main__":
    unittest.main()
