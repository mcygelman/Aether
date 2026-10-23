"""Real-file and HTTP checks for explicit conversion and user-authored presets."""
from settings_operator_fixture import OPERATOR_ID, OPERATOR, operator_registry, operator_options
import copy
import json
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from aether_bridge.model_budget_presets import BudgetPresetStore, BASIC
from aether_bridge.model_settings_store import StaleSettings
from aether.models.settings_store import SettingsStore
from aether_bridge.context_configuration import ContextConfigurationError
from aether_bridge.runtime_profiles import load_registry
from test_context_configuration import document
from test_model_settings_store import store_for
import model_context_settings as web


def catalog():
    p=document(2_000_000)['profiles']['research']
    return {'schemaVersion':1,'presets':{'Two million test envelope':
        {key:copy.deepcopy(p[key]) for key in (*BASIC,'contextBudget')}}}


def test_creation_duplicate_and_reload_preserve_original(tmp_path):
    store=store_for(tmp_path);loaded=store.read();draft=copy.deepcopy(loaded['document'])
    original=copy.deepcopy(draft['profiles']['research'])
    draft['profiles']['New deployment']=copy.deepcopy(original)
    draft['profiles']['New deployment']['requestedApiIdentifier']='a-distinct-alias'
    saved=store.save(draft,loaded['revision'], operator=OPERATOR_ID)
    assert saved['document']['profiles']['research']==original
    assert len(load_registry(store.path,budget_aware=True))==2
    assert SettingsStore(store.path, **operator_options(tmp_path)).read()==saved


def test_duplicate_alias_refuses_before_save(tmp_path):
    store=store_for(tmp_path);loaded=store.read();draft=copy.deepcopy(loaded['document'])
    draft['profiles']['Duplicate']=copy.deepcopy(draft['profiles']['research'])
    with pytest.raises(ContextConfigurationError,match='duplicate logical'):
        store.save(draft,loaded['revision'], operator=OPERATOR_ID)
    assert store.read()==loaded


def test_explicit_conversion_requires_all_budgets_and_remains_legacy_opt_in(tmp_path):
    store=store_for(tmp_path,version=1);loaded=store.read();draft=copy.deepcopy(loaded['document'])
    draft['schemaVersion']=2
    with pytest.raises(ContextConfigurationError):store.save(draft,loaded['revision'],migrate=True, operator=OPERATOR_ID)
    assert store.read()==loaded
    draft['profiles']['research']['contextBudget']=document(2_000_000)['profiles']['research']['contextBudget']
    saved=store.save(draft,loaded['revision'],migrate=True, operator=OPERATOR_ID)
    assert saved['document']==draft
    with pytest.raises(ContextConfigurationError,match='budget-aware'):load_registry(store.path)
    assert load_registry(store.path,budget_aware=True)['research'].context_budget is not None
    with pytest.raises(ContextConfigurationError):store.save(loaded['document'],saved['revision'],migrate=True, operator=OPERATOR_ID)
    assert store.read()==saved


def test_conversion_refuses_dropped_existing_profile_or_stale_revision(tmp_path):
    store=store_for(tmp_path,version=1);loaded=store.read();candidate=document(2_000_000)
    candidate['profiles']['replacement']=candidate['profiles'].pop('research')
    with pytest.raises(ContextConfigurationError,match='retain every'):store.save(candidate,loaded['revision'],migrate=True, operator=OPERATOR_ID)
    loaded['document']['profiles']['research']['maxToolCalls']=21
    newer=store.save(loaded['document'],loaded['revision'], operator=OPERATOR_ID)
    with pytest.raises(StaleSettings):store.save(document(2_000_000),loaded['revision'],migrate=True, operator=OPERATOR_ID)
    assert store.read()==newer


def test_preset_creation_update_delete_restart_is_separate_from_registry(tmp_path):
    registry=store_for(tmp_path);raw=registry.path.read_bytes();store=BudgetPresetStore(registry.path, **operator_options(tmp_path))
    loaded=store.read()
    assert not store.path.exists() and loaded['document']=={'schemaVersion':1,'presets':{}}
    saved=store.save(catalog(),loaded['revision'], operator=OPERATOR_ID)
    assert BudgetPresetStore(registry.path, **operator_options(tmp_path)).read()==saved
    assert registry.path.read_bytes()==raw
    candidate=copy.deepcopy(saved['document']);candidate['presets']['Two million test envelope']['maxToolCalls']=31
    updated=store.save(candidate,saved['revision'], operator=OPERATOR_ID)
    assert updated['document']['presets']['Two million test envelope']['maxToolCalls']==31
    deleted=store.save(loaded['document'],updated['revision'], operator=OPERATOR_ID)
    assert deleted['document']==loaded['document'] and registry.path.read_bytes()==raw


def test_two_editors_cannot_overwrite_first_preset_creation(tmp_path):
    store=BudgetPresetStore(tmp_path/'profiles.json', **operator_options(tmp_path));one=store.read();two=store.read()
    saved=store.save(catalog(),one['revision'], operator=OPERATOR_ID)
    with pytest.raises(StaleSettings):store.save(two['document'],two['revision'], operator=OPERATOR_ID)
    assert store.read()==saved


@pytest.mark.parametrize('field,value',[('maxToolCalls',True),('maxOutputTokens',3_000_000),
    ('timeoutSecs',0),('endpoint','https://example.invalid'),('apiKey','never-allowed')])
def test_invalid_preset_envelope_and_nonbudget_fields_refuse(tmp_path,field,value):
    store=BudgetPresetStore(tmp_path/'profiles.json', **operator_options(tmp_path));loaded=store.read();candidate=catalog()
    candidate['presets']['Two million test envelope'][field]=value
    with pytest.raises(ContextConfigurationError):store.save(candidate,loaded['revision'], operator=OPERATOR_ID)
    assert not store.path.exists()


def test_corrupt_existing_preset_file_is_not_replaced_by_empty_catalog(tmp_path):
    store=BudgetPresetStore(tmp_path/'profiles.json', **operator_options(tmp_path));store.path.write_text('{broken')
    with pytest.raises(ContextConfigurationError):store.read()
    assert store.path.read_text()=='{broken'


def test_preset_atomic_replacement_failure_preserves_file(tmp_path,monkeypatch):
    from aether_bridge import model_settings_store
    store=BudgetPresetStore(tmp_path/'profiles.json', **operator_options(tmp_path));saved=store.save(catalog(),store.read()['revision'], operator=OPERATOR_ID)
    def fail(*args):raise OSError('fixture replace failure')
    monkeypatch.setattr(model_settings_store.os,'replace',fail)
    with pytest.raises(OSError):store.save({'schemaVersion':1,'presets':{}},saved['revision'], operator=OPERATOR_ID)
    assert store.read()==saved and not list(tmp_path.glob('*.tmp'))


def test_http_conversion_and_preset_routes_keep_host_origin_revision_checks(tmp_path):
    store=store_for(tmp_path,version=1);server=web.bind(store)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    def request(route,payload=None,origin=base):
        return urlopen(Request(base+route,data=None if payload is None else json.dumps(payload).encode(),
            headers={'Content-Type':'application/json','Origin':origin}),timeout=3)
    try:
        loaded=json.load(request('/api/settings'));presets=json.load(request('/api/presets'))
        change={'document':document(2_000_000),'revision':loaded['revision'],'operator_id':OPERATOR_ID}
        for route,payload in [('/api/migrate',change),('/api/presets',{'document':catalog(),'revision':presets['revision'],'operator_id':OPERATOR_ID})]:
            with pytest.raises(HTTPError) as caught:request(route,payload,'https://untrusted.invalid')
            assert caught.value.code==403
        with pytest.raises(HTTPError) as caught:request('/api/save',change)
        assert caught.value.code==400
        converted=json.load(request('/api/migrate',change));assert converted['document']['schemaVersion']==2
        saved=json.load(request('/api/presets',{'document':catalog(),'revision':presets['revision'],'operator_id':OPERATOR_ID}))
        with pytest.raises(HTTPError) as caught:request('/api/presets',{'document':catalog(),'revision':presets['revision'],'operator_id':OPERATOR_ID})
        assert caught.value.code==409
        assert json.load(request('/api/presets'))==saved
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
