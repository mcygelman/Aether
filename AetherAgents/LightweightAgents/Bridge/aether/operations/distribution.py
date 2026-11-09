"""MVP-09 directory bundle: offline build/install, immutable app, explicit data.

Builder use: python -m aether.operations.distribution build --output ABSOLUTE_FOLDER
Recipient use: double-click Install.cmd, then Aether.cmd in the installed folder.
This preview supplies setup/campaign creation and recoverable payload maintenance.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import zipfile

SCHEMA = 'aether-directory-distribution-1'
DEPENDENCIES = {'Jinja2': '3.1.6', 'MarkupSafe': '2.1.3', 'pypdf': '6.21.2', 'typing_extensions': '4.15.0'}


def direct(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('Choose an absolute installation or data folder')
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except (FileNotFoundError, NotADirectoryError):
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError(f'Installation and data paths must not contain links or junctions: {part}')
    return path.resolve()


def write_json(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def copy_file(source, target):
    source = direct(source)
    if source.name.lower() == '.sealbox_key' or source.suffix.lower() == '.key':
        raise ValueError('Credential material cannot enter a distribution')
    if not source.is_file():
        raise ValueError('Bundle input is not a regular file')
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('xb') as stream:
        stream.write(source.read_bytes())


def tree_files(root, suffixes):
    for path in sorted(root.rglob('*')):
        if '__pycache__' in path.parts:
            continue
        if path.is_file() and path.suffix.lower() in suffixes:
            direct(path)
            yield path


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and p.name != 'bundle.json'}


def agent_editor_assets(output):
    """Copy both editions' fixed Editor resources, including public authoring."""
    from aether.agent_editor.http import ASSETS, WEB
    from aether.backends.gui_assets import PUBLIC_EDITOR_ASSETS
    for relative in sorted({entry[0] for entry in (*ASSETS.values(), *PUBLIC_EDITOR_ASSETS.values())}):
        copy_file(WEB / relative, output / 'code/aether/agent_editor/web' / relative)


PUBLIC_CODE_REPLACEMENTS = {
    'aether/runtime/orchestration/charter_run.py': 'aether/operations/public_charter.py',
}


def application_sources(output, *, bridge=None):
    """Package import-compatible public code without the resident charter pilot."""
    from aether.paths import BRIDGE_ROOT
    bridge = Path(bridge or BRIDGE_ROOT)
    for package in ('aether', 'aether_bridge'):
        for source in tree_files(bridge / package, {'.py'}):
            relative = source.relative_to(bridge).as_posix()
            replacement = PUBLIC_CODE_REPLACEMENTS.get(relative)
            copy_file(bridge / replacement if replacement else source, output / 'code' / relative)


# Generic workflow limits and the disabled historical-reconciliation policy.
# Developer runtime settings never ship; the Minder file stays generated and unconfigured.
WORKFLOW_DEFAULTS = ('planning_defaults.json', 'minder_assessment_defaults.json',
                     'minder_reconciliation_defaults.json', 'historical_reconciliation_defaults.json')
QUALIFICATION_DEFAULTS = ('minder_qualification_defaults.json', 'minder_hybrid_qualification_defaults.json')


def qualification_resources(output, *, bridge=None, repository=None):
    """Ship each qualification defaults file with the exact sources it names.

    Sources are repository-relative; the installed resources root resolves them at
    the same relative path, so preparation snapshots identical bytes. A source that
    is absolute, escapes the tree, or lies on a private path refuses the build.
    """
    from aether.paths import BRIDGE_ROOT, REPOSITORY_ROOT
    from .distribution_export import private_path
    bridge, repository = Path(bridge or BRIDGE_ROOT), Path(repository or REPOSITORY_ROOT)
    for name in QUALIFICATION_DEFAULTS:
        sources = json.loads((bridge / name).read_bytes()).get('sources')
        if type(sources) is not dict or not sources:
            raise ValueError('Qualification defaults need an explicit source mapping: ' + name)
        for relative in sorted(set(sources.values())):
            parts = Path(relative).parts if type(relative) is str else ()
            if (not parts or Path(relative).is_absolute() or Path(relative).drive or '..' in parts
                    or '\\' in relative or private_path(relative)):
                raise ValueError('Qualification source must be a public repository-relative file: ' + name)
            target = output / 'resources' / relative
            if target.exists():
                if target.read_bytes() != direct(repository / relative).read_bytes():
                    raise ValueError('Shared resource bytes differ between declarations: ' + relative)
                continue
            copy_file(repository / relative, target)
        copy_file(bridge / name, output / 'code' / name)


# Pure stdlib only: optional tools, tests, user site and package installers stay out.
STDLIB_EXCLUDED = frozenset({'site-packages', 'test', 'tests', 'idlelib', 'tkinter', 'ensurepip',
                             'venv', 'turtledemo', '__pycache__'})
# Identical input bytes and the same zlib give identical archive bytes: member order,
# timestamps, attributes and compression are explicit, never taken from the filesystem.
# Reproducibility is not upstream authenticity or permission to export.
STDLIB_ZIP_RECIPE = dict(schema='aether-stdlib-zip-recipe-1', order='sorted POSIX member name',
                         date_time=[1980, 1, 1, 0, 0, 0], compress_type='ZIP_DEFLATED', compresslevel=6,
                         create_system=0, external_attr=0o100644 << 16, comment='')


def stdlib_members(python_home):
    """Map each archived stdlib member name to its exact source file under Lib/."""
    lib = Path(python_home) / 'Lib'
    members = {}
    for folder, directories, names in os.walk(lib):
        # Prune before descent: a development interpreter may have enormous
        # model/science packages which are not build inputs, even for scanning.
        directories[:] = sorted(n for n in directories if n not in STDLIB_EXCLUDED)
        for name in names:
            if name.endswith('.py'):
                source = direct(Path(folder) / name)
                members[source.relative_to(lib).as_posix()] = source
    return dict(sorted(members.items()))


def write_stdlib_zip(target, members):
    """Write the runtime container by the fixed recipe; return its member/input mapping."""
    import zlib
    rows = []
    with zipfile.ZipFile(target, 'x') as archive:
        for name, source in sorted(members.items()):
            raw = Path(source).read_bytes()
            info = zipfile.ZipInfo(name, date_time=tuple(STDLIB_ZIP_RECIPE['date_time']))
            info.create_system = STDLIB_ZIP_RECIPE['create_system']
            info.external_attr = STDLIB_ZIP_RECIPE['external_attr']
            archive.writestr(info, raw, compress_type=zipfile.ZIP_DEFLATED,
                             compresslevel=STDLIB_ZIP_RECIPE['compresslevel'])
            rows.append(dict(name=name, sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw)))
    recipe = dict(STDLIB_ZIP_RECIPE, python=sys.version.split()[0], zlib=zlib.ZLIB_VERSION,
                  zlib_runtime=zlib.ZLIB_RUNTIME_VERSION)
    canonical = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return dict(sha256=hashlib.sha256(Path(target).read_bytes()).hexdigest(), member_count=len(rows),
                recipe=recipe, recipe_sha256=canonical(recipe), members_sha256=canonical(rows), members=rows)


def bundle_runtime(output):
    """The existing Windows runtime recipe, shared by private and public editions."""
    if os.name != 'nt' or sys.version_info[:2] != (3, 10):
        raise ValueError('Build with Windows Python 3.10')
    for name, version in DEPENDENCIES.items():
        if importlib.metadata.version(name) != version:
            raise ValueError(f'Build requires {name}=={version}')
    runtime = output / 'runtime'
    runtime.mkdir()
    python_home = Path(sys.base_prefix)
    for source in [python_home / 'python.exe', python_home / 'LICENSE.txt', *python_home.glob('*.dll')]:
        copy_file(source, runtime / source.name)
    for source in tree_files(python_home / 'DLLs', {'.pyd', '.dll'}):
        copy_file(source, runtime / 'DLLs' / source.name)
    stdlib = write_stdlib_zip(runtime / 'python310.zip', stdlib_members(python_home))
    for name in DEPENDENCIES:
        distribution = importlib.metadata.distribution(name)
        for item in distribution.files or ():
            relative = Path(item)
            if relative.is_absolute() or '..' in relative.parts or '__pycache__' in relative.parts:
                continue
            if relative.suffix == '.pyc':
                continue
            copy_file(Path(distribution.locate_file(item)), runtime / 'Lib/site-packages' / relative)
    from .release_provenance import PTH_BYTES
    (runtime / 'python310._pth').write_bytes(PTH_BYTES)
    return stdlib


def tracker_assets(output):
    """Copy the exact static files each project's roadmap view serves from its package."""
    from aether.paths import BRIDGE_ROOT
    from aether.roadmaps.tracker.tracker_http import ROOT, STATIC
    for name in sorted(set(STATIC.values())):
        copy_file(ROOT / name, output / 'code' / (ROOT / name).relative_to(BRIDGE_ROOT))



def public_demo_assets(output, repository):
    """Include only the exact operator-approved public practice pack, never homes."""
    from . import public_demo_fixtures as fixtures
    from .public_demo_preparation import PACK_LOCATION
    from . import public_demo_catalog as demo_catalog
    expected = 'f08a712fd6ad91e1f348d777029148ae25f68a29602489dd1c14963f1b9358ab'
    pack = repository / 'AetherPlanAndAnalysis/Specifications/MVP/HARBOR_GUIDE_STAFF_PACK_20261012'
    pack_manifest, _, payload = fixtures._pack(pack, expected)
    proposal = fixtures._json(fixtures._read(repository /
        'AetherPlanAndAnalysis/Specifications/MVP/HARBOR_GUIDE_CATALOG_ENTRY_20261012.json'))
    original_prefix = 'AetherPlanAndAnalysis/Specifications/MVP/HARBOR_GUIDE_STAFF_PACK_20261012'
    if (proposal.get('schema') != demo_catalog.CATALOG
            or proposal.get('templates') != [demo_catalog.entry(pack_manifest, original_prefix)]):
        raise ValueError('Harbor catalog proposal changed; review its tools/actions before rebuilding')
    for relative in demo_catalog.DEPENDENCIES:
        raw = fixtures._read(repository/relative)
        expected_dependency = pack_manifest['shared_dependencies_by_reviewed_hash'][relative]
        if (fixtures.digest(raw) != expected_dependency['sha256']
                or len(raw) != expected_dependency['bytes']):
            raise ValueError('Harbor shared dependency differs from the reviewed pack: '+relative)
        destination = output/'resources'/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream:
            stream.write(raw)
    manifest = fixtures._read(pack/'MANIFEST.json')
    if fixtures.digest(manifest) != expected:
        raise ValueError('Public demo manifest changed during packaging')
    for name, content in {**payload, 'MANIFEST.json': manifest}.items():
        target = output / PACK_LOCATION / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(content)
    write_json(output/demo_catalog.FRAGMENT_LOCATION, dict(schema=demo_catalog.CATALOG,
        templates=[demo_catalog.entry(pack_manifest, PACK_LOCATION)]))
    (output/'Demo.cmd').write_text(
        '@echo off\n"%~dp0runtime\\python.exe" -I -B -m aether.operations.public_demo_entry --app "%~dp0." %*\n'
        'set "aether_exit=%errorlevel%"\nif "%~1"=="" pause\n'
        'if not "%aether_exit%"=="0" pause\nexit /b %aether_exit%\n', encoding='ascii')
    (output/'Demo.txt').write_text(
        'Harbor Guide offline fixture preparation\n'
        'Run Demo.cmd from the INSTALLED application folder. This does not start a model or admit a campaign.\n'
        'A staff member is permanent: releasing its claim never deletes or reassigns it.\n'
        'CATALOG: close Aether and external catalog editors first.\n'
        'catalog-preview --request-id <catalog-operation-uuid>\n'
        'Review the proposed read_file/write_file/list_dir tools and read/draft/write actions.\n'
        'catalog-confirm --request-id <same-catalog-operation-uuid> --review-sha256 <catalog-review-sha256>\n'
        'Confirmation adds that declaration for future staffing; it does not admit or run any campaign.\n'
        'Existing catalog entries and the configured source root remain in place. Restart Aether after confirmation.\n'
        'Use a DIFFERENT retained UUID for member preparation below.\n\n'
        'Choose and retain one UUID for the operation; reuse it after interruptions.\n\n'
        '1. prepare --request-id <operation-uuid> --hire --manifest-sha256 '+expected+'\n'
        '   This explicitly hires a new HarborGuide member and holds its preparation claim. It writes no practice notes.\n'
        '   To select an existing unused member, replace --hire with --member-guid <guid>.\n'
        '2. Read the returned review and retain its sha256. Confirm the member GUID and the two labelled notes.\n'
        '3. confirm --request-id <same-operation-uuid> --review-sha256 <reviewed-sha256>\n'
        '   Retain the returned receipt and receipt_sha256 in private evidence. The claim remains held.\n'
        '4. release --request-id <same-operation-uuid>\n'
        '   The member and all fixture records remain. Only this preparation claim is released.\n'
        '5. status --request-id <same-operation-uuid>\n'
        '   Shows retained operation history; it does not verify current memory or current admission.\n\n'
        'If interrupted, repeat the original command and IDs; never hire again under a new UUID to repair an old hire.\n'
        'If unfinished preparation must stop: release --request-id <same-operation-uuid> --preserve-incomplete\n'
        'This keeps all partial records. A new operation may select the same GUID and resume an intact original intent.\n'
        'Truncated or conflicting bytes require inspection; nothing is overwritten or silently repaired.\n'
        'Host control records may contain claim tokens: do not copy that directory into member memory or public evidence.\n'
        'Catalog/hydration, campaign admission and T03/T07 validation are separate steps; seeding proves none of them.\n',
        encoding='utf-8')


def build(output):
    if os.name != 'nt' or sys.version_info[:2] != (3, 10):
        raise ValueError('Build this preview with Windows Python 3.10')
    for name, version in DEPENDENCIES.items():
        if importlib.metadata.version(name) != version:
            raise ValueError(f'Build requires {name}=={version}')
    from aether.paths import BRIDGE_ROOT, REPOSITORY_ROOT
    source_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPOSITORY_ROOT, text=True).strip()
    source_dirty = bool(subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=normal'],
                                               cwd=REPOSITORY_ROOT, text=True).strip())
    output = direct(output)
    output.mkdir(parents=True, exist_ok=False)
    # Only code and declared immutable resources; never copy Bridge state/configs,
    # resident profiles, cartridges, journals, credentials or arbitrary repo trees.
    application_sources(output)
    copy_file(BRIDGE_ROOT / 'docs/CAMPAIGN_COMPARISON.md', output / 'CampaignComparison.md')
    copy_file(BRIDGE_ROOT / 'docs/RELEASE_PROVENANCE.md', output / 'ReleaseProvenance.md')
    copy_file(BRIDGE_ROOT / 'docs/RELEASE_PROVENANCE_INTEGRATION.md', output / 'ReleaseProvenanceIntegration.md')
    copy_file(BRIDGE_ROOT / 'docs/RELEASE_DISPOSITION_INPUTS.md', output / 'ReleaseDispositionInputs.md')
    for source in tree_files(BRIDGE_ROOT / 'aether_bridge/web', {'.html', '.js', '.css', '.svg', '.png', '.ico'}):
        copy_file(source, output / 'code' / source.relative_to(BRIDGE_ROOT))
    agent_editor_assets(output)
    tracker_assets(output)
    # Planning, assessment and reconciliation refuse to prepare without their defaults.
    for relative in ('aether/modules/registry.json', 'aether_bridge/token_count_policy.defaults.json', *WORKFLOW_DEFAULTS):
        copy_file(BRIDGE_ROOT / relative, output / 'code' / relative)
    # The legacy settings handler locates this static page at BRIDGE_ROOT.
    copy_file(BRIDGE_ROOT / 'model_context_settings.html', output / 'code/model_context_settings.html')
    for source in tree_files(REPOSITORY_ROOT / 'AetherMemory/memory_fabric', {'.py', '.sql'}):
        copy_file(source, output / 'code/memory_fabric' / source.relative_to(REPOSITORY_ROOT / 'AetherMemory/memory_fabric'))
    for relative in ('AetherLexicon/Format_Web/WMC_Catalog.json', 'AetherLexicon/Format_Web/WMC_BOUNDED_PROJECT.html'):
        copy_file(REPOSITORY_ROOT / relative, output / 'resources' / relative)
    # Explicitly unconfigured: no real Minder identity/default configuration copied.
    write_json(output / 'code/minder_defaults.json', {'schema': 'aether-distribution-unconfigured-reviewer-1'})
    (output / 'code/heavyweight_catalog.json').write_text(
        '{"schema_version":"aether-heavyweight-catalog-1.0.0","mode":"public","profiles":[]}\n', encoding='utf-8')
    stdlib = bundle_runtime(output)
    (output / 'aether.py').write_text(
        'from aether.operations.distribution import main\nif __name__ == "__main__":\n    raise SystemExit(main())\n', encoding='ascii')
    for name, command in (('Install.cmd', 'install --open-folder'), ('Aether.cmd', 'serve --governed'),
                          ('Uninstall.cmd', 'uninstall'), ('Qualify.cmd', 'qualify')):
        pause = 'pause' if name in ('Aether.cmd', 'Uninstall.cmd') else 'if not "%aether_exit%"=="0" pause'
        (output / name).write_text('@echo off\n"%~dp0runtime\\python.exe" -I -B "%~dp0aether.py" ' + command +
            ' %*\nset "aether_exit=%errorlevel%"\n' + pause + '\nexit /b %aether_exit%\n', encoding='ascii')
    (output / 'Maintenance.cmd').write_text(
        '@echo off\n"%~dp0runtime\\python.exe" -I -B -m aether.operations.distribution_maintenance --app "%~dp0." %*\n'
        'set "aether_exit=%errorlevel%"\nif "%~1"=="" pause\n'
        'if not "%aether_exit%"=="0" pause\nexit /b %aether_exit%\n',encoding='ascii')
    (output / 'Maintenance.txt').write_text(
        'Aether private backup and restore\n'
        'Run Maintenance.cmd from the INSTALLED application folder. Without arguments it lists commands.\n'
        'Close Aether and external editors before backup or switching restored data.\n\n'
        '1. prepare-backup --destination <new-private-backup> --plan <new-plan.json>\n'
        '   Optional: --mount id=<absolute-extra-root> and --credential-dependencies <metadata.json>.\n'
        '   Registered project roots, configured minds and GUID owner mappings are discovered.\n'
        '   The plan contains private paths/owner identifiers; keep it private. No content snapshot exists yet.\n'
        '2. Inspect the plan, its roots, owners and credential dependencies. Retain its plan_sha256.\n'
        '3. backup --plan <same-plan.json> --plan-sha256 <reviewed-plan-hash>\n'
        '   Scope is rechecked under maintenance locks. Retain the returned manifest_sha256 separately.\n'
        '4. verify-backup --archive <backup> --manifest-sha256 <retained-backup-hash>\n'
        '5. restore --archive <backup> --manifest-sha256 <retained-backup-hash> --target <new-separate-staging>\n'
        '   Staging is verified but not active. Only the exact same application build is supported.\n'
        '6. switch --target <verified-staging> activates only unchanged original data.\n'
        '   finish-switch resumes an interrupted switch; prior roots remain in staging/previous.\n\n'
        'Changed/damaged/missing original data: use preview-replacement, inspect its exact hash,\n'
        'then replace-under-hold with --preview-sha256, --operator and --reason.\n'
        'The installation remains held for separate reconciliation; no supported hold-clearing command exists here.\n'
        'Never delete a hold marker to resume a campaign. No retry, refund, generation release or migration is implied.\n\n'
        'Credential dependency metadata is a JSON list of provider/reference/purpose objects.\n'
        'Do not supply passwords, tokens or key bytes. Providers: environment, windows-credential-manager,\n'
        'external-key-provider, file-provider. Secret material is excluded; restore may require re-entry\n'
        'or the original provider supported recovery. Public export is a separate reviewed workflow.\n'
        'Offline release tools: runtime-reference <inspect|draft|verify> and release-disposition <draft|check|statement>.\n'
        'See ReleaseProvenance.md, ReleaseProvenanceIntegration.md and ReleaseDispositionInputs.md for exact arguments and remaining holds.\n'
        'These tools do not authorize export. export-candidate still requires a clean raw scan.\n'
        'All paths must be absolute. Backup, plan and staging destinations must be new and separate.\n',encoding='utf-8')
    with (output/'Maintenance.txt').open('a',encoding='utf-8') as guide:
        guide.write('\nPublic candidate export (separate from private backups)\n'
            'prepare-export --bundle <extracted-candidate> --policy <new-policy.json> --report <new-scan.json>\n'
            'This drafts an exact file and nested-archive allowlist, then runs the unchanged privacy scan.\n'
            'Review every entry and finding. FAIL blocks export; known private/key paths remain unread.\n'
            'After a clean scan and policy review, retain policy_sha256, then use:\n'
            'export-candidate --bundle <same-candidate> --policy <reviewed-policy.json>\n'
            '  --policy-sha256 <reviewed-hash> --report <new-report.json> --destination <new-export-folder>\n'
            'The result is a scanned local candidate only. No public release or publication is performed.\n')
    (output / 'README.txt').write_text(
        'Aether offline preview\nDouble-click Install.cmd, then Aether.cmd in the installed folder.\n'
        'Mission Control opens in your browser. Closing its console stops this preview.\n'
        'No model is contacted at startup. Configure a deployment explicitly in Settings.\n'
        'Run Qualify.cmd from this extracted bundle for the standard-user clean-VM check.\n'
        'Close Aether, then run Uninstall.cmd from this extracted bundle to remove the app.\n'
        'Uninstall checks registered workspace locations and preserves data and credentials.\n'
        'Interrupted installs are repaired by rerunning Install.cmd with the same paths.\n'
        'For private backup/restore, close Aether and read Maintenance.txt in the installed folder.\n'
        'Public agent onboarding, signed releases and data migrations remain unqualified.\n', encoding='utf-8')
    public_demo_assets(output, REPOSITORY_ROOT)
    from .public_demo_workspaces import bundle_resources
    bundle_resources(output, REPOSITORY_ROOT)
    qualification_resources(output)   # After the demo pack: shared resources must match byte for byte.
    manifest = dict(schema=SCHEMA, python=sys.version.split()[0], dependencies=DEPENDENCIES,
                    release_status='LOCAL_PREVIEW_NOT_PUBLIC_RELEASE', source_commit=source_commit,
                    uncommitted_changes=source_dirty, files=hashes(output))
    write_json(output / 'bundle.json', manifest)
    return dict(bundle=str(output), files=len(manifest['files']), provider_calls=0, stdlib_archive=stdlib)


def verify(bundle):
    bundle = direct(bundle)
    manifest = json.loads((bundle / 'bundle.json').read_bytes())
    if manifest.get('schema') != SCHEMA or not isinstance(manifest.get('files'), dict):
        raise ValueError('Unsupported distribution manifest')
    for relative, expected in manifest['files'].items():
        path = Path(relative)
        if (path.is_absolute() or '..' in path.parts or ':' in relative
                or path.suffix.lower() == '.key' or path.name.lower() == '.sealbox_key'):
            raise ValueError('Unsafe distribution manifest path')
        target = direct(bundle / path)
        if not target.is_relative_to(bundle) or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise ValueError(f'Distribution integrity failed: {relative}')
    return manifest


def layout(app, data, credentials, *, explicit_campaign_folders=False):
    app, data, credentials = (direct(p) for p in (app, data, credentials))
    for a, b in ((app, data), (app, credentials), (data, credentials)):
        if a.is_relative_to(b) or b.is_relative_to(a):
            raise ValueError('Application, user data and credential folders must be separate')
    # Guarantee room even when this builder happens to have long paths enabled.
    from aether.roadmaps.project_setup import DEEPEST_RECORD_CHARS, WINDOWS_MAX_PATH
    longest = data / 'p' / ('c-' + '0' * 12)
    if not explicit_campaign_folders and len(str(longest)) + DEEPEST_RECORD_CHARS >= WINDOWS_MAX_PATH:
        raise ValueError('Choose a shorter data folder (for example your user profile\\.a). '
                         'Managed campaigns need a parent path of at most 54 characters. '
                         'Alternatively choose explicit campaign folders at installation.')
    return dict(schema=SCHEMA, application=str(app), data=str(data), credentials=str(credentials),
                campaign_folders='explicit' if explicit_campaign_folders else 'managed',
                managed_campaign_parent=str(data / 'p'))


def install(bundle, destination, data, credentials, *, explicit_campaign_folders=False):
    from .distribution_install import install as transaction
    return transaction(bundle, destination, data, credentials,
                       explicit_campaign_folders=explicit_campaign_folders)


def default_roots():
    local = Path(os.environ.get('LOCALAPPDATA', Path.home()))
    return local / 'Programs/Aether/preview', Path.home() / '.a', Path.home() / '.ac'


def serve(app, *, governed, port=0, browser=True, stop_on_stdin=False):
    # Must precede the first aether.paths import (also used by layout's guard).
    app = direct(app)
    config = json.loads((app / 'installation.json').read_bytes())
    os.environ['AETHER_INSTALL_DATA_ROOT'] = str(direct(config['data']))
    os.environ['AETHER_INSTALL_RESOURCES_ROOT'] = str(direct(app / 'resources'))
    os.environ['AETHER_INSTALL_CREDENTIALS_ROOT'] = str(direct(config['credentials']))
    from aether.operations.installed_app import serve_installed
    return serve_installed(app, governed=governed, port=port, browser=browser, stop_on_stdin=stop_on_stdin)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    builder = commands.add_parser('build'); builder.add_argument('--output', required=True, type=Path)
    installer = commands.add_parser('install')
    app, data, credentials = default_roots()
    installer.add_argument('--destination', type=Path, default=app)
    installer.add_argument('--data-root', type=Path, default=data)
    installer.add_argument('--credentials-root', type=Path, default=credentials)
    installer.add_argument('--explicit-campaign-folders', action='store_true')
    installer.add_argument('--open-folder', action='store_true')
    installer.add_argument('--json', action='store_true', help='Machine-readable installation receipt')
    launcher = commands.add_parser('serve')
    launcher.add_argument('--governed', action='store_true', required=True)
    launcher.add_argument('--port', type=int, default=0)
    launcher.add_argument('--no-browser', action='store_true')
    launcher.add_argument('--stop-on-stdin', action='store_true', help=argparse.SUPPRESS)
    remover = commands.add_parser('uninstall')
    remover.add_argument('--destination', type=Path, default=app)
    qualification = commands.add_parser('qualify')
    qualification.add_argument('--verdict', type=Path,
        help='JSON result (default: aether-qualification.json beside the extracted bundle)')
    args = parser.parse_args(argv)
    try:
        if args.command == 'build':
            value = build(args.output)
        else:
            bundle = Path(sys.argv[0]).resolve().parent
            if args.command == 'install':
                value = install(bundle, args.destination, args.data_root, args.credentials_root,
                                explicit_campaign_folders=args.explicit_campaign_folders)
            elif args.command == 'uninstall':
                from .distribution_install import uninstall
                value = uninstall(args.destination)
            elif args.command == 'qualify':
                from .distribution_qualification import qualify
                verdict = args.verdict or bundle.parent / 'aether-qualification.json'
                value = qualify(bundle, verdict)
                print(json.dumps(dict(status=value['status'], verdict=str(verdict),
                    failed_checks=[name for name, check in value['checks'].items() if check['status'] == 'FAIL'])), flush=True)
                return 0 if value['status'] == 'PASS' else 1
            else:
                return serve(bundle, governed=args.governed, port=args.port,
                             browser=not args.no_browser, stop_on_stdin=args.stop_on_stdin)
        if args.command == 'install' and not args.json:
            print('Aether is installed. Open Aether.cmd in:\n' + value['installed'])
            print('Your separate data folder is:\n' + value['data'])
            if args.open_folder:
                os.startfile(value['installed'])
        else:
            print(json.dumps(value), flush=True)
        return 0
    except (OSError, ValueError) as error:
        print(f'Aether: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
