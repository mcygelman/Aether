"""Deployment budget contract tests: no endpoint/model calls or live profile writes."""
import copy
import json
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aether_bridge import runtime_profiles as profiles
from aether_bridge.context_configuration import ContextConfigurationError, resolve


def document(window=32768):
    return {'schemaVersion': 2, 'profiles': {'research': {
        'description': 'Synthetic deployment, not a provider capability claim',
        'backend': 'lmstudio', 'endpoint': 'http://127.0.0.1:1234/v1',
        'requestedApiIdentifier': 'test-research', 'identifierKind': 'logical-alias',
        'contextWindow': window, 'maxOutputTokens': 2048,
        'capabilitySource': 'operator-declared test configuration',
        'timeoutSecs': 60, 'maxToolCalls': 8, 'dialect': 'openai',
        'contextBudget': {'workingContextTokens': window, 'toolResultReserveTokens': 1000,
                          'safetyMarginTokens': 512, 'minimumInputTokens': 1024,
                          'maxToolResultChars': 3000}}}}


def profile(raw=None):
    return profiles.validate_registry(document() if raw is None else raw,
                                      budget_aware=True)['research']


def test_round_trip_and_no_million_token_ceiling(tmp_path):
    raw = document(2_000_000)
    path = tmp_path / 'profiles.json'
    path.write_text(json.dumps(raw), encoding='utf-8')
    loaded = profiles.load('research', path, budget_aware=True)
    assert loaded.to_mapping() == raw['profiles']['research']
    assert resolve(loaded).preview(1_500_000, measurement='tokenizer')['fits']


def test_existing_consumers_cannot_silently_ignore_new_settings():
    with pytest.raises(profiles.RuntimeProfileError, match='budget-aware consumer'):
        profiles.validate_registry(document())


@pytest.mark.parametrize('bad', [True, False, 8192.5, '8192', None, -1])
def test_strict_integer_fields(bad):
    raw = document()
    raw['profiles']['research']['contextWindow'] = bad
    with pytest.raises(profiles.RuntimeProfileError):
        profile(raw)
    raw = document()
    raw['profiles']['research']['contextBudget']['safetyMarginTokens'] = bad
    with pytest.raises(profiles.RuntimeProfileError):
        profile(raw)


def test_missing_unknown_and_inconsistent_settings():
    raw = document()
    del raw['profiles']['research']['contextBudget']['minimumInputTokens']
    with pytest.raises(profiles.RuntimeProfileError, match='exactly'):
        profile(raw)
    raw = document()
    raw['profiles']['research']['contextBudget']['tierCeiling'] = 8192
    with pytest.raises(profiles.RuntimeProfileError, match='exactly'):
        profile(raw)
    raw = document()
    raw['profiles']['research']['contextBudget']['workingContextTokens'] = 4096
    with pytest.raises(profiles.RuntimeProfileError, match='leaves 536 input tokens'):
        profile(raw)
    raw = document()
    raw['profiles']['research']['contextBudget']['workingContextTokens'] = 32769
    with pytest.raises(profiles.RuntimeProfileError, match='exceeds declared'):
        profile(raw)


def test_same_profile_different_working_budgets_with_explicit_provenance(monkeypatch):
    monkeypatch.setenv('AETHER_DYNAMIC_FLOOR', '9999999')
    original = profile()
    small = resolve(original, overrides={'workingContextTokens': 8192})
    large = resolve(original)
    assert small.profile_digest == large.profile_digest
    assert small.revision != large.revision
    assert dict(small.provenance)['workingContextTokens'] == 'run override'
    assert not small.preview(10000, measurement='estimate')['fits']
    assert large.preview(10000, measurement='estimate')['fits']
    assert original.context_budget.working_context_tokens == 32768
    with pytest.raises(FrozenInstanceError):
        small.budget.working_context_tokens = 32768


def test_checkpoint_preview_and_governor_ledger_use_same_reserves():
    snapshot = resolve(profile(), overrides={'workingContextTokens': 8192})
    assert snapshot.ledger().usable_input_budget == 4632
    assert snapshot.ledger(bootstrap_cost_tokens=100).usable_input_budget == 4532
    # Full requests already include bootstrap: preview must not subtract it twice.
    initial = snapshot.preview(4632, measurement='tokenizer')
    assert initial['fits'] and initial['remaining_input_tokens'] == 0
    next_request = snapshot.preview(4700, measurement='tokenizer')
    assert not next_request['fits']
    assert next_request['required_context_tokens'] == 8260
    assert '68 tokens' in next_request['detail']
    assert initial['revision'] == next_request['revision']
    with pytest.raises(ContextConfigurationError, match='Bootstrap'):
        snapshot.ledger(bootstrap_cost_tokens=5000)


def test_verified_limits_and_unknowns_are_separate_from_declarations():
    default = resolve(profile())
    assert default.verified_context_limit is None
    with pytest.raises(ContextConfigurationError, match='require a source'):
        resolve(profile(), verified_context_limit=8192)
    with pytest.raises(ContextConfigurationError, match='exceeds verified context'):
        resolve(profile(), verified_context_limit=8192, verified_limit_source='test observation')
    with pytest.raises(ContextConfigurationError, match='exceeds verified output'):
        resolve(profile(), verified_output_limit=1024, verified_limit_source='test observation')
    accepted = resolve(profile(), overrides={'workingContextTokens': 8192},
                       verified_context_limit=8192, verified_limit_source='test observation')
    assert accepted.declared_context_tokens == 32768
    assert accepted.verified_context_limit == 8192


def test_overrides_and_measurement_fail_clearly():
    for overrides in ({'tier': 'small'}, {'maxOutputTokens': True},
                      {'maxOutputTokens': 999999}, {'workingContextTokens': 0}):
        with pytest.raises(ContextConfigurationError):
            resolve(profile(), overrides=overrides)
    with pytest.raises(ContextConfigurationError, match='measurement'):
        resolve(profile()).preview(10, measurement='guessed exact')
    with pytest.raises(ContextConfigurationError):
        resolve(profile()).preview(-1, measurement='estimate')


def test_migration_requires_explicit_values_and_preserves_existing_fields():
    raw = document()
    raw['profiles']['research']['description'] = '  Preserve authored whitespace  '
    raw['profiles']['research']['reasoningMode'] = None
    budgets = {'research': raw['profiles']['research'].pop('contextBudget')}
    raw['schemaVersion'] = 1
    before = copy.deepcopy(raw)
    with pytest.raises(profiles.RuntimeProfileError, match='exactly the existing'):
        profiles.migrate_registry(raw, {})
    migrated = profiles.migrate_registry(raw, budgets)
    assert raw == before
    assert migrated['schemaVersion'] == 2
    assert {k: v for k, v in migrated['profiles']['research'].items()
            if k != 'contextBudget'} == before['profiles']['research']
    with pytest.raises(ContextConfigurationError, match='migrate'):
        resolve(profiles.validate_registry(raw)['research'])


def test_snapshot_survives_configuration_edits():
    raw = document()
    admitted = resolve(profile(raw))
    revision = admitted.revision
    raw['profiles']['research']['contextBudget']['workingContextTokens'] = 8192
    newer = resolve(profile(raw))
    assert admitted.revision == revision
    assert admitted.budget.working_context_tokens == 32768
    assert newer.budget.working_context_tokens == 8192
    assert newer.revision != admitted.revision


@pytest.mark.parametrize('source', [[], {}, 1, True, '', '  '])
def test_optional_verified_source_cannot_inject_mutable_or_invalid_provenance(source):
    with pytest.raises(ContextConfigurationError, match='source must be nonblank text'):
        resolve(profile(), verified_limit_source=source)


@pytest.mark.parametrize('source', [None, [], {}, True, ''])
def test_v2_rejects_missing_or_nontextual_capability_provenance(source):
    raw = document()
    raw['profiles']['research']['capabilitySource'] = source
    with pytest.raises(profiles.RuntimeProfileError, match='capabilitySource must be nonblank text'):
        profile(raw)
