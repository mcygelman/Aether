from settings_operator_fixture import OPERATOR_ID, OPERATOR, operator_registry, operator_options
import copy
import json
import sys
import threading
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aether_bridge.model_settings_store import StaleSettings, writer_lock
from aether.models.settings_store import SettingsStore, preview
from aether_bridge.context_configuration import ContextConfigurationError, resolve
from aether_bridge import runtime_profiles
import model_context_settings as web
from test_context_configuration import document


def store_for(tmp_path, version=2):
    raw=document(2_000_000)
    if version==1:
        raw['schemaVersion']=1
        del raw['profiles']['research']['contextBudget']
    path=tmp_path/'profiles.json'
    path.write_text(json.dumps(raw),encoding='utf-8')
    return SettingsStore(path, **operator_options(tmp_path))


def test_save_roundtrip_and_restart_preserve_effective_large_values(tmp_path):
    store=store_for(tmp_path);loaded=store.read()
    changed=copy.deepcopy(loaded['document'])
    changed['profiles']['research']['contextBudget']['workingContextTokens']=1_500_000
    before=resolve(runtime_profiles.validate_registry(loaded['document'],budget_aware=True)['research'])
    saved=store.save(changed,loaded['revision'], operator=OPERATOR_ID)
    assert SettingsStore(store.path, **operator_options(tmp_path)).read()==saved
    assert saved['document']==changed
    after=resolve(runtime_profiles.load('research',store.path,budget_aware=True))
    assert after.budget.working_context_tokens==1_500_000
    assert before.budget.working_context_tokens==2_000_000
    assert before.revision!=after.revision


def test_stale_edit_never_overwrites_newer_values(tmp_path):
    store=store_for(tmp_path);first=store.read();second=store.read()
    first['document']['profiles']['research']['maxToolCalls']=11
    saved=store.save(first['document'],first['revision'], operator=OPERATOR_ID)
    second['document']['profiles']['research']['maxToolCalls']=12
    with pytest.raises(StaleSettings,match='changed since'):
        store.save(second['document'],second['revision'], operator=OPERATOR_ID)
    assert store.read()==saved


def test_invalid_and_failed_replacement_preserve_file(tmp_path,monkeypatch):
    import aether_bridge.model_settings_store as module
    store=store_for(tmp_path);loaded=store.read();before=store.path.read_bytes()
    bad=copy.deepcopy(loaded['document']);bad['profiles']['research']['maxOutputTokens']=True
    with pytest.raises(ContextConfigurationError):store.save(bad,loaded['revision'], operator=OPERATOR_ID)
    assert store.path.read_bytes()==before
    def fail(*args):raise OSError('simulated replace failure')
    monkeypatch.setattr(module.os,'replace',fail)
    with pytest.raises(OSError):store.save(loaded['document'],loaded['revision'], operator=OPERATOR_ID)
    assert store.path.read_bytes()==before
    assert not list(tmp_path.glob('*.tmp'))


def test_external_edit_during_staging_is_preserved(tmp_path,monkeypatch):
    import aether_bridge.model_settings_store as module
    store=store_for(tmp_path);loaded=store.read()
    newer=copy.deepcopy(loaded['document']);newer['profiles']['research']['maxToolCalls']=99
    original=module.os.fsync
    def edit(fd):
        original(fd);store.path.write_text(json.dumps(newer),encoding='utf-8')
    monkeypatch.setattr(module.os,'fsync',edit)
    with pytest.raises(StaleSettings,match='during save'):
        store.save(loaded['document'],loaded['revision'], operator=OPERATOR_ID)
    assert store.read()['document']==newer


def test_writer_lock_refuses_overlapping_save(tmp_path):
    store=store_for(tmp_path);loaded=store.read()
    with writer_lock(store.path.with_name(store.path.name+'.lock')):
        with pytest.raises(StaleSettings,match='in progress'):
            store.save(loaded['document'],loaded['revision'], operator=OPERATOR_ID)


def test_no_implicit_format_migration(tmp_path):
    store=store_for(tmp_path,version=1);loaded=store.read()
    with pytest.raises(ContextConfigurationError,match='explicit profile migration'):
        store.save(document(),loaded['revision'], operator=OPERATOR_ID)
    assert store.read()==loaded


def test_preview_distinguishes_unknown_supplied_counts_and_legacy_path(tmp_path):
    raw=document()
    result=preview(raw,'research')
    assert result['request'] is None and result['verified_limits'] is None
    assert result['input_allowance']==32768-2048-1000-512
    assert not preview(raw,'research',100000,'estimate')['request']['fits']
    assert preview(raw,'research',100,'tokenizer')['request']['measurement']=='tokenizer'
    legacy=store_for(tmp_path,version=1).read()['document']
    assert preview(legacy,'research',100)['complete_budget'] is False
    assert preview(legacy,'research')['input_allowance'] is None


def test_duplicate_or_malformed_json_fails_without_rewrite(tmp_path):
    store=store_for(tmp_path)
    raw=store.path.read_text().replace('"schemaVersion": 2','"schemaVersion": 2, "schemaVersion": 1')
    store.path.write_text(raw)
    with pytest.raises(ContextConfigurationError,match='Duplicate'):store.read()
    assert store.path.read_text()==raw


def test_local_http_preview_save_and_cross_origin_rejection(tmp_path):
    store=store_for(tmp_path);server=web.bind(store)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    def request(path,payload=None,origin=None,host=None):
        headers={'Content-Type':'application/json'}
        if origin is not None:headers['Origin']=origin
        if host is not None:headers['Host']=host
        data=None if payload is None else json.dumps(payload).encode()
        return urlopen(Request(base+path,data=data,headers=headers),timeout=3)
    try:
        loaded=json.load(request('/api/settings'))
        assert 'Models & Context' in request('/').read().decode()
        candidate=loaded['document'];candidate['profiles']['research']['maxToolCalls']=12
        payload={'document':candidate,'revision':loaded['revision'],'operator_id':OPERATOR_ID}
        with pytest.raises(HTTPError) as error:request('/api/save',payload,'https://untrusted.example')
        assert error.value.code==403
        with pytest.raises(HTTPError) as error:request('/api/settings',host='untrusted.example')
        assert error.value.code==403
        calculated=json.load(request('/api/preview',{'document':candidate,'profile':'research'},base))
        assert calculated['request'] is None
        saved=json.load(request('/api/save',payload,base))
        assert saved['document']['profiles']['research']['maxToolCalls']==12
        with pytest.raises(HTTPError) as error:request('/api/save',payload,base)
        assert error.value.code==409
        assert store.read()=={k:saved[k] for k in ('document','revision','path')}
        from aether_bridge.token_count_policy import load_default
        assert saved['tokenCountPolicyDefaults']==load_default().to_mapping()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
