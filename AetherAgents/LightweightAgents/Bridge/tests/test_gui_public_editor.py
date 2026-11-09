"""Public editor boundary and navigation-disconnect regressions; synthetic state only."""
from http.client import HTTPConnection
from unittest.mock import Mock
import json
import threading
import pytest
from aether.agent_editor.http import WEB
from aether.backends.gui_assets import PUBLIC_EDITOR_ASSETS
from aether.mission_control import atelier_server
from aether.operations.installed_app import unconfigured_profiles


@pytest.fixture
def atelier(tmp_path):
    profiles=tmp_path/'runtime_profiles.json';profiles.write_text(json.dumps(unconfigured_profiles()))
    bridge=tmp_path/'bridge';bridge.mkdir()
    server=atelier_server.serve(bridge,port=0,registry_path=tmp_path/'projects.json',
        runtime_profiles_path=profiles,minds_folder=tmp_path/'minds',agent_roots=[],
        project_folders_root=tmp_path/'Projects',atelier_root_path=tmp_path/'atelier')
    _,token=server.state['operator_access'].register(dict(username='fixture',name='Fixture Admin',
        password='Synthetic fixture phrase',confirmation='Synthetic fixture phrase'))
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    def get(path):
        conn=HTTPConnection('127.0.0.1',server.server_port,timeout=10)
        try:
            conn.request('GET',path,headers={'Cookie':f'aether_operator_{server.server_port}={token}'})
            response=conn.getresponse();return response.status,response.read()
        finally:conn.close()
    get.httpd=server
    try:yield get
    finally:server.shutdown();server.server_close();worker.join(10)


def test_public_editor_fixed_assets_and_permissions(atelier):
    server = atelier.httpd
    status, page = atelier('/agent-editor/')
    assert status == 200 and b'New blank template' in page and b'Future Feature' in page
    assert b'js/bridge.js' not in page and b'publication-confirm' not in page
    for path in PUBLIC_EDITOR_ASSETS:
        assert atelier('/agent-editor/'+path)[0] == 200
    for path in ('lifecycle.html', 'js/bridge.js', 'fixtures/sample_lw_template.json', '../http.py'):
        assert atelier('/agent-editor/'+path)[0] == 404
    assert atelier('/api/agent-editor/consent-policy')[0] == 404
    access = server.state['operator_access']
    reader, token = access.register(dict(username='reader',name='Reader',password='Synthetic reader phrase',confirmation='Synthetic reader phrase'))
    def validate(cookie):
        conn = HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        try:
            conn.request('POST','/api/agent-editor/validate',(WEB/'fixtures/sample_lw_template.json').read_bytes(),headers={
                'Content-Type':'application/json','Origin':f'http://127.0.0.1:{server.server_port}',
                'Cookie':f'aether_operator_{server.server_port}={cookie}'})
            response=conn.getresponse();response.read();return response.status
        finally: conn.close()
    assert validate(token) == 403
    admin=next(a for a in access.listing()['accounts'] if a['role']=='admin')
    access.update(admin,dict(reader,revision=access.read()['revision'],permissions=['manage_agents']))
    assert validate(token) == 200
    access.update(admin,dict(reader,revision=access.read()['revision'],permissions=[]))
    assert validate(token) == 403


@pytest.mark.parametrize('failure',[ConnectionAbortedError,ConnectionResetError,BrokenPipeError])
def test_navigation_disconnect_never_attempts_a_second_response(monkeypatch,failure):
    cls=atelier_server.make_handler({});handler=cls.__new__(cls)
    handler.path='/api/workspace/installation';handler.close_connection=False
    handler.send_json=Mock()
    monkeypatch.setattr(atelier_server.gateway,'refuse',lambda *args:False)
    monkeypatch.setattr(atelier_server,'operator_guard',lambda *args:False)
    monkeypatch.setattr(atelier_server,'handle_workspace',Mock(side_effect=failure('client left')))
    handler._dispatch('GET')
    assert handler.close_connection
    handler.send_json.assert_not_called()


def test_disconnect_during_error_response_is_closed(monkeypatch):
    cls=atelier_server.make_handler({});handler=cls.__new__(cls)
    handler.path='/api/workspace/installation';handler.close_connection=False
    handler.send_json=Mock(side_effect=BrokenPipeError('client left'))
    monkeypatch.setattr(atelier_server.gateway,'refuse',lambda *args:False)
    monkeypatch.setattr(atelier_server,'operator_guard',lambda *args:False)
    monkeypatch.setattr(atelier_server,'handle_workspace',Mock(side_effect=ValueError('fixture failure')))
    handler._dispatch('GET')
    assert handler.close_connection
    assert handler.send_json.call_args.args[1] == 500
