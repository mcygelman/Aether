"""Acceptance-checker review P2-1 (10-11): the campaign loader parsed JSON permissively, so a record the
product's own stores refuse (duplicate keys such as "decision":"REJECT","decision":"ACCEPT") projected
as its last duplicate. The loader now refuses it as a listed problem."""
from aether.campaign.records import _Reader, strict_loads

import pytest


def test_duplicate_keys_are_a_problem_not_a_record(tmp_path):
    record = tmp_path / "000001-decision.json"
    record.write_text('{"decision": "REJECT", "decision": "ACCEPT"}', encoding="utf-8")
    reader = _Reader()
    assert reader.anchor(tmp_path, "test") is not None   # reads happen only below a checked anchor
    assert reader.json(record) is None
    assert any("duplicate JSON key 'decision'" in p["error"] for p in reader.problems)
    assert str(record) not in reader.documents


@pytest.mark.parametrize("text", ['{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}'])
def test_non_finite_numbers_are_refused(text):
    with pytest.raises(ValueError):
        strict_loads(text)


def test_a_well_formed_record_still_reads(tmp_path):
    record = tmp_path / "000001-decision.json"
    record.write_text('{"decision": "ACCEPT", "nested": {"a": 1, "b": [1, 2]}}', encoding="utf-8")
    reader = _Reader()
    assert reader.anchor(tmp_path, "test") is not None
    assert reader.json(record) == {"decision": "ACCEPT", "nested": {"a": 1, "b": [1, 2]}}
    assert reader.problems == []
