#!/usr/bin/env python3
"""Preserve door-open events across a close between monitor polls."""
import argparse
from pathlib import Path

OLD = '''        prior.erds[erd] = {
            value,
            lastSuccessAt: now,
            lastChangedAt: changed ? now : (prior.erds[erd]?.lastChangedAt ?? now),
        };'''
NEW = '''        prior.erds[erd] = {
            ...prior.erds[erd],
            value,
            lastSuccessAt: now,
            lastChangedAt: changed ? now : (prior.erds[erd]?.lastChangedAt ?? now),
            ...(erd === '0x2012' && String(value).toLowerCase() === '00' && changed
                ? { lastOpenedAt: now } : {}),
        };'''


def patch(source):
    if NEW in source:
        return source
    if source.count(OLD) != 1:
        raise ValueError('Heartbeat layout changed; refusing an ambiguous plugin patch')
    return source.replace(OLD, NEW)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--device-path',type=Path);parser.add_argument('--apply',action='store_true');a=parser.parse_args()
    paths=[a.device_path] if a.device_path else sorted((Path.home()/'.local').glob('node-*/lib/node_modules/@homebridge-plugins/homebridge-smarthq/dist/devices/device.js'))
    if not paths:raise SystemExit('Installed SmartHQ device.js not found')
    p=paths[-1];source=p.read_text();updated=patch(source)
    if a.apply and updated!=source:
        p.with_suffix('.js.before-laundry-door-history').write_text(source)
        p.write_text(updated)
    print('Door-open event history: '+('applied' if a.apply else 'validated'))

if __name__=='__main__':main()
