"""Own fresh specialist homes and retry receipts; never overwrite homes, wake models or modify resident memories."""
import hashlib
import json
from pathlib import Path
import stat
import uuid

from aether.integrity.sealbox import atomic_write_bytes
from aether.config.model_settings_store import writer_lock
from . import specialist_catalog
from .participants import Participant


def direct(path):
    path = Path(path).absolute()
    for parent in (path,*path.parents):
        try: info=parent.lstat()
        except FileNotFoundError: continue
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise ValueError('Linked specialist installation paths are unavailable.')
    return path


def _write(path, record):
    direct(path)
    atomic_write_bytes(path,(json.dumps(record,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))


def connections(settings):
    profiles=settings.read()['document'].get('profiles') or {}
    return sorted(name for name,p in profiles.items() if isinstance(p,dict) and name!='ultralight'
                  and (p.get('requestedApiIdentifier') or p.get('model')) not in (None,'','not-configured') and p.get('endpoint'))


def install(registry, share, settings, body):
    """One explicit install request, safely repeatable after a lost HTTP response.

    Partial authoring is retained and reported, never overwritten. Registration
    can be retried after a completely authored home. Completed requests are inert.
    """
    rid=body.get('request_id')
    try:
        if str(uuid.UUID(rid))!=rid: raise ValueError()
    except (ValueError,TypeError,AttributeError):
        raise ValueError('Use a canonical request UUID.') from None
    selection=body.get('templates')
    if not isinstance(selection,list) or not 1<=len(selection)<=29 or not all(isinstance(x,str) for x in selection) or len(set(selection))!=len(selection):
        raise ValueError('Choose one to 29 distinct template ids.')
    doc=specialist_catalog.load()
    if body.get('revision')!=doc['revision']:
        raise ValueError('The shipped catalog changed. Refresh and review the selection.')
    templates=[specialist_catalog.template(x,doc) for x in sorted(selection)]
    deployment=body.get('deployment') or 'unconfigured'
    if deployment!='unconfigured' and deployment not in connections(settings):
        raise ValueError('Choose a saved parent model connection or configure one later.')
    request=dict(templates=sorted(selection),revision=doc['revision'],deployment=deployment)
    return install_resolved(registry,share,rid,request,templates)


def install_resolved(registry,share,rid,request,templates,family='specialist-installs'):
    """Install only server-resolved, version-pinned contracts; never accept raw HTTP contracts."""
    folder=direct(registry.path.parent/family);folder.mkdir(parents=True,exist_ok=True)
    with registry.lock,writer_lock(direct(folder/(rid+'.lock'))):
        return _install_resolved(registry,share,rid,request,templates,family)


def _install_resolved(registry,share,rid,request,templates,family):
    deployment=request['deployment']
    with registry.lock:
        if not registry.default_folder:
            raise ValueError('Choose an agents folder before adding specialists.')
        receipts=direct(registry.path.parent/family);receipts.mkdir(parents=True,exist_ok=True)
        receipt_path=direct(receipts/(rid+'.json'))
        if receipt_path.exists():
            receipt=json.loads(receipt_path.read_text(encoding='utf-8'))
            if receipt['request']!=request:
                raise ValueError('This request id belongs to a different selection.')
            if receipt['complete']:
                return receipt
        else:
            folder=direct(Path(registry.default_folder)/'specialists')
            receipt=dict(request_id=rid,request=request,complete=False,model_calls=0,agents=[
                dict(template=t['id'],id='specialist-'+uuid.uuid5(uuid.UUID(rid),t['id']).hex,
                     home=str(folder/t.get('home_key',t['id']+'-'+rid[:12])),phase='planned') for t in templates])
            _write(receipt_path,receipt)
        for item,row in zip(receipt['agents'],templates):
            home=direct(item['home'])
            if item['phase']=='registered': continue
            if item['phase']=='authoring':
                raise ValueError('Interrupted authoring retained at '+str(home)+'. Inspect that incomplete home before creating another request; it was not overwritten.')
            if item['phase']=='planned':
                # Exclusive directory creation is the no-overwrite boundary.
                home.parent.mkdir(parents=True,exist_ok=True)
                home.mkdir(exist_ok=False)
                item['phase']='authoring';_write(receipt_path,receipt)
                mind=Participant.author(home,id=item['id'],name=row['name'],kind=row['kind'],system=row['system'],
                    deployment=deployment,roots=row['roots'],mailboxes=Path(share),cartridge=False,
                    protocols='Use the project brief and acceptance criteria. Tools and writes are limited by this role. '
                    'Execute only approved project tests on trusted inputs; the test runner is not an OS security sandbox. '
                    'Report actual checks separately from planned checks. Installation recommendations are not enforced model budgets.')
                from .specialist_learning import create
                create(home,mind.id,mind.name,row['technical_subject'])
                mind.allowed_tools=row['allowed_tools'];mind.tags=[row['id'],'specialist'];mind.save()
                (home/'template.json').write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                item['authored_hashes']={p:hashlib.sha256((home/p).read_bytes()).hexdigest() for p in
                                        ('participant.json','identity/System_0.txt','protocols/orientation.md','template.json')}
                item['phase']='authored';_write(receipt_path,receipt)
            if any(hashlib.sha256((home/p).read_bytes()).hexdigest()!=h for p,h in item['authored_hashes'].items()):
                raise ValueError('Authored files changed before registration. Inspect '+str(home))
            preview=registry.preview('inject',str(home))
            registry.confirm(preview['review'],preview['token'])
            item['phase']='registered';_write(receipt_path,receipt)
        receipt['complete']=True;_write(receipt_path,receipt)
        return receipt
