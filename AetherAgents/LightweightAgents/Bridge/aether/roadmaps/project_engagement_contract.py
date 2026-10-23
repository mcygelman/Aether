"""Versioned local attribution and association records; no runtime authority."""
import hashlib
import json
import uuid
import re

AUTHORITY = 'Project lead association only. Hydration, planning, staffing and execution require subsequent gates.'

def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':')).encode('utf-8')

def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()

def actor_valid(actor):
    if not isinstance(actor, dict) or set(actor) != {'id','display_name','revision'}:
        raise ValueError('Unsupported local operator record.')
    uuid.UUID(actor['id'])
    if not isinstance(actor['display_name'],str) or not actor['display_name'].strip() or type(actor['revision']) is not int or actor['revision'] < 1:
        raise ValueError('Invalid local operator name or revision.')

def validate_association(framing, project=None):
    lead = framing.get('heavyweight')
    if not isinstance(lead,dict) or set(lead) != {'id','version','proposal','sha256','request_id','request_sha256','at','workspace','operator'}:
        raise ValueError('Unsupported heavyweight association record.')
    if type(lead['version']) is not int or lead['version'] != 1 or lead['sha256'] != digest(lead['proposal']):
        raise ValueError('Heavyweight association integrity failed.')
    proposal = lead['proposal']
    if not isinstance(proposal,dict) or set(proposal)!={'version','project','profile','operator','catalog_sha256','mode','role','authority'} or proposal.get('authority') != AUTHORITY or type(proposal.get('version')) is not int or proposal['version'] != 1 or proposal.get('role') != 'project-lead':
        raise ValueError('Unsupported heavyweight authority.')
    bound=proposal['project'];profile=proposal['profile']
    if not isinstance(bound,dict) or set(bound)!={'id','name','root','summary','wmc','sha256'}:
        raise ValueError('Missing heavyweight project provenance.')
    if any(not isinstance(bound[k],str) or not bound[k].strip() for k in ('id','name','root','summary','sha256')):
        raise ValueError('Invalid heavyweight project provenance.')
    if project is None or bound['id']!=project['id'] or bound['root']!=project['root'] or bound['wmc']!=framing['wmc'] or bound['summary']!=framing['summary']:
        raise ValueError('The heavyweight ruling belongs to a different project context.')
    # Display names may change; the ruling retains the reviewed name. Identity/root/WMC do not.
    if not isinstance(profile,dict) or set(profile)!={'id','name','role','summary','source','manifest_sha256','classification','eligibility','integrity','status','model'}:
        raise ValueError('Missing heavyweight profile provenance.')
    if any(not isinstance(profile[k],str) or not profile[k].strip() for k in ('id','name','role','summary','source','manifest_sha256')):
        raise ValueError('Invalid heavyweight profile provenance.')
    if proposal['mode'] not in ('public','internal') or profile['classification'] not in ('public','internal','local') or (proposal['mode']=='public' and profile['classification'] not in ('public','local')) or profile['integrity']!='PASS' or profile['status']!='eligible-for-association':
        raise ValueError('Invalid heavyweight eligibility provenance.')
    if not isinstance(profile['eligibility'],dict) or set(profile['eligibility'])!={'source','sha256'} or not isinstance(profile['eligibility']['source'],str) or not profile['eligibility']['source']:
        raise ValueError('Invalid heavyweight eligibility reference.')
    model=profile['model']
    if not isinstance(model,dict) or model.get('status') not in ('unresolved','configured-unverified') or 'profile' not in model or not isinstance(model.get('note'),str):
        raise ValueError('Invalid heavyweight model provenance.')
    hashes=[bound['sha256'],proposal['catalog_sha256'],profile['manifest_sha256'],profile['eligibility']['sha256']]
    if model['status']=='configured-unverified':
        if any(not isinstance(model.get(k),str) or not model[k] for k in ('profile','registry_sha256','configuration_sha256','backend','model','capability_source')):raise ValueError('Incomplete heavyweight model provenance.')
        hashes.extend([model['registry_sha256'],model['configuration_sha256']])
    if any(not isinstance(h,str) or re.fullmatch('[0-9a-f]{64}',h) is None for h in hashes):
        raise ValueError('Invalid heavyweight integrity reference.')
    actor_valid(lead['operator'])
    if proposal.get('operator') != lead['operator']:
        raise ValueError('Heavyweight operator attribution changed.')
    rid = str(uuid.UUID(lead['request_id']))
    if lead['id'] != 'engagement-'+uuid.UUID(rid).hex or lead['workspace'] != '.aether/engagements/'+lead['id']+'/workspace':
        raise ValueError('Invalid heavyweight engagement workspace.')
    body=dict(request_id=rid,proposal=proposal,sha256=lead['sha256'],confirmed=True)
    if lead['request_sha256'] != digest(body) or not isinstance(lead['at'],str) or not lead['at']:
        raise ValueError('Invalid heavyweight confirmation evidence.')
    from .lead_rebinding import validate_history as rebind_history
    rebind_history(framing,project)
    from aether.roadmaps.planning_contract import validate_history, approval as planning_approval, event as planning_event
    preparations=validate_history(framing,project)
    expected=[approval(lead),*[planning_approval(r) for r in preparations]]
    events=[event(lead),*[planning_event(r) for r in preparations]]
    if framing.get('plan_rulings') is not None:
        from aether.roadmaps.plan_ruling_contract import validate_history as ruling_history
        expected,events=ruling_history(framing,project,preparations)
    if framing.get('approvals') != expected or len(framing.get('events',[]))!=len(events)+1 or framing['events'][1:]!=events:
        raise ValueError('Heavyweight association lacks its exact approval and lifecycle event.')

def approval(lead):
    return dict(id=lead['request_id'],action='associate-heavyweight',decision='confirmed',operator=lead['operator'],
                object_id=lead['id'],version=lead['version'],sha256=lead['sha256'],scope=AUTHORITY,
                reason='Operator confirmed the displayed project association.',at=lead['at'],prior='WMC_SELECTED',state='HEAVYWEIGHT_SELECTED')

def event(lead):
    return {**approval(lead), 'actor':lead['operator']}
