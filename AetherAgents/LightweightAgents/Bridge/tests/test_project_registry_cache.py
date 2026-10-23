"""Exact-byte registry reuse must not retain caller mutations or stale authority."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os

import pytest

from aether_bridge import projects


@pytest.fixture(autouse=True)
def empty_cache():
    projects._retained_registry.cache_clear()
    yield
    projects._retained_registry.cache_clear()


@pytest.fixture
def registry(tmp_path):
    data = dict(active='p', extras={'labels': ['original']}, projects=[dict(
        id='p', name='Original', root=str(tmp_path/'project'), created_at='now',
        hired=[dict(template='t', instance_agent_id='worker', briefcase_path='b', hired_at='now')],
        notes={'items': ['original']})])
    path = tmp_path/'registry.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    return projects.ProjectRegistry(tmp_path/'lw', path), data


def test_repeated_load_and_other_instances_isolate_every_mutable_projection(registry):
    reg, data = registry; reg.load()
    reg.get('p').hired[0].template = 'changed'
    reg.get('p').extra['notes']['items'].append('changed')
    reg._extra['extras']['labels'].clear()
    other = projects.ProjectRegistry(reg.lightweight_dir, reg.registry_path); other.load()
    assert other.get('p').hired[0].template == 't'
    assert other.get('p').extra['notes']['items'] == ['original']
    assert other._extra['extras']['labels'] == ['original']
    reg.load()
    assert reg.list_projects() == other.list_projects()
    assert reg.get('p') is not other.get('p')
    assert projects._retained_registry.cache_info().hits == 2


def test_exact_current_bytes_are_read_even_with_same_length_and_timestamp(registry):
    reg, data = registry; reg.load(); stamp = reg.registry_path.stat()
    before = reg.registry_path.read_bytes()
    changed = before.replace(b'Original', b'Modified')
    assert len(changed) == len(before) and changed != before
    reg.registry_path.write_bytes(changed)
    os.utime(reg.registry_path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    reg.load()
    assert reg.get('p').name == 'Modified' and reg._raw == changed
    reg.registry_path.unlink()
    with pytest.raises(FileNotFoundError): reg.load()


def test_invalid_current_bytes_never_use_a_previous_valid_snapshot(registry):
    reg, data = registry; reg.load(); original = reg.registry_path.read_bytes()
    bad = deepcopy(data); bad['projects'][0]['framing'] = {'schema_version': 'forged'}
    reg.registry_path.write_text(json.dumps(bad), encoding='utf-8')
    for _ in range(2):
        with pytest.raises(ValueError, match='framing schema'): reg.load()
    assert projects._retained_registry.cache_info().currsize == 1
    reg.registry_path.write_bytes(original); reg.load()
    assert reg.get('p').name == 'Original'


def test_cached_read_does_not_waive_save_validation_or_compare_and_swap(registry):
    reg, data = registry; reg.load(); original = reg.registry_path.read_bytes()
    reg.get('p').framing = {'schema_version': 'forged'}
    with pytest.raises(ValueError, match='framing schema'): reg.save()
    assert reg.registry_path.read_bytes() == original
    reg.load(); other = projects.ProjectRegistry(reg.lightweight_dir, reg.registry_path); other.load()
    other.get('p').name = 'Other'; other.save()
    reg.get('p').name = 'Stale'
    with pytest.raises(ValueError, match='changed; reload'): reg.save()
    reg.load(); assert reg.get('p').name == 'Other'


def test_first_session_dependency_stays_uncached_even_when_json_escaped(registry, monkeypatch):
    reg, data = registry
    # A harmless extension carries the marker here: this is a unit test of the
    # conservative bypass, not a fabricated valid native qualification record.
    data['extension'] = [{'schema': 'aether-minder-first-session-preparation-1.0.0'}]
    raw = json.dumps(data).replace('aether-minder', r'\u0061ether-minder')
    reg.registry_path.write_text(raw, encoding='utf-8'); reg.load()
    assert projects._retained_registry.cache_info().currsize == 0
    def external_changed(*args): raise ValueError('External dependency changed')
    monkeypatch.setattr(projects, 'validate_framing', external_changed)
    with pytest.raises(ValueError, match='External dependency'): reg.load()


def test_large_registry_falls_back_without_growing_cache(registry, monkeypatch):
    reg, data = registry
    monkeypatch.setattr(projects, '_REGISTRY_CACHE_BYTES', 1)
    reg.load(); reg.load()
    assert projects._retained_registry.cache_info().currsize == 0
    assert reg.get('p').name == 'Original'


def test_cache_is_bounded_and_concurrent_readers_cannot_share_mutations(registry):
    reg, data = registry
    for i in range(6):
        data['generation'] = i
        reg.registry_path.write_text(json.dumps(data), encoding='utf-8'); reg.load()
    assert projects._retained_registry.cache_info().currsize == 4
    def read_and_change(i):
        local = projects.ProjectRegistry(reg.lightweight_dir, reg.registry_path); local.load()
        assert local.get('p').name == 'Original'
        local.get('p').extra['notes']['items'].append(i)
        return local.get('p').extra['notes']['items']
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(read_and_change, range(12)))
    assert rows == [['original', i] for i in range(12)]
    reg.load(); assert reg.get('p').extra['notes']['items'] == ['original']
