"""Keep ambiguous camera events out of HomeKit's physical-doorbell channel."""
import argparse
import json
import shutil
from pathlib import Path

OLD = '''        if (eventType === node_alarm_dot_com_1.WebSocketEventTypes.VideoCameraTriggered) {
            if (!accessory.context.isDoorbell) {
                log.debug(`Ignoring ring event for non-doorbell camera ${name} (${id})`);
                return false;
            }
            log.info(`Camera ring detected for ${name} (${id})`);
            const doorbellService = accessory.getService(hap.Service.Doorbell);
            doorbellService
                ?.getCharacteristic(hap.Characteristic.ProgrammableSwitchEvent)
                .updateValue(hap.Characteristic.ProgrammableSwitchEvent.SINGLE_PRESS);
            return true;
        }'''
NEW = '''        if (eventType === node_alarm_dot_com_1.WebSocketEventTypes.VideoCameraTriggered) {
            // Event 71 is not proof of a physical button press. Publishing it as
            // SINGLE_PRESS causes false HomePod chimes for camera activity.
            log.debug(`Ignoring ambiguous camera trigger for ${name} (${id}); no verified button press`);
            return false;
        }'''


def patched(source):
    if NEW in source:
        return source
    if source.count(OLD) != 1:
        raise ValueError('Camera event handler changed; review before applying')
    return source.replace(OLD, NEW)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if json.loads((args.root/'package.json').read_text()).get('version') != '1.13.1':
        raise SystemExit('Unreviewed Alarm.com plugin version; no changes made')
    path = args.root/'dist/handlers/CameraHandler.js'
    source = path.read_text()
    result = patched(source)
    if args.apply and result != source:
        backup = path.with_suffix('.js.before-ambiguous-ring-guard')
        if not backup.exists(): shutil.copy2(path, backup)
        path.write_text(result)
    print(json.dumps({'changed': result != source, 'applied': args.apply}))


if __name__ == '__main__':
    main()
