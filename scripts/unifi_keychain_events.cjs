'use strict';
// Vendor occupancy algorithms and UUIDs remain; transport and stale-cache guards change.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const cp = require('node:child_process');

function readConfig() {
  return JSON.parse(fs.readFileSync(path.join(os.homedir(),
    'Library/Application Support/SmartHomeMonitor/config/unifi_api_key.json'), 'utf8'));
}
function readKey(config) {
  try {
    return cp.execFileSync('/usr/bin/security', ['find-generic-password', '-s', config.service,
      '-a', config.account, '-w'], {encoding:'utf8', timeout:5000, stdio:['ignore','pipe','pipe']}).trim();
  } catch (_) { throw new Error('UniFi Keychain credential unavailable'); }
}

function writeHealth(report) {
  const destination=path.join(os.homedir(),'Library/Application Support/SmartHomeMonitor/data/latest_unifi_plugin_health.json');
  const temporary=destination+'.tmp';
  fs.writeFileSync(temporary,JSON.stringify(report),{mode:0o600});
  fs.renameSync(temporary,destination);
}

exports.withBase = function withBase(Base, deps = {}) {
  return class KeychainUnifiEvents extends Base {
    constructor(opts) {
      const config = (deps.readConfig || readConfig)();
      const pin = new URL(config.controller);
      if (pin.protocol !== 'https:' || pin.username || pin.password || pin.search || pin.hash
          || opts.host !== pin.hostname || Number(opts.port || 443) !== Number(pin.port || 443)
          || opts.unifios !== true || !/^[a-zA-Z0-9_-]+$/.test(opts.site || 'default')) {
        throw new Error('UniFi controller does not match the approved API-key destination');
      }
      const key = (deps.readKey || readKey)(config);
      if (typeof key !== 'string' || key.length < 16 || key.length > 4096 || /\s/.test(key)) {
        throw new Error('UniFi Keychain credential invalid');
      }
      // Skip password login and event sockets; the plugin retains its configured polling interval.
      super({...opts, username:'unused', password:'unused'});
      this.apiKey = key;
      this.rp = this.rp.defaults({headers:{'User-Agent':this.userAgent, 'X-API-KEY':key}, timeout:12000});
      this.cooldownUntil = 0;
      this.health={authSource:'keychain-api-key',pollingOnly:true,endpoints:{}};
      this.writeHealth=deps.writeHealth || writeHealth;
      this.allowedReads = new Set(['/v2/api/fingerprint_devices/0',
        `/v2/api/site/${this.opts.site}/device`, `/v2/api/site/${this.opts.site}/clients/active`]);
    }
    _login() { return Promise.resolve(); }
    _ensureLoggedIn() {
      return Date.now() < this.cooldownUntil
        ? Promise.reject(new Error('UniFi API authentication is cooling down')) : Promise.resolve();
    }
    connect() { this.isClosed=false; return Promise.resolve(); }
    _url(route) { return super._url(route).replace('/proxy/network//','/proxy/network/'); }
    async get(route) {
      if (!this.allowedReads.has(route)) throw new Error('UniFi adapter permits only occupancy reads');
      if (Date.now() < this.cooldownUntil) throw new Error('UniFi API authentication is cooling down');
      try { const result=await super.get(route); this.recordHealth(route,true,result); return result; }
      catch (error) {
        const code = Number(error.statusCode || error.status);
        if ([401,403,429].includes(code)) this.cooldownUntil = Date.now()+900000;
        this.recordHealth(route,false,null);
        // request-promise errors can carry authorization headers: never pass them to logs.
        throw new Error([401,403,429].includes(code) ? `UniFi occupancy read unavailable (HTTP ${code}); cooling down` : 'UniFi occupancy read unavailable');
      }
    }
    recordHealth(route,ok,payload) {
      const at=new Date().toISOString();
      const name=route.endsWith('/clients/active')?'clients':route.endsWith('/device')?'networkDevices':'fingerprints';
      const count=Array.isArray(payload)?payload.length:Array.isArray(payload?.network_devices)?payload.network_devices.length:null;
      this.health.endpoints[name]={at,ok,count};
      this.health.generatedAt=at;
      this.health.ok=['clients','networkDevices','fingerprints'].every(key=>this.health.endpoints[key]?.ok===true);
      try { this.writeHealth(this.health); } catch (_) {} // A read must not fail because diagnostics storage is unavailable.
    }
    del() { return Promise.reject(new Error('UniFi adapter is read-only')); }
    post() { return Promise.reject(new Error('UniFi adapter is read-only')); }
    put() { return Promise.reject(new Error('UniFi adapter is read-only')); }
    _listen() { return Promise.resolve(); }
    _reconnect() { /* REST polling owns recovery; never retry password login. */ }
    close() { this.isClosed=true; }

  };
};

// Protect the pre-repair cache; stale room mappings remain explicit faults for review.
exports.preserveInvalidAccessories = function preserveInvalidAccessories(platform) {
  platform.protectedOccupancyAccessories ||= new Set(platform.accessories.keys());
  platform.preservedInvalidAccessories = new Set();
  platform.reportedStaleOccupancy ||= new Set();
  for (const [uuid, accessory] of platform.accessories) {
    let valid=false;
    try { valid=Boolean(platform.ensureAccessoryHandler(accessory)?.valid); } catch (_) {}
    if (!valid && !platform.protectedOccupancyAccessories.has(uuid)) {
      platform.removeAccessory(accessory); // Normal lifecycle for new ephemeral guests only.
      continue;
    }
    if (!valid) {
      platform.preservedInvalidAccessories.add(uuid);
      if (!platform.reportedStaleOccupancy.has(uuid)) {
        platform.log.warn('Preserved a stale occupancy accessory for review; pairing identity retained.');
        platform.reportedStaleOccupancy.add(uuid);
      }
    }
    try {
      const service=accessory.getService(platform.Service.OccupancySensor);
      if (service && platform.Characteristic.StatusFault) service.updateCharacteristic(platform.Characteristic.StatusFault,valid?0:1);
      if (service && platform.Characteristic.StatusActive) service.updateCharacteristic(platform.Characteristic.StatusActive,valid);
    } catch (_) {} // Older HAP services may lack optional status characteristics.
  }
};
