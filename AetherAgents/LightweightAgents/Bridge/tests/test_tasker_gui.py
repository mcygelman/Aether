"""Verify the GUI/API/native Timer composition offline in fresh profiles.

Never contact a provider or borrow resident homes; scripted fixtures own their closure text.
"""
import json
import threading
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from aether.mission_control.atelier_server import serve
from aether.mission_control.tasker_projects import TaskerProjects
from aether.overseer.homes import create_heavyweight_home
from aether.tasker import staff
from aether.tasker.sprint import load_sprint, TurnResult
from tasker_http_fixture import ScriptedServer
from test_tasker_support import small_identity


@pytest.fixture
def tmp_path(tmp_path_factory):
    return tmp_path_factory.mktemp('g')


class Instance:
    def __init__(self, root, endpoint='http://127.0.0.1:9/v1'):
        self.root = root
        self.data = root/'d'
        self.profiles = root/'profiles.json'
        self.profiles.write_text(json.dumps({'schemaVersion':1,'profiles':{'offline':{
            'description':'Offline scripted fixture','backend':'openai-compatible',
            'requestedApiIdentifier':'fixture-model','identifierKind':'logical-alias','endpoint':endpoint,
            'capabilitySource':'offline fixture','timeoutSecs':10,'maxToolCalls':100,
            'dialect':'openai','maxOutputTokens':8192,'contextWindow':131072}}}), encoding='utf-8')
        create_heavyweight_home(self.data, 'iris', 'Fixture Iris')
        seed = root/'synthetic-staff'; seed.mkdir()
        (seed/'Identity_Researcher_Sagan.txt').write_text(small_identity('Sagan','Researcher'), encoding='utf-8')
        staff.seed_staff(self.data, seed)
        self.server = serve(root/'bridge', port=0, registry_path=root/'registry.json',
            runtime_profiles_path=self.profiles, minds_folder=root/'empty',
            project_folders_root=root/'projects', atelier_root_path=root/'atelier', tasker_data_root=self.data)
        self.server.state['tasker_projects'].interval = .02
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:'+str(self.server.server_port)
        self.cookie = ''
        body=dict(username='reviewer',name='Offline Reviewer',password='offline-pass-2026',confirmation='offline-pass-2026')
        request=Request(self.base+'/api/access/register',data=json.dumps(body).encode(),headers={'Origin':self.base,'Content-Type':'application/json'})
        with urlopen(request) as response:
            self.cookie=response.headers['Set-Cookie'].split(';',1)[0]
            self.actor=json.load(response)['user']['id']

    def call(self, path, body=None, expected=200, headers=None):
        request=Request(self.base+path,data=None if body is None else json.dumps(body).encode(),
                        headers={'Cookie':self.cookie,'Origin':self.base,'Content-Type':'application/json',**(headers or {})})
        try: response=urlopen(request,timeout=10)
        except HTTPError as exc: response=exc
        with response:
            raw=response.read()
            assert response.status==expected,raw
            return json.loads(raw) if response.headers.get('Content-Type','').startswith('application/json') else raw

    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join(5)
        assert not self.thread.is_alive()


def payload(review=False):
    return dict(request_id=uuid.uuid4().hex,name='Offline output',goals='Copy the fixture input to COPY.txt.',
        overseer='iris',deployment='offline',caps={'max_turns_per_sprint':20,'max_cost_per_sprint':1},
        contracts={'1':dict(version=1,review_required=review,files={'COPY.txt':{'json':{'value':7}}},evidence=[])})


def wait(instance, project):
    deadline=time.monotonic()+35
    while time.monotonic()<deadline:
        value=instance.call('/api/tasker/project?project='+project)
        if value['execution'] not in ('running','stopping'):
            return value
        time.sleep(.05)
    raise AssertionError('Native Timer did not finish: '+str(value))


@pytest.mark.parametrize('mode',['verified','review','failed'])
def test_real_http_native_timer_and_acceptance(tmp_path, mode):
    source=tmp_path/'input/source.txt';source.parent.mkdir();source.write_text('{"value":8}' if mode=='failed' else '{"value":7}')
    with ScriptedServer(source) as model:
        app=Instance(tmp_path,model.endpoint)
        try:
            body=payload(mode=='review')
            created=app.call('/api/tasker/create',body);project=created['project_id']
            assert created['execution']=='prepared' and not model.requests
            assert app.call('/api/tasker/create',body)['id']==created['id']
            changed=dict(body,name='Different')
            app.call('/api/tasker/create',changed,409)
            sprint=load_sprint(created['path'])
            # The isolated fixture provisions a known input grant, never a browser-selected path.
            sprint.update(lambda d:d.update(overseer_read_roots=[str(source.parent)]))
            app.call('/api/tasker/start',{'project':project})
            app.call('/api/tasker/start',{'project':project})
            ended=wait(app,project)
            assert not model.errors
            assert ended['execution']=='ended'
            assert ended['verification']['status']=={'verified':'accepted','review':'review_required','failed':'incomplete'}[mode]
            count=len(model.requests)
            # A fresh adapter reconstructs state; reading/start retry cannot resume held work.
            fresh=TaskerProjects(app.data,app.profiles)
            assert fresh.read(project)['id']==created['id']
            assert fresh.start(project,app.actor)['execution']=='ended'
            assert len(model.requests)==count
            if mode=='review':
                sha=ended['verification']['tasks']['1']['receipt_sha256']
                bad=app.call('/api/tasker/accept',{'project':project,'reviews':{'1':{'verdict':'PASS','receipt_sha256':'0'*64}}})
                assert bad['status']=='failed'
                assert ended['headline']=='Waiting for your review'
                good=app.call('/api/tasker/accept',{'project':project,'reviews':{'1':{'verdict':'PASS','receipt_sha256':sha,'notes':'Read COPY.txt'}}})
                assert good['status']=='accepted'
                assert app.call('/api/tasker/project?project='+project)['verification']['status']=='accepted'
                saved=json.loads((app.data/'verification'/created['id']/'review-current.json').read_text(encoding='utf-8'))
                assert saved['reviews']['1']['notes']=='Read COPY.txt'
                (sprint.outputs(1)/'COPY.txt').write_text('{"value":9}')
                assert app.call('/api/tasker/accept',{'project':project})['status']=='failed'
            elif mode=='failed':
                assert 'output_verification_failed' in ended['terminal_failure']
                assert app.call('/api/tasker/accept',{'project':project})['status']=='incomplete'
                # Michel, 2026-11-07: a failed check reads in plain words and has a way forward (Accept anyway).
                assert ended['failure_words'].startswith('The outputs did not pass their checks')
                assert ended['can_accept_anyway'] and ended['operator_accepted'] is None
                app.call('/api/tasker/accept-anyway',{'project':project,'reason':'  '},409)
                app.call('/api/tasker/accept-anyway',{'project':project,'reason':'x','path':'C:/'},409)
                over=app.call('/api/tasker/accept-anyway',{'project':project,'reason':'The value is right this week.'})
                assert over['operator_accepted']['current'] and not over['can_accept_anyway']
                assert over['headline']=='Accepted by Offline Reviewer over a failed check'
                assert over['verification']['status']=='incomplete'          # the check itself is unchanged
                app.call('/api/tasker/accept-anyway',{'project':project,'reason':'again'},409)
            else:
                assert app.call('/api/tasker/accept',{'project':project})['status']=='accepted'
            assert len(list((app.data/'sprints').glob('*/sprint.json')))==1
        finally: app.close()


def test_stop_halt_reload_and_guards(tmp_path):
    app=Instance(tmp_path)
    entered=threading.Event();release=threading.Event()
    def slow(*args,**kwargs):
        entered.set();release.wait(8)
        return TurnResult(True,'Fixture stopped',{})
    app.server.state['tasker_projects'].runners=(slow,slow)
    try:
        app.call('/api/tasker/create',dict(payload(),profiles_path='C:/outside.json'),409)
        app.call('/api/tasker/projects',headers={'Cookie':''},expected=401)
        app.call('/api/tasker/create',payload(),headers={'Origin':'http://other.invalid'},expected=403)
        created=app.call('/api/tasker/create',payload());project=created['project_id']
        app.call('/api/tasker/start',{'project':project});assert entered.wait(5)
        app.call('/api/workspace',{'action':'halt','revision':app.call('/api/workspace')['revision']})
        assert app.call('/api/tasker/project?project='+project)['execution']=='stopping'
        app.call('/api/tasker/create',payload(),409)
        release.set();ended=wait(app,project)
        assert ended['execution']=='stopped'
        assert app.call('/api/tasker/start',{'project':project})['execution']=='stopped'
        app.call('/api/workspace',{'action':'resume','revision':app.call('/api/workspace')['revision']})
        assert app.call('/api/tasker/project?project='+project)['execution']=='stopped'
        app.call('/api/tasker/output?project='+project+'&task=1&name=../../.sealbox_key',expected=409)
        assert app.call('/projects/tasker').startswith(b'<!doctype html>')
        assert b'Never reconstruct filesystem roots' in app.call('/workspace-assets/tasker-project.js')
    finally: release.set();app.close()


def test_lead_budget_continue_and_plain_status(tmp_path):
    app=Instance(tmp_path)
    try:
        options=app.call('/api/tasker/lead',{'name':'Nova Lead'})
        assert any(m['id']=='nova-lead' and m['name']=='Nova Lead' for m in options['minds'])
        app.call('/api/tasker/lead',{'name':'Nova Lead'},409)  # an existing home is never replaced
        app.call('/api/tasker/lead',{'name':'Other','path':'C:/outside'},409)
        created=app.call('/api/tasker/create',payload());project=created['project_id']
        assert created['headline']=='Ready to start'
        assert created['output_files']=={'1':[{'name':'COPY.txt','ready':False}]}
        app.call('/api/tasker/budget',{'project':project,'caps':{'max_cost_per_sprint':0.5}},409)
        app.call('/api/tasker/budget',{'project':project,'caps':{'max_parallel_turns':9}},409)
        raised=app.call('/api/tasker/budget',{'project':project,'caps':{'max_cost_per_sprint':2,'max_turns_per_sprint':40}})
        assert raised['caps']['max_cost_per_sprint']==2 and raised['caps']['max_turns_per_sprint']==40
        app.call('/api/tasker/resume',{'project':project},409)  # Continue answers a pause; it never starts work
        assert app.call('/api/tasker/project?project='+project)['execution']=='prepared'
    finally: app.close()


def test_first_use_seeds_the_shipped_staff_once(tmp_path):
    profiles=tmp_path/'profiles.json'
    profiles.write_text('{"profiles":{"unconfigured":{"requestedApiIdentifier":"not-configured"}}}',encoding='utf-8')
    service=TaskerProjects(tmp_path/'d',profiles)
    assert service.options()['deployments']==[]  # the installed setup placeholder is not a model connection
    # Praxis, 11-03: the shipped catalog (37 fresh identities); the page groups them by its categories.
    catalog=json.loads((staff.PRODUCT_STAFF/'catalog.json').read_text(encoding='utf-8'))['templates']
    shown=service.options()['staff']
    assert sorted(s['name'] for s in shown)==sorted(t['name'] for t in catalog) and len(shown)==len(catalog)
    assert {s['id']:s['category'] for s in shown}=={t['staff_id']:t['category'] for t in catalog}
    assert list(dict.fromkeys(s['category'] for s in shown))==list(dict.fromkeys(t['category'] for t in catalog))
    assert all(s['available'] for s in service.options()['staff'])
    assert all(not staff.home_file(tmp_path/'d',s['id']).read_text(encoding='utf-8').count('MICRO_REFLECTION_INDEX_ENTRY_BEGIN')
               for s in service.options()['staff'])
    # The roster is the operator's after first use: it is never reseeded over.
    roster=tmp_path/'d'/'staff'/'roster.json';before=roster.read_bytes()
    service.options();assert roster.read_bytes()==before


def test_an_older_roster_takes_the_missing_standard_staff_without_replacing_anyone(tmp_path):
    # Michel, 2026-11-03: one step adds the shipped staff a roster lacks; nobody already at home is replaced.
    app=Instance(tmp_path)  # its roster holds only the fixture's own Sagan
    try:
        home=staff.home_file(app.data,'sagan');before=home.read_bytes()
        options=app.call('/api/tasker/options')
        missing={m['id'] for m in options['missing_staff']}
        catalog=json.loads((staff.PRODUCT_STAFF/'catalog.json').read_text(encoding='utf-8'))['templates']
        assert missing=={t['staff_id'] for t in catalog}-{'sagan'} and 'grace' in missing
        added=app.call('/api/tasker/staff',{})
        assert set(added['added'])==missing and added['missing_staff']==[] and len(added['staff'])==len(catalog)
        assert home.read_bytes()==before  # the existing Sagan, and its memory, untouched
        app.call('/api/tasker/staff',{},409)  # nothing left to add
    finally: app.close()


def test_staff_ai_reads_the_native_lead_and_staff_homes_and_names_them(tmp_path):
    # Praxis, 11-03 (local review and agent visibility): Staff → AI shows the homes sprints hire from.
    from aether.overseer.meridian_setup import prepare
    data=tmp_path/'data';data.mkdir()
    prepare(data)
    profiles=tmp_path/'profiles.json'
    profiles.write_text(json.dumps({'profiles':{'offline':{'requestedApiIdentifier':'fixture-model'}}}),encoding='utf-8')
    service=TaskerProjects(data,profiles)
    before=sorted((p.relative_to(data).as_posix(),p.read_bytes()) for p in data.rglob('*') if p.is_file())
    team=service.team()
    assert [(l['id'],l['name'],l['available'],l['intact']) for l in team['leads']]==[('meridian','Meridian',True,True)]
    catalog=json.loads((staff.PRODUCT_STAFF/'catalog.json').read_text(encoding='utf-8'))['templates']
    assert len(team['staff'])==len(catalog) and team['missing_staff']==[]
    assert all(s['sprint'] is None and s['home'].endswith(s['id']) for s in team['staff'])
    assert sorted((p.relative_to(data).as_posix(),p.read_bytes()) for p in data.rglob('*') if p.is_file())==before  # read-only
    page=(Path(__file__).resolve().parents[1]/'aether_bridge/web/agent_availability.html').read_text(encoding='utf-8')
    assert "teamApi('/api/tasker/team')" in page and 'id="atelier-older"' in page
    assert "$('starter').hidden=true;$('starterPanel').hidden=true;" in page  # no empty-registry starter team


def test_the_sprint_page_names_its_lead_and_staff(tmp_path):
    app=Instance(tmp_path)
    try:
        created=app.call('/api/tasker/create',payload())
        assert created['lead_name']=='Fixture Iris' and created['staff_names']==[]
        team=app.call('/api/tasker/team')
        assert [l['name'] for l in team['leads']]==['Fixture Iris'] and [s['id'] for s in team['staff']]==['sagan']
    finally: app.close()


def test_teach_shows_the_school_the_staff_are_taught_from(tmp_path):
    # 11-05 (Michel: "there might be issues displaying the teachframes"): Teach listed only workspace and Chat
    # frames, so the School's curriculum was invisible in Mission Control.
    from aether.knowledge.school import load_catalog
    app=Instance(tmp_path)
    try:
        frames=app.call('/api/tasker/school')['frames']
        catalog=load_catalog()
        assert {f['name'] for f in frames}==set(catalog)
        body=next(f for f in frames if f['name']=='SVG_CAT_BODY_CONSTRUCTION')
        assert body['state']=='CURRICULUM' and body['school']=='School Of Coding' and body['version']
        assert body['recommended_for']==['CatAnimator'] and body['title'].startswith('Construct a Cat Body')
        text=app.call('/api/tasker/school-frame?name=SVG_CAT_BODY_CONSTRUCTION')['text']
        assert text==catalog['SVG_CAT_BODY_CONSTRUCTION'].path.read_text(encoding='utf-8')
        refused=app.call('/api/tasker/school-frame?name=../../CATALOG.json',expected=409)
        assert 'No School frame' in refused['error']   # a catalog name only, never a path
    finally: app.close()
    page=(Path(__file__).resolve().parents[1]/'aether_bridge/web/workspace_assets/teach.js').read_text(encoding='utf-8')
    assert "api('/api/tasker/school')" in page and "['school','From the School']" in page


def test_interrupted_owner_is_never_reported_stopped_or_restarted(tmp_path):
    app=Instance(tmp_path)
    try:
        created=app.call('/api/tasker/create',payload());project=created['project_id']
        path=Path(created['path'])/'timer/gui-run.json'
        path.write_text('{"state":"running"}')
        assert app.call('/api/tasker/project?project='+project)['execution']=='interrupted'
        assert app.call('/api/tasker/start',{'project':project})['execution']=='interrupted'
        assert app.call('/api/tasker/stop',{'project':project})['execution']=='interrupted'
        assert not (Path(created['path'])/'timer/timer.json').exists()
    finally: app.close()


def test_a_stopped_run_never_reads_like_a_closed_one():
    # 11-04 SVG Cat runs: four Timer stops read "Finished without a checked result" or "Output checks failed".
    from aether.mission_control.tasker_projects import headline
    stop = 'incomplete: command_unresolved: Correct the failed operation and rerun with retry=true, or task_block.'
    assert headline('ended', 'incomplete', terminal_failure=stop, sprint_state='running') == 'Stopped before finishing'
    assert headline('ended', 'failed', terminal_failure=stop, sprint_state='closing') == 'Stopped before finishing'
    assert headline('ended', 'incomplete', sprint_state='closed-pass') == 'Closed by the team; not reviewable now'
    assert headline('ended', 'review_required', sprint_state='closed-pass') == 'Waiting for your review'
    assert headline('ended', 'incomplete', sprint_state='running') == 'Finished without a checked result'
    assert headline('interrupted', 'incomplete', terminal_failure=stop).startswith('Interrupted')


def test_a_stop_reads_in_plain_words():
    from aether.mission_control.tasker_projects import failure_words
    raw = "incomplete: output_verification_failed: Output file set differs: missing=[], extra=['HELLO.txt']"
    assert failure_words(raw) == ('The outputs did not pass their checks: the lead also wrote files this sprint did '
                                  'not ask for: HELLO.txt.')
    raw = "incomplete: output_verification_failed: Output file set differs: missing=['answer.txt'], extra=[]"
    assert failure_words(raw) == 'The outputs did not pass their checks: these files were not written: answer.txt.'
    assert failure_words('incomplete: overseer_closure_missing: no receipt').startswith('The lead did not file')
    assert failure_words(None) == ''


def test_continue_carries_on_only_when_the_app_closed_between_turns(tmp_path):
    # Michel, 2026-11-03 (T05): the operator continues an interrupted sprint, never a turn left mid-way.
    # The real process kills are in GUI_TASKER_20261102/opus/prove_interrupted_sprint.py.
    source=tmp_path/'input/source.txt';source.parent.mkdir();source.write_text('{"value":7}')
    with ScriptedServer(source) as model:
        app=Instance(tmp_path,model.endpoint)
        try:
            projects=[]
            for _ in range(2):
                created=app.call('/api/tasker/create',payload());project=created['project_id']
                load_sprint(created['path']).update(lambda d:d.update(overseer_read_roots=[str(source.parent)]))
                (Path(created['path'])/'timer').mkdir(exist_ok=True)
                (Path(created['path'])/'timer/gui-run.json').write_text('{"state":"running"}')  # its process is gone
                projects.append((project,Path(created['path'])))
            (mid,mid_path),(between,_)=projects
            (mid_path/'timer/active').mkdir()
            (mid_path/'timer/active/0001.json').write_text('{"turn":1,"mind":"overseer","kind":"HIRED"}')
            shown=app.call('/api/tasker/project?project='+mid)
            assert shown['execution']=='interrupted' and shown['open_turns']==['overseer'] and not shown['can_continue']
            assert shown['headline']=='Interrupted mid-turn; kept as it is'
            refused=app.call('/api/tasker/continue',{'project':mid},409)
            assert refused['error'].startswith('The lead was in the middle of a turn') and not model.requests
            assert not (mid_path/'timer/RECOVERY_STOP.json').exists()  # refusing changes nothing
            shown=app.call('/api/tasker/project?project='+between)
            assert shown['can_continue'] and not shown['can_start'] and shown['open_turns']==[]
            assert shown['headline']=='Interrupted between turns; ready to continue'
            assert app.call('/api/tasker/continue',{'project':between})['execution']=='running'
            app.call('/api/tasker/continue',{'project':between},409)  # once running, there is nothing to continue
            ended=wait(app,between)
            assert ended['execution']=='ended' and ended['headline']=='Accepted' and not model.errors
            assert [c['operator'] for c in ended['run']['continued']]==[app.actor]
        finally: app.close()


def test_a_sprint_started_from_a_roadmap_records_its_outcome_there(tmp_path):
    # Michel, 2026-11-02: a sprint starts from its roadmap, and the lead's roadmap_update writes back into it.
    from aether.roadmaps.project_folders import seed_first_run
    from aether.roadmaps.tracker.tracker_store import RoadmapStore
    from aether.overseer.roadmap import record_outcome
    seed_first_run(tmp_path/'projects')
    app=Instance(tmp_path)
    try:
        key='Aether/roadmap-mvp-persistent-agents-20261008'
        options=app.call('/api/tasker/options')
        assert [r['key'] for r in options['roadmaps']]==[key]
        app.call('/api/tasker/create',dict(payload(),roadmap='Nowhere/none'),409)
        created=app.call('/api/tasker/create',dict(payload(),roadmap=key))
        assert created['roadmap']=={'key':key,'name':'Aether MVP — Persistent agents and a reproducible demonstration','step':None}
        sprint=load_sprint(created['path'])
        before=len(RoadmapStore(tmp_path/'projects/Aether/roadmaps.json').read()[0]['sprints'])
        assert record_outcome(sprint,'Copied the input.','pass')['status']=='recorded'
        data=RoadmapStore(tmp_path/'projects/Aether/roadmaps.json').read()[0]
        [row]=[s for s in data['sprints'] if s['id']==sprint.id]
        assert len(data['sprints'])==before+1 and row['roadmapId']=='roadmap-mvp-persistent-agents-20261008'
        assert row['status']=='Closed' and row['description']=='PASS: Copied the input.'
        assert app.call('/api/tasker/projects')['projects'][0]['roadmap']['key']==key
    finally: app.close()


def test_active_sprints_joins_the_roadmaps_sprints_with_the_runs_that_do_them(tmp_path):
    # Michel, 2026-11-03: a roadmap's sprints are the sprints; Start sprint on a planned one binds the run to it.
    from aether.roadmaps.project_folders import seed_first_run
    seed_first_run(tmp_path/'projects')
    app=Instance(tmp_path)
    try:
        key='Aether/roadmap-mvp-persistent-agents-20261008'
        [roadmap]=app.call('/api/tasker/plan')['roadmaps']
        assert roadmap['key']==key and len(roadmap['sprints'])==11
        assert [s['status'] for s in roadmap['sprints']].count('Open')==10 and not any(s['run_record'] for s in roadmap['sprints'])
        steps=app.call('/api/tasker/options')['roadmaps'][0]['steps']
        assert len(steps)==10 and steps[0]['id']=='sprint-mvp-01'
        app.call('/api/tasker/create',dict(payload(),roadmap_step='sprint-mvp-01'),409)  # a step needs its roadmap
        app.call('/api/tasker/create',dict(payload(),roadmap=key,roadmap_step='sprint-nowhere'),409)
        created=app.call('/api/tasker/create',dict(payload(),roadmap=key,roadmap_step='sprint-mvp-01'))
        assert created['roadmap']['step']=={'id':'sprint-mvp-01','name':'MVP-01 — Complete Mission Control project workflow'}
        record=load_sprint(created['path']).record()
        assert record['roadmap_sprint_id']=='sprint-mvp-01' and record['roadmap_key']==key
        # While its sprint has not ended, the planned sprint is not offered again on the form.
        assert 'sprint-mvp-01' not in [s['id'] for s in app.call('/api/tasker/options')['roadmaps'][0]['steps']]
        page=(Path(__file__).resolve().parents[1]/'aether_bridge/web/workspace_assets/sprints.js').read_text(encoding='utf-8')
        assert "api('/api/tasker/plan')" in page and "'&step='" in page
        # Michel, 11-05: newest first; a never-started sprint is dated by when it was prepared. Planned keeps plan order.
        assert app.call('/api/tasker/project?project='+created['project_id'])['created']==record['created']
        assert "at:v.run?.started_at||v.created||''" in page and "if(g!=='planned')these.sort(" in page
    finally: app.close()


def test_a_sprint_page_draws_its_svg_outputs_and_offers_accepted_ones_to_creations(tmp_path):
    # Michel, 11-05: Show draws an SVG output as a picture (a blob image, never page markup), and an accepted sprint's
    # outputs open in Creations as a copy to review before saving.
    app=Instance(tmp_path)
    try:
        with urlopen(Request(app.base+'/projects/tasker',headers={'Cookie':app.cookie}),timeout=10) as response:
            policy=response.headers['Content-Security-Policy']
        assert "img-src 'self' blob: data:" in policy and "object-src 'none'" in policy
        script=app.call('/workspace-assets/tasker-project.js').decode('utf-8')
        assert "type:'image/svg+xml'" in script and "status==='accepted'" in script and 'Save to Creations…' in script
        creations=app.call('/workspace-assets/creations.js').decode('utf-8')
        assert "params.get('sprint')" in creations and "status!=='accepted'" in creations
    finally: app.close()
