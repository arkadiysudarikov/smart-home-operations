"""Preserve genuine laundry pushes; dry-run unless --apply is specified."""
import argparse
import json
import shutil
from pathlib import Path

IMPORT = "import { recordLaundrySignal } from './smartHomeLaundrySignals.mjs';\n"
TARGET = '        const erdName = lookupErdName(obj.item.erd);'
REPLACEMENT = '''        recordLaundrySignal(accessory.displayName, obj.item.applianceId,
            normaliseErd(obj.item.erd), liveValue);
''' + TARGET


def patched(source):
    if IMPORT in source and REPLACEMENT in source:
        return source
    if source.count(TARGET) != 1 or IMPORT in source:
        raise ValueError('Plugin layout changed; review before applying')
    return IMPORT + source.replace(TARGET, REPLACEMENT)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    package = json.loads((args.root/'package.json').read_text())
    if package.get('version') != '0.9.0':
        raise SystemExit('Unreviewed SmartHQ version; no changes made')
    path = args.root/'dist/platform.js'
    source = path.read_text()
    result = patched(source)
    helper = args.root/'dist/smartHomeLaundrySignals.mjs'
    if args.apply:
        if result != source:
            backup = path.with_suffix('.js.before-live-laundry-signals')
            if not backup.exists(): shutil.copy2(path, backup)
        shutil.copy2(Path(__file__).with_name('smarthq_live_laundry_signals.mjs'), helper)
        path.write_text(result)
    print(json.dumps({'applied': args.apply, 'changed': source != result}))


if __name__ == '__main__':
    main()
