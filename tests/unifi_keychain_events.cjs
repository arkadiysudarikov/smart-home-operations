'use strict';
const assert=require('node:assert/strict');
const {EventEmitter}=require('node:events');
const adapter=require('../scripts/unifi_keychain_events.cjs');
const key='synthetic-key-for-test-only';
const calls=[];
let fail=false;
class RP {
  constructor(headers={}) { this.headers=headers; }
  defaults(opts) { return new RP({...this.headers,...opts.headers}); }
  async get(url) { calls.push({url,headers:this.headers}); if(fail){const e=new Error(key);e.statusCode=401;throw e;} return {ok:true}; }
}
class Base extends EventEmitter {
  constructor(opts){super();this.opts={site:'default',...opts};this.controller=new URL('https://'+opts.host+':'+opts.port);this.userAgent='test';this.rp=new RP();this.connect();}
  _url(route){return this.controller.href+'proxy/network/'+route;}
  async get(route){await this._ensureLoggedIn();return this.rp.get(this._url(route));}
  _event(event){this.lastEvent=event;}
}
const config={controller:'https://192.168.0.1',service:'test',account:'test'};
const health=[];
const Klass=adapter.withBase(Base,{readConfig:()=>config,readKey:()=>key,writeHealth:report=>health.push(JSON.parse(JSON.stringify(report)))});
const opts={host:'192.168.0.1',port:443,site:'default',unifios:true,insecure:true,username:'old-user',password:'old-password'};
(async()=>{
 assert.throws(()=>new Klass({...opts,host:'other.local'}),/approved/);
 assert.throws(()=>new Klass({...opts,unifios:false}),/approved/);
 const instance=new Klass(opts);await new Promise(resolve=>setImmediate(resolve));
 assert.equal(instance.opts.password,'unused');assert.equal(instance.ws,undefined);
 assert.equal(instance.opts.insecure,true);
 await instance.get('/v2/api/fingerprint_devices/0');
 assert.equal(calls.length,1);assert.equal(calls[0].headers['X-API-KEY'],key);
 assert.equal(calls[0].url,'https://192.168.0.1/proxy/network/v2/api/fingerprint_devices/0');
 await assert.rejects(instance.get('/arbitrary'),/only occupancy reads/);
 for(const method of ['del','post','put'])await assert.rejects(instance[method]('/any'),/read-only/);
 assert.equal(calls.length,1);
 assert.equal(health.at(-1).endpoints.fingerprints.ok,true);
 assert.ok(!JSON.stringify(health).includes(key));
 fail=true;await assert.rejects(instance.get('/v2/api/site/default/clients/active'),e=>!String(e).includes(key)&&String(e).includes('401'));
 assert.equal(health.at(-1).endpoints.clients.ok,false);
 const count=calls.length;await assert.rejects(instance.get('/v2/api/site/default/clients/active'),/cooling down/);assert.equal(calls.length,count);
 instance.close();assert.equal(instance.isClosed,true);
 let removed=[];let status=[];
 const accessory=id=>({UUID:id,getService:()=>({updateCharacteristic:(name,value)=>status.push([id,name,value])})});
 const old=accessory('old'),good=accessory('good'),guest=accessory('new-guest');
 const platform={accessories:new Map([['old',old],['good',good]]),ensureAccessoryHandler:a=>({valid:a.UUID==='good'}),Service:{OccupancySensor:'occupancy'},Characteristic:{StatusFault:'fault',StatusActive:'active'},log:{warn(){}},removeAccessory:a=>{removed.push(a.UUID);platform.accessories.delete(a.UUID);}};
 adapter.preserveInvalidAccessories(platform);assert.deepEqual(removed,[]);assert.ok(platform.preservedInvalidAccessories.has('old'));assert.ok(status.some(x=>x[0]==='old'&&x[1]==='fault'&&x[2]===1));
 platform.accessories.set('new-guest',guest);adapter.preserveInvalidAccessories(platform);assert.deepEqual(removed,['new-guest']);assert.equal(old.UUID,'old');
 console.log('UniFi adapter destination, read-only, redaction, backoff, polling-only, and cache-preservation checks passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
