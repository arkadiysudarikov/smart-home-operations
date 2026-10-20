"""Exact-source gate for the reviewed UniFi occupancy 1.3.0 transport repair."""
import argparse
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

VERSION='1.3.0'
ORIGINAL_HASH='a277e23effc22f2cadaef33232dc8087e9d4c3689ac059020919b63759949d47'
PATCHED_HASH='16ac09320c8702a6d261ecc9d21fd06c5f2f61a538d1c0851662ad0317c24731'
ADAPTER='require(require("path").join(require("os").homedir(), "Library/Application Support/SmartHomeMonitor/scripts/unifi_keychain_events.cjs"))'
OLD_IMPORT='const unifi_events_1 = __importDefault(require("unifi-events"));'
NEW_IMPORT=f'const unifi_events_1 = __importDefault({ADAPTER}.withBase(require("unifi-events")));'
OLD_REMOVE='''    removeInvalidAccessories() {
        const invalidAccessories = [];
        this.accessories.forEach((accessory, uuid) => {
            const handler = this.ensureAccessoryHandler(accessory);
            if (!(handler === null || handler === void 0 ? void 0 : handler.valid)) {
                invalidAccessories.push(accessory);
            }
        });
        for (const accessory of invalidAccessories) {
            this.removeAccessory(accessory);
        }
    }'''
NEW_REMOVE=f'''    removeInvalidAccessories() {{
        {ADAPTER}.preserveInvalidAccessories(this);
    }}'''
OLD_UPDATE='            this.updateAccessoryHandler(handler);'
NEW_UPDATE='''            if (!this.preservedInvalidAccessories || !this.preservedInvalidAccessories.has(uuid)) {
                if (handler && !handler.service && handler.accessory) handler.setupAccessory();
                this.updateAccessoryHandler(handler);
            }'''
OLD_ENSURE='    ensureAccessory(subject, room) {\n        return ensure'
NEW_ENSURE='''    ensureAccessory(subject, room) {
        if (!this.accessories.has(subject.accessoryUUID(room)) && this.accessories.size >= 145) return null;
        return ensure'''


def patched(data, version):
    digest=hashlib.sha256(data).hexdigest()
    if version!=VERSION or digest not in (ORIGINAL_HASH,PATCHED_HASH):
        raise ValueError('Unreviewed UniFi plugin version or source hash; no change')
    if digest==PATCHED_HASH:return data
    text=data.decode()
    for old,new in ((OLD_IMPORT,NEW_IMPORT),(OLD_REMOVE,NEW_REMOVE),(OLD_UPDATE,NEW_UPDATE),(OLD_ENSURE,NEW_ENSURE)):
        if text.count(old)!=1:raise ValueError('Reviewed patch anchor mismatch')
        text=text.replace(old,new)
    result=text.encode()
    if hashlib.sha256(result).hexdigest()!=PATCHED_HASH:raise ValueError('Patched source hash mismatch')
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--plugin-path',type=Path,required=True)
    parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    version=json.loads((args.plugin_path/'package.json').read_text())['version']
    target=args.plugin_path/'dist/platform.js';original=target.read_bytes();result=patched(original,version)
    if args.apply and original!=result:
        backup=Path.home()/'.homebridge/codex-backups'/('unifi-platform-before-keychain-'+datetime.now().strftime('%Y%m%dT%H%M%S')+'.js')
        backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,backup);backup.chmod(0o600)
        target.write_bytes(result)
    print(json.dumps({'reviewed':True,'changed':args.apply and original!=result,'hash':hashlib.sha256(result).hexdigest()}))


if __name__=='__main__':main()
