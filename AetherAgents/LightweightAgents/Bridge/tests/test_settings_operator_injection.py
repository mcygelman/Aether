"""Operator lookup is injected into Model Catalog stores; all records are disposable."""
import json
from pathlib import Path

import pytest

from aether.config.context_configuration import ContextConfigurationError
from aether.models.model_budget_presets import BudgetPresetStore
from aether.models.settings_store import SettingsStore
from aether.roadmaps.operators import reader
from settings_operator_fixture import OPERATOR, OPERATOR_ID, operator_registry
from test_model_settings_store import store_for

MESSAGE = 'Registered operator identity unavailable'


def audit_path(store):
    return store.path.with_name(store.path.name+'.audit.jsonl')


@pytest.mark.parametrize('presets', [False, True])
def test_store_without_operators_refuses_save(tmp_path, presets):
    fixture = store_for(tmp_path)
    store = (BudgetPresetStore if presets else SettingsStore)(fixture.path, operator_registry_path=operator_registry(tmp_path))
    loaded = store.read()
    with pytest.raises(ContextConfigurationError, match=MESSAGE): store.registered_operators()
    with pytest.raises(ContextConfigurationError, match=MESSAGE):
        store.save(loaded['document'], loaded['revision'], operator=OPERATOR_ID)
    assert store.read() == loaded and not audit_path(store).exists()


@pytest.mark.parametrize('damage', ['corrupt', 'bad_operator', 'bad_project'])
def test_damaged_registry_blocks_save_through_reader(tmp_path, damage):
    store = store_for(tmp_path); loaded = store.read()
    registry_path = operator_registry(tmp_path)
    assert store.operators() == [OPERATOR] and reader(registry_path)() == [OPERATOR]
    registry = json.loads(registry_path.read_text())
    if damage == 'corrupt':
        registry_path.write_text('{"local_operator": ', encoding='utf-8')
    else:
        if damage == 'bad_operator':
            registry['local_operator']['id'] = 'not-a-uuid'
        else:
            registry['projects'] = [{'id': 'broken'}]  # Whole-registry validation, not just the operator.
        registry_path.write_text(json.dumps(registry), encoding='utf-8')
    with pytest.raises(ContextConfigurationError, match=MESSAGE): reader(registry_path)()
    with pytest.raises(ContextConfigurationError, match=MESSAGE):
        store.save(loaded['document'], loaded['revision'], operator=OPERATOR_ID)
    assert store.read() == loaded and not audit_path(store).exists()


def test_model_catalog_source_never_imports_roadmaps():
    package = Path(__file__).resolve().parents[1] / 'aether' / 'models'
    offenders = [path.name for path in package.rglob('*.py')
                 if 'aether.roadmaps' in path.read_text(encoding='utf-8')
                 or 'from ..roadmaps' in path.read_text(encoding='utf-8')]
    assert offenders == []
