"""Separate retained brightness from actual power; reviewed Alarm.com plugin only."""
import argparse
import json
import shutil
import subprocess
from pathlib import Path

OLD = '''            case node_alarm_dot_com_1.WebSocketEventTypes.SwitchLevelChanged: {
                const brightness = event.EventValue;
                if (accessory.context.state !== true) {
                    accessory.context.state = true;
                    service.updateCharacteristic(hap.Characteristic.On, true);
                }
                if (accessory.context.isDimmer && brightness !== accessory.context.lightLevel) {
                    log.info(`Updating light ${name} (${id}), brightness=${brightness}, prev=${accessory.context.lightLevel}`);
                    accessory.context.lightLevel = brightness;
                    service.updateCharacteristic(hap.Characteristic.Brightness, brightness);
                }
                break;
            }'''
NEW = '''            case node_alarm_dot_com_1.WebSocketEventTypes.SwitchLevelChanged: {
                const brightness = event.EventValue;
                // A retained dimmer level is not evidence that power is on.
                if (accessory.context.isDimmer && brightness !== accessory.context.lightLevel) {
                    log.info(`Updating light ${name} (${id}), brightness=${brightness}, prev=${accessory.context.lightLevel}`);
                    accessory.context.lightLevel = brightness;
                    service.updateCharacteristic(hap.Characteristic.Brightness, brightness);
                }
                // Coalesce a dragged brightness stream into one authoritative read.
                clearTimeout(this.lightPowerRefreshTimer);
                this.lightPowerRefreshTimer = setTimeout(() => {
                    this.lightPowerRefreshTimer = undefined;
                    Promise.resolve(this.ctx.refreshDevices()).catch(() => {
                        log.debug('Light power reconciliation failed; awaiting the next device refresh');
                    });
                }, 750);
                break;
            }'''
CONNECTED = "                this.log.info('WebSocket connection established.');"
RECONCILED = CONNECTED + '''
                // Reconcile restored caches and events missed while disconnected.
                void this.refreshDevices().catch(() => {
                    this.log.debug('WebSocket state reconciliation failed; awaiting the next refresh');
                });'''


def patched(source):
    if NEW in source:
        return source
    if source.count(OLD) != 1:
        raise ValueError('Light handler layout changed; review before applying')
    return source.replace(OLD, NEW)


def patched_connection(source):
    if RECONCILED in source:
        return source
    if source.count(CONNECTED) != 1:
        raise ValueError('Connection handler layout changed; review before applying')
    return source.replace(CONNECTED, RECONCILED)


def verify(root, node):
    # Exercise the installed compiled handler with fake HAP and refresh methods.
    # No login, real accessory, light command, or network request occurs.
    code = r'''
const root=process.argv[1];
const {LightHandler}=require(root+'/dist/handlers/LightHandler');
const {WebSocketEventTypes:E}=require(root+'/node_modules/node-alarm-dot-com');
const updates=[];const timers=new Map();let seq=0,refreshes=0;
global.setTimeout=fn=>{timers.set(++seq,fn);return seq};
global.clearTimeout=id=>timers.delete(id);
const ctx={api:{hap:{Service:{Lightbulb:'light'},Characteristic:{On:'On',Brightness:'Brightness'}}},
log:{info(){},debug(){}},refreshDevices(){refreshes++;return Promise.resolve()}};
const h=new LightHandler(ctx);
const a={context:{accID:'test',name:'Test',state:false,lightLevel:100,isDimmer:true},
getService(){return{updateCharacteristic(k,v){updates.push([k,v])}}}};
const check=(ok,message)=>{if(!ok)throw Error(message)};
h.statFromWebSocket(a,{EventType:E.SwitchLevelChanged,EventValue:100});
check(a.context.state===false && updates.length===0,'Retained brightness falsely powered on');
h.statFromWebSocket(a,{EventType:E.SwitchLevelChanged,EventValue:50});
check(a.context.state===false && a.context.lightLevel===50,'Brightness or power incorrect');
check(timers.size===1,'Brightness refreshes not coalesced');
[...timers.values()][0]();check(refreshes===1,'Authoritative refresh not requested');
h.statFromWebSocket(a,{EventType:E.LightTurnedOn});check(a.context.state===true,'Real On event lost');
h.statFromWebSocket(a,{EventType:E.LightTurnedOff});check(a.context.state===false,'Real Off event lost');
h.statFromWebSocket(a,{EventType:E.LightTurnedOn});
h.stat(a,{attributes:{state:3,lightLevel:100}});check(a.context.state===false,'Authoritative Off lost');
const fs=require('fs'),vm=require('vm');
const source=fs.readFileSync(root+'/dist/index.js','utf8');
const start=source.indexOf('            client.onopen = () => {');
const end=source.indexOf('\n            };',start);
check(start>=0 && end>start,'Connection callback not found');
const callback=source.slice(start,end+'\n            };'.length);
const client={};let connectionReads=0;
const platform={wsConnectGeneration:1,wsClient:client,wsConsecutiveFailures:1,
log:ctx.log,refreshDevices(){connectionReads++;return Promise.resolve()}};
const sandbox={client,generation:1,platform,clearTimeout(){},setTimeout(){return 1},
WS_REFRESH_INTERVAL_MS:1000,WS_REFRESH_JITTER_MS:0};
vm.runInNewContext('(function(){'+callback+'}).call(platform)',sandbox);
client.onopen();check(connectionReads===1,'Connection did not reconcile cached state');
platform.wsConnectGeneration=2;client.onopen();check(connectionReads===1,'Stale connection performed a read');
platform.wsConnectGeneration=1;platform.isShuttingDown=true;client.onopen();check(connectionReads===1,'Shutdown performed a read');
console.log(JSON.stringify({ok:true,checks:9,realDeviceCommands:0}));
'''
    result = subprocess.run([str(node), '-e', code, str(root)], text=True,
                            capture_output=True, check=True, timeout=15)
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--node', type=Path, default=Path('node'))
    args = parser.parse_args()
    if json.loads((args.root/'package.json').read_text()).get('version') != '1.13.1':
        raise SystemExit('Unreviewed Alarm.com plugin version; no changes made')
    path = args.root/'dist/handlers/LightHandler.js'
    source = path.read_text()
    result = patched(source)
    connection_path = args.root/'dist/index.js'
    connection_source = connection_path.read_text()
    connection_result = patched_connection(connection_source)
    if args.apply and result != source:
        backup = path.with_suffix('.js.before-light-power-reconcile')
        if not backup.exists(): shutil.copy2(path, backup)
        path.write_text(result)
    if args.apply and connection_result != connection_source:
        backup = connection_path.with_suffix('.js.before-light-power-reconcile')
        if not backup.exists(): shutil.copy2(connection_path, backup)
        connection_path.write_text(connection_result)
    report = {'changed': result != source or connection_result != connection_source, 'applied': args.apply}
    if args.verify: report['verification'] = verify(args.root, args.node)
    print(json.dumps(report))


if __name__ == '__main__':
    main()
