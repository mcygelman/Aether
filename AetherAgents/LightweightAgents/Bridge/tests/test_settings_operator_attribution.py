"""Real registered identities on settings writes; all records are disposable."""
import inspect
import json

import pytest

from aether_bridge.context_configuration import ContextConfigurationError
from aether_bridge.model_settings_store import StaleSettings, writer_lock
from aether.models.settings_store import SettingsStore
from aether_bridge.model_budget_presets import BudgetPresetStore
from aether.roadmaps.operators import reader
from settings_operator_fixture import OPERATOR_ID, OPERATOR, operator_registry
from test_model_settings_store import store_for


def target_store(tmp_path, presets):
    store = store_for(tmp_path)
    return BudgetPresetStore(store.path, operator_registry_path=store.operator_registry_path, operators=store.operators) if presets else store


@pytest.mark.parametrize('presets', [False, True])
def test_operator_is_a_required_keyword_and_omission_writes_nothing(tmp_path, presets):
    store = target_store(tmp_path, presets); loaded = store.read()
    parameter = inspect.signature(store.save).parameters['operator']
    assert parameter.default is inspect.Parameter.empty and parameter.kind is inspect.Parameter.KEYWORD_ONLY
    with pytest.raises(TypeError, match='operator'): store.save(loaded['document'], loaded['revision'])
    assert store.read() == loaded
    assert not store.path.with_name(store.path.name+'.audit.jsonl').exists()


@pytest.mark.parametrize('presets', [False, True])
@pytest.mark.parametrize('identity', ['local_operator', None, '', OPERATOR,
    '4b1fae83-0253-4e12-9b81-fb3d7b214d41'])
def test_unregistered_or_invented_attribution_never_saves(tmp_path, presets, identity):
    store = target_store(tmp_path, presets); loaded = store.read()
    with pytest.raises(ContextConfigurationError):
        store.save(loaded['document'], loaded['revision'], operator=identity)
    assert store.read() == loaded
    assert not store.path.with_name(store.path.name+'.audit.jsonl').exists()


@pytest.mark.parametrize('presets', [False, True])
def test_audit_keeps_registered_name_at_each_write(tmp_path, presets):
    store = target_store(tmp_path, presets); loaded = store.read()
    first = store.save(loaded['document'], loaded['revision'], operator=OPERATOR_ID)
    path = store.path.with_name(store.path.name+'.audit.jsonl'); original = path.read_bytes()
    registry_path = operator_registry(tmp_path)
    registry = json.loads(registry_path.read_text())
    registry['local_operator'].update(display_name='Renamed Fixture Operator', revision=2)
    registry_path.write_text(json.dumps(registry), encoding='utf-8')
    store.save(first['document'], first['revision'], operator=OPERATOR_ID)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert records[1]['operator'] == OPERATOR
    assert records[-1]['operator'] == {'id': OPERATOR_ID, 'display_name': 'Renamed Fixture Operator'}
    assert path.read_bytes().startswith(original)


def test_removed_operator_and_registry_lock_block_write(tmp_path):
    store = store_for(tmp_path); loaded = store.read()
    with writer_lock(store.operator_registry_path.with_name(store.operator_registry_path.name+'.lock')):
        with pytest.raises(StaleSettings): store.save(loaded['document'], loaded['revision'], operator=OPERATOR_ID)
    registry = json.loads(store.operator_registry_path.read_text()); del registry['local_operator']
    store.operator_registry_path.write_text(json.dumps(registry), encoding='utf-8')
    with pytest.raises(ContextConfigurationError): store.save(loaded['document'], loaded['revision'], operator=OPERATOR_ID)
    assert store.read() == loaded


def test_missing_registry_is_not_provisioned_by_read_or_save(tmp_path):
    fixture = store_for(tmp_path)
    missing = tmp_path/'missing-projects.json'
    store = SettingsStore(fixture.path, operator_registry_path=missing, operators=reader(missing)); loaded = store.read()
    with pytest.raises(ContextConfigurationError): store.registered_operators()
    with pytest.raises(ContextConfigurationError): store.save(loaded['document'], loaded['revision'], operator=OPERATOR_ID)
    assert not missing.exists()
    assert store.read() == loaded
