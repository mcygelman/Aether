"""Operator maintenance workflow over the existing exact-build preservation API.

Prepare a reviewable backup scope from installation/registry/owner metadata, then
confirm its exact digest. Preparation is not a backup and grants no recovery or
generation authority. No credential store is opened and no secret is copied.
"""
from copy import deepcopy
import argparse
import json
import os
from pathlib import Path
import re
import uuid

from . import distribution_preservation as preservation

SCHEMA = 'aether-maintenance-backup-plan-1'
MAX_METADATA = 16 * 1024 * 1024


def metadata(path,*,forbidden=()):
    path=preservation.safe(path)
    if (any(path.is_relative_to(root) for root in forbidden)
            or preservation.secret_name(path.as_posix()) or path.stat().st_size>MAX_METADATA):
        raise ValueError('Only bounded non-secret maintenance metadata may be read')
    value=preservation.read_json(path)
    return value,preservation.sha(path.read_bytes())


def _separate(path, roots, label):
    path=preservation.safe(path)
    if any(path.is_relative_to(root) or root.is_relative_to(path) for root in roots):
        raise ValueError(label+' must be separate from application, credentials, source roots and other outputs')
    return path


def discover(app,extra_mounts):
    app,config=preservation.installation(app)
    data=preservation.safe(config['data']); credentials=preservation.safe(config['credentials'])
    _separate(data,[app,credentials],'Installed data root')
    mounts={}; anchors={}; candidates=[]
    anchors[str(app/'installation.json')]=preservation.sha((app/'installation.json').read_bytes())
    if type(extra_mounts) is not dict:
        raise ValueError('Explicit additional mount mapping required')
    for key,path in extra_mounts.items():
        if type(key) is not str or key in {'data','minds'} or not re.fullmatch('[a-z][a-z0-9_-]{0,31}',key):
            raise ValueError('Additional mount IDs must be bounded; data and minds are discovered from the installation')
        mounts[key]=str(preservation.safe(path))
    registry=data/'projects.json'
    if registry.exists():
        value,anchors[str(registry)]=metadata(registry)
        if type(value.get('projects')) is not list:
            raise ValueError('Registered project list required')
        for row in value['projects']:
            candidates.append(preservation.safe(row['root']))
    settings=data/'.aether_control/installation_settings.json'
    if settings.exists():
        value,anchors[str(settings)]=metadata(settings)
        configured=value.get('memory.minds_folder')
        if configured:
            minds=preservation.safe(configured)
            if not minds.is_relative_to(data):
                mounts['minds']=str(minds)
    for name in ('layout.json','staff_homes.json'):
        path=data/name
        if path.exists():
            value,anchors[str(path)]=metadata(path)
            if name=='staff_homes.json' and (value.get('schema')!='aether-staff-homes-location-1.0.0'
                    or preservation.safe(value['user_data_root'])!=data):
                raise ValueError('Staff-home declaration differs from the installation data root')
    # Choose the outermost explicitly registered project roots; never broaden to
    # an unregistered common ancestor. Extra mounts remain explicitly visible.
    for path in sorted(set(candidates),key=lambda p:(len(p.parts),str(p).casefold())):
        if any(path.is_relative_to(root) for root in [data,*map(Path,mounts.values())]):
            continue
        key='project-'+preservation.sha(str(path).encode('utf-8'))[:16]
        if key in mounts and Path(mounts[key])!=path:
            raise ValueError('Generated project mount ID collides with an explicit mount')
        mounts[key]=str(path)
    # This checks disjointness, project coverage, existence, links, and forbidden
    # application/credential overlap BEFORE enumerating any selected root.
    roots=preservation._roots(config,mounts)
    files,dirs,excluded=preservation.inventory(roots)
    if len(files)+len(dirs)>50000:
        raise ValueError('Maintenance plan exceeds 50000 selected entries')
    owners={}
    for name,path in files.items():
        if not name.endswith('/identity/AGENT_ID.json'):
            continue
        value,anchors[str(path)]=metadata(path)
        guid=value.get('guid')
        if type(guid) is not str or str(uuid.UUID(guid))!=guid or guid in owners:
            raise ValueError('Every member needs an unambiguous canonical GUID; no reassignment or guessing')
        owners[guid]=name
    ownership=preservation._owner_mapping(files,owners)
    return app,config,roots,mounts,owners,ownership,anchors,files,dirs,excluded


def prepare_backup(app,destination,*,extra_mounts=None,credential_dependencies=None):
    extras=deepcopy(extra_mounts or {})
    deps=preservation._dependencies(deepcopy(credential_dependencies if credential_dependencies is not None else []))
    app,config,roots,mounts,owners,ownership,anchors,files,dirs,excluded=discover(app,extras)
    destination=_separate(destination,[app,preservation.safe(config['credentials']),*roots.values()],'Backup destination')
    if destination.exists():
        raise ValueError('Backup destination must be new; existing data is never replaced')
    if not deps and any(row['reason']=='credential-content-not-read' for row in excluded):
        raise ValueError('Declare provider/reference/purpose recovery dependencies for excluded protected material; never supply keys')
    plan=dict(schema=SCHEMA,operation='backup',status='REVIEW_REQUIRED_NOT_A_BACKUP',app=str(app),
        destination=str(destination),inputs=dict(extra_mounts=extras),
        selection=dict(mounts=mounts,owners=owners,credential_dependencies=deps),
        roots={k:str(v) for k,v in sorted(roots.items())},metadata_sha256=dict(sorted(anchors.items())),
        ownership=ownership,file_count=len(files),directory_count=len(dirs),
        path_set_sha256=preservation.sha(preservation.encoded(dict(files=sorted(files),directories=dirs))),
        excluded=excluded,compatibility=preservation.compatibility(app),
        boundary='Close installed Aether and external editors. Backup rechecks this scope under the existing maintenance/SQLite boundary.',
        secrets_included=False,content_snapshot_taken=False,
        restore_policy='EXACT_BUILD_ORIGINAL_PATHS_NO_MIGRATION_NO_RETRY_NO_REFUND')
    return dict(plan=plan,sha256=preservation.sha(preservation.encoded(plan)))


def write_plan(review,path):
    p=review['plan']
    if review!=dict(plan=p,sha256=preservation.sha(preservation.encoded(p))):
        raise ValueError('Exact backup plan digest required')
    _,config=preservation.installation(p['app'])
    path=_separate(path,[preservation.safe(p['app']),preservation.safe(config['credentials']),
        preservation.safe(p['destination']),*(preservation.safe(r) for r in p['roots'].values())],'Plan document')
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as stream:
        stream.write(preservation.encoded(review));stream.flush();os.fsync(stream.fileno())
    return dict(plan_path=str(path),plan_sha256=review['sha256'],status=p['status'],
        roots=p['roots'],owners=p['ownership'],files=p['file_count'],
        credential_dependencies=p['selection']['credential_dependencies'],secrets_included=False)


def confirm_backup(app,plan_path,*,expected_plan_sha256):
    app,config=preservation.installation(app)
    review,_=metadata(plan_path,forbidden=(preservation.safe(config['credentials']),))
    if type(review) is not dict or set(review)!={'plan','sha256'}:
        raise ValueError('Exact prepared backup plan document required')
    p=review['plan']
    if (p.get('schema')!=SCHEMA or p.get('operation')!='backup'
            or preservation.safe(p['app'])!=app
            or review['sha256']!=expected_plan_sha256
            or preservation.sha(preservation.encoded(p))!=expected_plan_sha256):
        raise ValueError('Backup plan differs from the independently reviewed digest')
    def recheck():
        fresh=prepare_backup(p['app'],p['destination'],extra_mounts=p['inputs']['extra_mounts'],
            credential_dependencies=p['selection']['credential_dependencies'])
        if fresh!=review:
            raise ValueError('Installation, registered roots, identity mapping or selected paths changed; prepare a new plan')
    recheck()
    result=preservation.backup(p['app'],p['destination'],**p['selection'],_review_selection=recheck)
    return dict(result,plan_sha256=expected_plan_sha256,
        next_action='Retain the backup manifest SHA-256 separately. Restore only to a new staging target on this exact application build.',
        generations_released=False,provider_dependencies_satisfied='NOT_ASSERTED')


def _mounts(values):
    result={}
    for value in values:
        name,separator,path=value.partition('=')
        if not separator or name in result:
            raise ValueError('Use unique --mount id=absolute-path declarations')
        result[name]=str(preservation.safe(path))
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,epilog=(
        'Close Aether before backup/switch. Preparation lists scope only. Retain plan and backup hashes separately. '
        'No secret is copied; credential-provider recovery is separate. Existing destinations are never overwritten.'))
    parser.add_argument('--app',required=True,type=Path,help='Installed application folder; Maintenance.cmd supplies this')
    commands=parser.add_subparsers(dest='operation')
    for name in ('runtime-reference','release-disposition'):
        tool=commands.add_parser(name,help='Offline release evidence only; no export or publication authority')
        tool.add_argument('arguments',nargs=argparse.REMAINDER,help='Tool subcommand and exact input paths; see ReleaseProvenance.md')
    plan=commands.add_parser('prepare-backup',help='Discover exact registered roots and GUID owner mapping; write a new review plan')
    plan.add_argument('--destination',required=True,type=Path)
    plan.add_argument('--plan',required=True,type=Path)
    plan.add_argument('--mount',action='append',default=[],help='Additional explicit id=absolute-path, repeatable')
    plan.add_argument('--credential-dependencies',type=Path,help='JSON list of provider/reference/purpose metadata, never keys')
    create=commands.add_parser('backup',help='Confirm the exact plan; take the maintenance snapshot')
    create.add_argument('--plan',required=True,type=Path);create.add_argument('--plan-sha256',required=True)
    check=commands.add_parser('verify-backup',help='Verify exact manifest, payload, owner mappings and SQLite contents')
    check.add_argument('--archive',required=True,type=Path);check.add_argument('--manifest-sha256',required=True)
    stage=commands.add_parser('restore',help='Verify and restore into a separate new target; does not switch')
    stage.add_argument('--archive',required=True,type=Path);stage.add_argument('--manifest-sha256',required=True)
    stage.add_argument('--target',required=True,type=Path)
    for name in ('switch','finish-switch','preview-replacement'):
        command=commands.add_parser(name);command.add_argument('--target',required=True,type=Path)
    replace=commands.add_parser('replace-under-hold')
    replace.add_argument('--target',required=True,type=Path);replace.add_argument('--preview-sha256',required=True)
    replace.add_argument('--operator',required=True);replace.add_argument('--reason',required=True)
    draft=commands.add_parser('prepare-export',help='Draft exact bundle allowlist plus mandatory privacy scan; never publishes')
    draft.add_argument('--bundle',required=True,type=Path);draft.add_argument('--policy',required=True,type=Path)
    draft.add_argument('--report',required=True,type=Path)
    public=commands.add_parser('export-candidate',help='Confirm reviewed policy digest; copy only after a clean scan; never publishes')
    public.add_argument('--bundle',required=True,type=Path);public.add_argument('--policy',required=True,type=Path)
    public.add_argument('--policy-sha256',required=True);public.add_argument('--report',required=True,type=Path)
    public.add_argument('--destination',required=True,type=Path)
    args=parser.parse_args(argv)
    if args.operation is None:
        parser.print_help();return 0
    try:
        if args.operation in ('runtime-reference','release-disposition'):
            if args.operation=='runtime-reference':
                from .release_provenance import main as release_tool
            else:
                from .release_disposition import main as release_tool
            return release_tool(args.arguments)
        app=preservation.safe(args.app)
        if args.operation=='prepare-backup':
            _,config=preservation.installation(app)
            deps=metadata(args.credential_dependencies,forbidden=(preservation.safe(config['credentials']),))[0] if args.credential_dependencies else []
            result=write_plan(prepare_backup(app,args.destination,extra_mounts=_mounts(args.mount),
                credential_dependencies=deps),args.plan)
        elif args.operation=='backup':
            result=confirm_backup(app,args.plan,expected_plan_sha256=args.plan_sha256)
        elif args.operation=='verify-backup':
            manifest=preservation.verify_backup(args.archive,expected_manifest_sha256=args.manifest_sha256,app=app)
            result=dict(status='VERIFIED_BACKUP_NOT_ACTIVE',manifest_sha256=args.manifest_sha256,
                files=len(manifest['files']),owners=manifest['owners'],obligations=manifest['obligations'],
                credential_dependencies=manifest['credential_dependencies'],generations_released=False)
        elif args.operation=='restore':
            receipt=preservation.restore(app,args.archive,args.target,expected_manifest_sha256=args.manifest_sha256)
            result={k:receipt[k] for k in ('status','target','archive','manifest_sha256')}
        elif args.operation=='preview-replacement':
            p=preservation.preview_replacement(app,args.target)
            result=dict(preview=p,sha256=preservation.sha(preservation.encoded(p)))
        elif args.operation=='replace-under-hold':
            result=preservation.replace_under_hold(app,args.target,expected_preview_sha256=args.preview_sha256,
                operator=args.operator,reason=args.reason)
        elif args.operation in {'prepare-export','export-candidate'}:
            from . import distribution_export as public_export
            _,config=preservation.installation(app)
            credentials=preservation.safe(config['credentials'])
            bundle=_separate(args.bundle,[credentials],'Export source')
            _separate(args.policy,[credentials,bundle],'Export policy')
            _separate(args.report,[credentials,bundle],'Export report')
            if args.operation=='prepare-export':
                result=public_export.prepare_bundle_export(bundle,policy_path=args.policy,report_path=args.report)
            else:
                policy,_=metadata(args.policy,forbidden=(credentials,))
                if preservation.sha(preservation.encoded(policy))!=args.policy_sha256:
                    raise ValueError('Export policy differs from the independently reviewed digest')
                destination=_separate(args.destination,[credentials,bundle],'Export destination')
                result=public_export.export(bundle,destination,policy,report_path=args.report)
        else:
            result=getattr(preservation,args.operation.replace('-','_'))(app,args.target)
        print(json.dumps(result,indent=2,ensure_ascii=False));return 1 if result.get('status')=='FAIL' else 0
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print(json.dumps(dict(status='REFUSED',error=str(exc),automatic_retry=False),indent=2));return 2


if __name__=='__main__':
    raise SystemExit(main())
