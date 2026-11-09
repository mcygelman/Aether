"""The Roadmap Tracker store inside the Bridge (ported from RoadMaps/Roadmap Tracker/tests)."""
import json
import re
from pathlib import Path

import pytest

from aether_bridge.roadmap_tracker.tracker_store import (DataValidationError, RevisionConflictError, RoadmapStore,
                                                         validate_data)

TRACKER_FILES = Path(__file__).resolve().parents[1] / 'aether' / 'roadmaps' / 'tracker'   # the page the server serves


def sample_data():
    return {
        'schemaVersion': 1,
        'roadmaps': [{'id': 'roadmap-one', 'name': 'Roadmap One', 'description': 'The first roadmap.', 'status': 'Active'}],
        'sprints': [{'id': 'sprint-one', 'roadmapId': 'roadmap-one', 'name': 'Sprint One',
                     'description': 'The first sprint.', 'status': 'Open', 'sequence': 1}],
    }


def test_valid_chain_passes():
    assert validate_data(sample_data()) == []


def test_missing_roadmap_reference_fails():
    data = sample_data()
    data['sprints'][0]['roadmapId'] = 'missing'
    assert any('references missing Roadmap' in error for error in validate_data(data))


def test_duplicate_sequence_within_roadmap_fails():
    data = sample_data()
    data['sprints'].append(dict(data['sprints'][0], id='sprint-two'))
    assert any('duplicate Sprint sequence' in error for error in validate_data(data))


def test_same_sequence_in_different_roadmaps_passes():
    data = sample_data()
    data['roadmaps'].append({'id': 'roadmap-two', 'name': 'Roadmap Two', 'description': 'The second roadmap.', 'status': 'Active'})
    data['sprints'].append(dict(data['sprints'][0], id='sprint-two', roadmapId='roadmap-two'))
    assert validate_data(data) == []


def test_unexpected_fields_fail_closed():
    data = sample_data()
    data['roadmaps'][0]['owner'] = 'Somebody'
    assert any('unexpected fields' in error for error in validate_data(data))


def test_active_roadmap_requires_unfinished_sprint():
    data = sample_data()
    data['sprints'][0]['status'] = 'Closed'
    assert any('must own at least one unfinished Sprint' in error for error in validate_data(data))


def test_completed_roadmap_rejects_unfinished_sprint():
    data = sample_data()
    data['roadmaps'][0]['status'] = 'Completed'
    assert any('cannot own unfinished Sprints' in error for error in validate_data(data))


@pytest.fixture
def store(tmp_path):
    path = tmp_path / 'roadmaps.json'
    path.write_text(json.dumps(sample_data()), encoding='utf-8')
    return RoadmapStore(path)


def test_closing_aborts_never_started_sprints_and_reopening_plans_again(store):
    # The Roadmaps page's Close roadmap and Reopen roadmap change statuses only (Michel, 11-05).
    data = sample_data()
    data['sprints'][0]['status'] = 'Closed'
    data['sprints'].append({'id': 'sprint-two', 'roadmapId': 'roadmap-one', 'name': 'Sprint Two',
                            'description': 'Never started.', 'status': 'Planned', 'sequence': 2})
    revision = store.write(data)
    data['roadmaps'][0]['status'] = 'Completed'
    with pytest.raises(DataValidationError):
        store.write(data, revision)
    data['sprints'][1]['status'] = 'Aborted'
    revision = store.write(data, revision)
    data['roadmaps'][0]['status'] = 'Planned'
    store.write(data, revision)
    assert [s['status'] for s in store.read()[0]['sprints']] == ['Closed', 'Aborted']


def test_round_trip_is_pretty_printed_and_valid(store):
    data, revision = store.read()
    next_revision = store.write(data, revision)
    assert revision != next_revision
    assert store.path.read_text(encoding='utf-8').endswith('\n')
    assert store.read()[0] == data


def test_stale_revision_is_rejected(store):
    data, revision = store.read()
    external = sample_data()
    external['roadmaps'][0]['name'] = 'External edit'
    store.path.write_text(json.dumps(external), encoding='utf-8')
    with pytest.raises(RevisionConflictError):
        store.write(data, revision)


def test_invalid_document_is_not_written(store):
    data, revision = store.read()
    data['sprints'][0]['status'] = 'Unknown'
    original = store.path.read_bytes()
    with pytest.raises(DataValidationError):
        store.write(data, revision)
    assert store.path.read_bytes() == original


def test_hidden_attribute_survives_author_display_rules():
    styles = (TRACKER_FILES / 'styles.css').read_text(encoding='utf-8')
    assert re.search(r'\[hidden\]\s*\{\s*display:\s*none\s*!important;\s*\}', styles)
