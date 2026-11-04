"""Non-destructive, backed-up deployment of repository-owned runtime files."""
import argparse
import ast
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def managed_files(source):
    tracked = None
    if (source/'.git').exists():
        tracked = set(subprocess.check_output(['git','-C',str(source),'ls-files','--cached'],text=True).splitlines())
    for directory in ('scripts', 'launchagents', 'plugins', 'config'):
        for path in sorted((source/directory).rglob('*')):
            if (path.is_file() and not path.is_symlink()
                    and not any(x in path.parts for x in ('node_modules','__pycache__','xcuserdata'))
                    and path.suffix in ('.py','.js','.mjs','.sh','.swift','.plist','.json')):
                # Secret/config files absent from Git must never be copied implicitly.
                if tracked is not None and str(path.relative_to(source)) not in tracked:
                    continue
                if directory == 'config' and path.name not in {
                    'sources.json','additional_announcements.json','jev_energy.json','calendar-reader-Info.plist',
                    'sense.example.json','sce_green_button_connect.example.json','chargepoint.example.json'}:
                    continue
                yield path.relative_to(source)


def deploy(source, runtime):
    files = list(managed_files(source))
    selected = set(files)
    # Parse every Python source before changing any deployed file.
    for rel in files:
        if rel.suffix == '.py':
            tree = ast.parse((source/rel).read_text())
            for node in ast.walk(tree):
                names = ([node.module] if isinstance(node, ast.ImportFrom) else
                         [item.name for item in node.names] if isinstance(node, ast.Import) else [])
                for name in names:
                    if name:
                        dependency = Path('scripts')/(name.split('.')[0]+'.py')
                        if (source/dependency).exists() and dependency not in selected:
                            raise RuntimeError(f'Local dependency is absent from the deployment manifest: {dependency}')
        elif rel.suffix == '.json':
            json.loads((source/rel).read_text())
    backup = runtime/'data/backups'/('install-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f'))
    backup.mkdir(parents=True)
    changed = []
    try:
        for rel in files:
            target = runtime/rel
            # Configuration belongs to the running home, not a source checkout.
            if rel.parts[0] == 'config' and target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.read_bytes() == (source/rel).read_bytes():
                continue
            previous = backup/rel
            if target.exists():
                previous.parent.mkdir(parents=True, exist_ok=True);shutil.copy2(target, previous)
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
                staging = Path(tmp.name)
            shutil.copy2(source/rel, staging)
            staging.replace(target)
            changed.append((target, previous))
        config_path = runtime/'config/sources.json'
        if config_path.exists():
            config = json.loads(config_path.read_text())
            if 'laundry_worker' not in config:
                previous = backup/'config/sources.json'
                previous.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(config_path,previous)
                config['laundry_worker'] = {'enabled':True}
                temp = config_path.with_suffix('.json.tmp')
                temp.write_text(json.dumps(config,indent=2)+'\n');temp.chmod(0o600);temp.replace(config_path)
                changed.append((config_path,previous))
    except BaseException:
        for target, previous in reversed(changed):
            if previous.exists():shutil.copy2(previous, target)
            else:target.unlink(missing_ok=True)
        raise
    result = {'changed':[str(p.relative_to(runtime)) for p,_ in changed], 'backup':str(backup)}
    (backup/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--source-root', type=Path, required=True)
    p.add_argument('--runtime-root', type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(deploy(args.source_root.resolve(),args.runtime_root.resolve())))
