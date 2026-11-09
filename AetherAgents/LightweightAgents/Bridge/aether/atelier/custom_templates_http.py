"""Own local custom-template HTTP and pinned creation; never execute imports or alter existing specialists."""
from urllib.parse import urlparse, parse_qs
import uuid
from aether.integrity.http_body import json_body
from .custom_templates import CustomTemplates, convert, identifier, label
from . import specialist_install

API='/api/agent-catalog/custom'

def install(store,registry,share,settings,body):
    rid=identifier(body.get('request_id'))
    row=store.get(body.get('id'),body.get('version'))
    if body.get('sha256')!=row['sha256']:raise ValueError('Choose an exact saved template revision.')
    name=label(body.get('name'),'specialist name')
    deployment=body.get('deployment') or 'unconfigured'
    if deployment!='unconfigured' and deployment not in specialist_install.connections(settings):
        raise ValueError('Choose a saved parent model connection or configure one later.')
    runtime=convert(row['draft'],row['config'])
    runtime.update(id='custom-'+row['id'],name=name,source_template=row)
    runtime['home_key']='c-'+uuid.uuid5(uuid.UUID(rid),runtime['id']).hex
    runtime['system']='Your instance name is '+name+'. The template name identifies your authoring lineage.\n'+runtime['system']
    request=dict(id=row['id'],version=row['version'],sha256=row['sha256'],name=name,deployment=deployment)
    return specialist_install.install_resolved(registry,share,rid,request,[runtime],'custom-specialist-installs')

def handle_custom(handler,method,state):
    parsed=urlparse(handler.path);path=parsed.path
    registry=state['agent_registry'];store=CustomTemplates(registry.path.parent/'custom-templates.json')
    if method=='GET' and path==API:
        handler.settings_send(dict(store.listing(),connections=specialist_install.connections(state['model_settings']),default_folder=registry.default_folder))
    elif method=='GET' and path==API+'/template':
        q=parse_qs(parsed.query);version=q.get('version',[None])[0]
        handler.settings_send(store.get(q.get('id',[None])[0],int(version) if version is not None else None))
    elif method=='POST' and path in (API+'/save',API+'/install'):
        body=json_body(handler,2*1024*1024,'Send a custom template request as JSON.')
        if not isinstance(body,dict):raise ValueError('Send an object.')
        if path.endswith('/save'):result=store.save(body,handler.operator['id'])
        else:
            from .atelier_http import root_of
            result=install(store,registry,root_of(state),state['model_settings'],body)
        handler.settings_send(result)
    else:handler.settings_send(dict(error='Not found'),404)
    return True
