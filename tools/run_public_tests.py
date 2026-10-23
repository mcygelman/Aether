"""Run only the declared offline public test tier with temporary state."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PREFIXES = {'bridge': 'AetherAgents/LightweightAgents/Bridge', 'memory': 'AetherMemory'}


def selection(root, group=None, selected=()):
    surface = json.loads((root/'AetherPublicFacing/Release/public_surface.json').read_bytes())
    if selected and group is None:
        raise ValueError('--select requires --group')
    groups = [group] if group else list(PREFIXES)
    result = {}
    for name in groups:
        prefix = PREFIXES[name]+'/'
        tests = [p.removeprefix(prefix) for p in surface['tests'] if p.startswith(prefix)]
        if selected:
            if any(p not in tests for p in selected):
                raise ValueError('--select must name a declared test file relative to its group')
            tests = list(dict.fromkeys(selected))
        if not tests or any(not (root/prefix/p).is_file() for p in tests):
            raise ValueError('Missing or empty public test selection: '+name)
        result[name] = tests
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', choices=list(PREFIXES))
    parser.add_argument('--select', action='append', default=[])
    parser.add_argument('--list', action='store_true', help='Print selection without running tests or creating state')
    args = parser.parse_args(argv)
    try:
        groups = selection(ROOT, args.group, args.select)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    print(json.dumps(groups, indent=2), flush=True)
    if args.list:
        return 0
    bridge = ROOT/PREFIXES['bridge']
    spec = importlib.util.spec_from_file_location('public_support', bridge/'tests/v01_public_support.py')
    support = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(support)
    results = ROOT/'test-results'
    results.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='aether-public-') as temporary:
        scratch = Path(temporary).resolve()
        blocker = scratch/'blocker'; blocker.mkdir()
        (blocker/'sitecustomize.py').write_text(support.SITECUSTOMIZE, encoding='utf-8')
        config = scratch/'pytest.ini'; config.write_text('[pytest]\n', encoding='utf-8')
        base_env = {k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME','DEEPSEEK_API_KEY')
                    and not k.startswith('AETHER_')}
        base_env.update(PYTHONDONTWRITEBYTECODE='1', AETHER_PUBLIC_TEST_TIER='1',
                        TEMP=str(scratch), TMP=str(scratch), TMPDIR=str(scratch))
        status = 0
        for name, tests in groups.items():
            cwd = ROOT/PREFIXES[name]
            paths = [blocker, cwd, ROOT]
            if name == 'bridge':
                paths.append(ROOT/'AetherMemory')
            env = dict(base_env, PYTHONPATH=os.pathsep.join(map(str,paths)))
            command = [sys.executable, '-B', '-m', 'pytest', *tests, '-q', '-p', 'no:cacheprovider',
                       '-c', str(config), '--rootdir', str(cwd), '--basetemp', str(scratch/name),
                       '--junitxml', str(results/(name+'.xml'))]
            result = subprocess.run(command, cwd=cwd, env=env)
            if result.returncode:
                status = result.returncode
        return status


if __name__ == '__main__':
    raise SystemExit(main())
