import {readFileSync, writeFileSync, renameSync, mkdirSync} from 'node:fs';
import {dirname} from 'node:path';
import {homedir} from 'node:os';

// Capture actual arrivals, including signals which have no HomeKit service.
export function recordLaundrySignal(name, id, erd, value, path = process.env.SMART_HOME_SMARTHQ_HEARTBEAT_PATH
    ?? `${homedir()}/Library/Application Support/SmartHomeMonitor/data/smarthq_erd_heartbeat.json`) {
  if (!['Washer', 'Dryer', 'Combination Washer Dryer'].includes(name)
      || !['0x200a', '0x2012', '0x203c'].includes(erd)
      || typeof value !== 'string' || !/^[0-9a-f]{2}$/i.test(value)) return false;
  try {
    let data;
    try { data = JSON.parse(readFileSync(path, 'utf8')); }
    catch { data = {version: 1, devices: {}}; }
    data.devices ??= {};
    const now = new Date().toISOString();
    const device = data.devices[id] ?? {erds: {}};
    device.erds ??= {};
    const prior = device.erds[erd] ?? {};
    const changed = prior.value !== value;
    device.erds[erd] = {...prior, value, lastSuccessAt: now,
      lastChangedAt: changed ? now : (prior.lastChangedAt ?? now),
      ...(erd === '0x2012' && value === '00' && changed ? {lastOpenedAt: now} : {})};
    Object.assign(device, {nickname: name, lastSuccessAt: now,
      lastChangedAt: changed ? now : (device.lastChangedAt ?? now)});
    data.devices[id] = device;
    data.updatedAt = now;
    mkdirSync(dirname(path), {recursive: true});
    const temp = `${path}.${process.pid}.tmp`;
    writeFileSync(temp, JSON.stringify(data), {mode: 0o600});
    renameSync(temp, path);
    return true;
  } catch { return false; }
}
