"""Verify MCP protocol, source authorization, actual reports and model integration without live data."""
from copy import deepcopy
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
import pytest
from aether.data_sources.service import SourceService, SourceSession, bound, agent_session
from aether.data_sources.store import protected
from aether.data_sources.results import normalize, export
from aether.data_sources.mcp import endpoint
from aether.keys.runtime_keys import as_operator, inherited
from aether.atelier.operator_roles import effective
from data_source_fixture import http_fixture


def test_transport_exchange_direct():
    from aether.data_sources.mcp import exchange
    with http_fixture() as fixture:
        result=exchange(dict(transport='http',url=fixture.url,timeout=3),fixture.token,lambda client:client.list_tools())
        assert len(result)==2


class Access:
    def __init__(self):
        self.doc = dict(roles=[dict(id='reporting', name='Reporting', permissions=['use_data_sources'])], accounts=[
            dict(id='admin', name='Admin', role='admin', active=True, permissions=[], roles=[]),
            dict(id='reader', name='Reader', role='user', active=True, permissions=[], roles=['reporting']),
            dict(id='peer', name='Peer', role='user', active=True, permissions=[], roles=['reporting']),
            dict(id='denied', name='Denied', role='user', active=True, permissions=[], roles=[])])

    def actor(self, aid):
        return effective(next((u for u in self.doc['accounts'] if u['id'] == aid and u['active']), None), self.doc)

    def read(self):
        return deepcopy(self.doc)


def configured(tmp_path, fixture, provider='SQL Server'):
    access = Access()
    service = SourceService(tmp_path / 'sources', access)
    revision = service.store.listing('admin', True)['revision']
    body = dict(revision=revision, name='Personnel', alias='Personnel', description='Synthetic promotion data', provider=provider,
                guide='Promotion flag means approved; do not confuse eligibility with approval.', transport='http', url=fixture.url,
                timeout=3, token=fixture.token, roles=['reporting'], enabled=True, allow_model_use=True, tools=[])
    saved = service.store.update('admin', body)
    sid = saved['sources'][0]['id']
    discovered = service.discover('admin', sid)
    source = discovered['sources'][0]
    source['tools'] = [dict(name=t['name'], kind='schema' if t['name'].startswith('describe') else 'query', mapping={}) for t in source['catalog']]
    saved = service.store.update('admin', dict(source, revision=discovered['revision']))
    return service, access, saved['sources'][0]


@pytest.mark.parametrize('provider', ['SQL Server', 'Oracle', 'MySQL', 'PostgreSQL'])
def test_provider_neutral_http_real_rows_and_restart(tmp_path, provider):
    with http_fixture() as fixture:
        service, access, source = configured(tmp_path, fixture, provider)
        session = SourceSession(service, 'reader')
        assert session.directory()[0]['provider'] == provider
        result = session.call('call', dict(source='@Personnel', tool='report_officers', arguments=dict(division='North')))
        assert result['result']['table']['rows'][0] == ['Avery Chen', 'North', 'Sergeant', 8]
        assert result['result']['completeness'] == 'complete'
        report = session.call('save_report', dict(run=result['id'], title='Promotions', layout=dict(sort_column='Years', ascending=False)))
        restarted = SourceService(tmp_path/'sources', access)
        fresh = SourceSession(restarted, 'reader').call('run_report', dict(report=report['id'], arguments=dict(division='South')))
        assert fresh['result']['table']['rows'][0][1] == 'South'
        assert fresh['layout'] == dict(sort_column='Years', ascending=False)
        assert service.store.private('reader', 'run', report['snapshot_id'])['result']['table']['rows'][0][1] == 'North'
        assert service.store.private('reader', 'report', report['id'])['arguments'] == dict(division='North')
        with pytest.raises(PermissionError):
            service.store.private('peer', 'run', result['id'])


def test_role_denial_revocation_and_no_unscoped_borrowing(tmp_path):
    with http_fixture() as fixture:
        service, access, source = configured(tmp_path, fixture)
        assert not SourceSession(service, 'denied').directory()
        with pytest.raises(PermissionError):
            service.run('denied', source['id'], 'report_officers', {})
        assert fixture.calls == []
        with bound(service):
            assert agent_session() is None
            with as_operator('reader'):
                found=[]
                worker=threading.Thread(target=inherited(lambda: found.append(agent_session().aid)))
                worker.start();worker.join();assert found==['reader']
        result = service.run('reader', source['id'], 'report_officers', {})
        access.doc['accounts'][1]['roles'] = []
        with pytest.raises(PermissionError):
            service.store.private('reader', 'run', result['id'])
        assert not SourceSession(service, 'reader').directory()


def test_revocation_during_call_does_not_publish(tmp_path):
    with http_fixture() as fixture:
        service, access, source = configured(tmp_path, fixture)
        fixture.release.clear()
        job=service.start('reader',dict(source=source['id'],tool='report_officers',arguments={}))
        assert fixture.entered.wait(3)
        access.doc['accounts'][1]['active']=False
        fixture.release.set();service.jobs[job['id']]['worker'].join(4)
        assert service.jobs[job['id']]['status']=='failed'
        with service.store.db() as db:
            assert db.execute("SELECT count(*) FROM records WHERE kind='run'").fetchone()[0]==0


def test_tool_scope_and_changed_schema_are_enforced(tmp_path):
    with http_fixture() as fixture:
        service, access, source = configured(tmp_path, fixture)
        with pytest.raises(PermissionError):
            service.run('reader', source['id'], 'delete_database', {})
        fixture.tools[0]['inputSchema']['properties']['division']['type']='integer'
        with pytest.raises(ValueError,match='selected tool changed'):
            service.run('reader', source['id'], 'report_officers', {})
        assert fixture.calls==[]


def test_discovery_and_settings_require_manager_stale_save_fails(tmp_path):
    with http_fixture() as fixture:
        service, access, source = configured(tmp_path, fixture)
        with pytest.raises(PermissionError):service.discover('reader',source['id'])
        with pytest.raises(PermissionError):service.store.update('reader',{})
        with pytest.raises(ValueError,match='Refresh'):service.store.update('admin',dict(source,revision='stale'))
        data=service.store.listing('admin',True)
        source.update(url=source['url']+'/changed')
        with pytest.raises(ValueError,match='Discover tools'):service.store.update('admin',dict(source,revision=data['revision']))


def test_service_token_protected_and_not_exposed(tmp_path):
    with http_fixture() as fixture:
        service, access, source = configured(tmp_path, fixture)
        assert source['token_configured'] and 'secret' not in source
        assert fixture.token.encode() not in service.store.path.read_bytes()
        assert fixture.token not in json.dumps(service.store.listing('reader'))
        assert fixture.token not in SourceSession(service,'reader').briefing()


def test_sse_and_protocol_failure(tmp_path):
    with http_fixture() as fixture:
        fixture.sse=True
        service, access, source=configured(tmp_path,fixture)
        assert service.run('reader',source['id'],'report_officers',{})['result']['returned_rows']==2
        fixture.protocol='unsupported-future'
        with pytest.raises(ValueError,match='unsupported protocol'):service.discover('admin',source['id'])


def test_local_stdio_service(tmp_path):
    with http_fixture() as fixture:
        service,access,source=configured(tmp_path,fixture)
        data=service.store.listing('admin',True)
        source.update(transport='stdio',command=[sys.executable,str(Path(__file__).with_name('data_source_fixture.py'))],cwd='',tools=[])
        saved=service.store.update('admin',dict(source,revision=data['revision']))
        data=service.discover('admin',source['id']);source=data['sources'][0]
        source['tools']=[dict(name='report_officers',kind='query',mapping={})]
        service.store.update('admin',dict(source,revision=data['revision']))
        result=service.run('reader',source['id'],'report_officers',dict(division='Local'))
        assert result['result']['table']['rows'][0][1]=='Local'


def test_stop_and_halt_do_not_publish(tmp_path):
    with http_fixture() as fixture:
        service,access,source=configured(tmp_path,fixture)
        service.halt=lambda:True
        with pytest.raises(ValueError,match='stopped'):service.run('reader',source['id'],'report_officers',{})
        assert not fixture.calls
        service.halt=lambda:False
        fixture.release.clear()
        job=service.start('reader',dict(source=source['id'],tool='report_officers',arguments={}))
        assert fixture.entered.wait(3)
        service.job('reader',job['id'],True);fixture.release.set();service.jobs[job['id']]['worker'].join(4)
        assert service.jobs[job['id']]['status']=='stopped'
        assert service.store.private('reader','run')==[]


def test_error_result_not_saved_as_success(tmp_path):
    with http_fixture() as fixture:
        service,access,source=configured(tmp_path,fixture);fixture.fail=True
        with pytest.raises(ValueError,match='reported an error'):service.run('reader',source['id'],'report_officers',{})
        assert not service.store.private('reader','run')


def test_result_mapping_completeness_and_formula_safe_csv():
    value=normalize(dict(structuredContent=dict(data=dict(names=['Name','Count'],records=[['=1+1',-2]]),more=True)),dict(rows='data.records',columns='data.names',truncated='more'))
    assert value['completeness']=='partial'
    raw,_,_=export(dict(result=value),'csv');assert "'=1+1,-2" in raw.decode('utf-8-sig')
    unknown=normalize(dict(content=[dict(type='text',text='[{"a":1}]')]))
    assert unknown['completeness']=='unknown'
    many=normalize(dict(structuredContent=dict(rows=[dict(a=i) for i in range(5001)],truncated=False)))
    assert many['client_limited'] and many['completeness']=='partial' and len(many['table']['rows'])==5000
    assert normalize(dict(structuredContent=dict(message='No table')))['table'] is None


@pytest.mark.parametrize('url',['http://remote.example/mcp','https://user:secret@host/mcp','https://host/mcp?token=secret','file:///tmp/file'])
def test_endpoint_rejects_unsafe_auth_locations(url):
    with pytest.raises(ValueError):endpoint(url)


def test_chat_without_folder_uses_sources_and_protects_replay(tmp_path):
    from aether.atelier.model_chat import ModelChats
    with http_fixture() as fixture:
        service,access,source=configured(tmp_path,fixture)
        class Client:
            def __init__(self):self.calls=0
            def chat(self,messages,tools=None):
                self.calls+=1
                if self.calls==1:
                    assert any(s['function']['name']=='data_sources' for s in tools)
                    assert '@Personnel' in messages[0]['content']
                    args=dict(action='call',arguments_json=json.dumps(dict(source='Personnel',tool='report_officers',arguments=dict(division='Chat'))))
                    return dict(content='',tool_calls=[dict(id='call1',type='function',function=dict(name='data_sources',arguments=json.dumps(args)))])
                return dict(content='Two officers are included. Open the attached report for the actual rows.')
        client=Client();settings=SimpleNamespace(read=lambda:dict(document=dict(profiles=dict(test=dict(requestedApiIdentifier='synthetic',endpoint='http://unused',maxOutputTokens=1024)))))
        chat=ModelChats(tmp_path/'chats',settings,factory=lambda _:client,data_sources=service,operator_id='reader')
        doc=chat.create();chat.start(doc['id'],dict(model='test',text='Show promotions'))
        chat.settle(5);result=chat.read(doc['id'])
        assert result['job']['status']=='complete',result['job']
        assert len(result['messages'][-1]['report_runs'])==1
        assert result['used_data_sources']==[source['id']]
        access.doc['accounts'][1]['roles']=[]
        chat.start(doc['id'],dict(model='test',text='Repeat them'));chat.settle(5)
        assert chat.read(doc['id'])['job']['status']=='failed'
        assert client.calls==2


def test_agent_tool_uses_requesting_scope_and_respects_participant_policy(tmp_path):
    from aether.atelier.turn import Tools
    with http_fixture() as fixture:
        service,access,source=configured(tmp_path,fixture)
        participant=SimpleNamespace(technical=lambda:False,allowed_tools=['data_sources'])
        with bound(service),as_operator('reader'):
            tools=Tools(participant,tmp_path)
            result=json.loads(tools.call('data_sources',dict(action='call',arguments_json=json.dumps(dict(source='Personnel',tool='report_officers',arguments={})))) )
            assert result['result']['returned_rows']==2
            participant.allowed_tools=[]
            assert 'not allowed' in tools.call('data_sources',dict(action='list'))


def test_model_opt_out_and_source_selection(tmp_path):
    with http_fixture() as fixture:
        service,access,source=configured(tmp_path,fixture)
        assert not SourceSession(service,'reader',selected=[]).directory()
        data=service.store.listing('admin',True)
        service.store.update('admin',dict(source,revision=data['revision'],allow_model_use=False))
        assert not SourceSession(service,'reader').directory()
        assert service.run('reader',source['id'],'report_officers',{})['result']['returned_rows']==2


def test_timeout_does_not_retry_or_publish(tmp_path):
    with http_fixture() as fixture:
        service,access,source=configured(tmp_path,fixture)
        data=service.store.listing('admin',True)
        service.store.update('admin',dict(source,revision=data['revision'],timeout=1))
        fixture.release.clear();started=time.monotonic()
        with pytest.raises(ValueError):service.run('reader',source['id'],'report_officers',{})
        assert time.monotonic()-started<2.5
        fixture.release.set()
        assert len(fixture.calls)==1 and not service.store.private('reader','run')


def test_private_source_notes_and_saved_report_metadata_stay_scoped(tmp_path):
    with http_fixture() as fixture:
        service,access,source=configured(tmp_path,fixture)
        session=SourceSession(service,'reader')
        note=session.call('note',dict(source='Personnel',text='Approval flag is the confirmed business meaning.'))
        assert note['text'] in session.call('tools',dict(source='Personnel'))['personal_notes']
        assert not SourceSession(service,'peer').call('notes',dict(source='Personnel'))
        run=session.call('call',dict(source='Personnel',tool='report_officers',arguments={}))
        session.call('save_report',dict(run=run['id'],title='Local definition'))
        fresh=SourceSession(service,'reader');assert fresh.call('reports',{}) and source['id'] in fresh.used
