"""Reproducible runtime stdlib container: same input bytes give the same archive bytes.

Synthetic Lib trees for metadata and content sensitivity; the real interpreter's Lib for
import behaviour. Builds into disposable folders only; no candidate is built.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

from aether.operations import distribution as dist


def lib_tree(root):
    files = {'a.py': 'A = 1\n', 'pkg/__init__.py': '', 'pkg/b.py': 'B = 2\n',
             'test/skip.py': 'raise SystemExit\n', 'pkg/tests/skip.py': '', 'site-packages/skip.py': '',
             '__pycache__/skip.py': '', 'notes.txt': 'not python\n'}
    for name, text in files.items():
        path = root/'Lib'/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text, encoding='utf-8')
    return root


def build(home, target):
    return dist.write_stdlib_zip(target, dist.stdlib_members(home))


def test_selection_order_and_fixed_metadata(tmp_path):
    home = lib_tree(tmp_path/'h')
    result = build(home, tmp_path/'one.zip')
    with zipfile.ZipFile(tmp_path/'one.zip') as archive:
        infos = archive.infolist()
    assert [i.filename for i in infos] == ['a.py', 'pkg/__init__.py', 'pkg/b.py']   # Excluded folders and non-.py stay out.
    assert {i.date_time for i in infos} == {(1980, 1, 1, 0, 0, 0)} and {i.compress_type for i in infos} == {zipfile.ZIP_DEFLATED}
    assert {i.external_attr for i in infos} == {0o100644 << 16} and {i.create_system for i in infos} == {0}
    assert [r['name'] for r in result['members']] == [i.filename for i in infos] and result['member_count'] == 3
    assert result['recipe']['python'] == sys.version.split()[0] and result['recipe']['zlib']


def test_changed_mtimes_keep_bytes_and_changed_content_does_not(tmp_path):
    home = lib_tree(tmp_path/'h')
    first = build(home, tmp_path/'one.zip')
    for path in (home/'Lib').rglob('*.py'):
        os.utime(path, (1_000_000_000, 1_700_000_000))
    second = build(home, tmp_path/'two.zip')
    assert (tmp_path/'one.zip').read_bytes() == (tmp_path/'two.zip').read_bytes()
    assert first == second
    (home/'Lib/pkg/b.py').write_text('B = 3\n', encoding='utf-8')
    third = build(home, tmp_path/'three.zip')
    assert third['sha256'] != first['sha256'] and third['members_sha256'] != first['members_sha256']
    assert third['recipe_sha256'] == first['recipe_sha256']   # Same recipe, different inputs.


def test_real_stdlib_is_reproducible_and_imports_from_the_container(tmp_path_factory):
    root = tmp_path_factory.mktemp('rt')
    home = Path(sys.base_prefix)
    first = build(home, root/'one.zip'); second = build(home, root/'two.zip')
    assert (root/'one.zip').read_bytes() == (root/'two.zip').read_bytes() and first['sha256'] == second['sha256']
    names = {r['name'] for r in first['members']}
    assert {'json/__init__.py', 'http/server.py', 'sqlite3/__init__.py', 'zipfile.py'} <= names
    assert not any(n.split('/')[0] in dist.STDLIB_EXCLUDED for n in names)
    probe = ("import importlib,json,sys\n"
             "sys.path[:]=[sys.argv[1],sys.argv[2]]\n"
             "out={}\n"
             "for m in ('json','http.server','sqlite3','zipfile','email.message','urllib.request','logging.handlers',"
             "'argparse','uuid','concurrent.futures','xml.etree.ElementTree','asyncio','ssl','hashlib','html.parser'):\n"
             "    before=m in sys.modules; mod=importlib.import_module(m)\n"
             "    out[m]=None if before else getattr(mod,'__file__',None)\n"
             "print(json.dumps(out))\n")
    done = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', probe, str(root/'one.zip'), str(home/'DLLs')],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    loaded = json.loads(done.stdout)
    fresh = {m: f for m, f in loaded.items() if f is not None and f.endswith('.py')}
    assert fresh and all(f.startswith(str(root/'one.zip')) for f in fresh.values()), fresh
