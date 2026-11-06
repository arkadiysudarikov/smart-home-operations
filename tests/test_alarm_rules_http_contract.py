import json
import subprocess
import unittest
from pathlib import Path


class AlarmRulesContract(unittest.TestCase):
    def test_rules_read_uses_exact_jsonapi_accept_and_auth_does_not_leak(self):
        script=r'''const {fetchJson}=require(process.argv[1]);
const http=require('node:http');
const server=http.createServer((req,res)=>{
 if(req.headers.accept!=='application/vnd.api+json'){res.writeHead(500);res.end('{}');return;}
 res.writeHead(200,{'Content-Type':'application/vnd.api+json'});res.end(JSON.stringify({data:[{id:'one'}]}));
});
server.listen(0,'127.0.0.1',async()=>{try{
 const result=await fetchJson(`http://127.0.0.1:${server.address().port}/rules`,{cookie:'test-only',ajaxKey:'test-only'});
 console.log(JSON.stringify(result));
}finally{server.close()}});'''
        module=Path(__file__).resolve().parents[1]/'scripts/capture_alarm_com.js'
        result=subprocess.run(['node','-e',script,str(module)],capture_output=True,text=True,timeout=10,check=True)
        self.assertTrue(json.loads(result.stdout)['ok'])
        self.assertNotIn('test-only',result.stdout)


if __name__=='__main__':unittest.main()
