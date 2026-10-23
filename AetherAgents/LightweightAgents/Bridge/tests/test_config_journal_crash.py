"""ConfigJournal is crash-safe (10-08 list, item 10): a crash at any point of a change leaves one
consistent state, and never a value change without its journal entry."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aether.config.journal import ConfigJournal  # noqa: E402


class Crash(Exception):
    pass


def crash_on_value_write(monkeypatch, journal):
    real = ConfigJournal._write_atomic

    def write(path, text):
        if Path(path) == journal.path:
            raise Crash("power cut before the value was written")
        return real(path, text)
    monkeypatch.setattr(ConfigJournal, "_write_atomic", staticmethod(write))


@pytest.fixture
def journal(tmp_path):
    j = ConfigJournal(tmp_path / "settings.json", clock=lambda: "2026-10-08T00:00:00Z")
    j.set("memory.minds_folder", "D:/Minds", by="michel", reason="first")
    return j


def test_a_crash_after_the_entry_is_finished_on_the_next_read(journal, monkeypatch):
    crash_on_value_write(monkeypatch, journal)
    with pytest.raises(Crash):
        journal.set("memory.minds_folder", "E:/Minds", by="michel", reason="moved")
    assert json.loads(journal.path.read_text(encoding="utf-8"))["memory.minds_folder"] == "D:/Minds"
    monkeypatch.undo()
    fresh = ConfigJournal(journal.path)
    assert fresh.get("memory.minds_folder") == "E:/Minds"                 # rolled forward
    assert json.loads(journal.path.read_text(encoding="utf-8"))["memory.minds_folder"] == "E:/Minds"
    assert [c.reason for c in fresh.history()] == ["moved", "first"]     # recorded once


def test_a_crashed_removal_is_finished_too(journal, monkeypatch):
    crash_on_value_write(monkeypatch, journal)
    with pytest.raises(Crash):
        journal.remove("memory.minds_folder", by="michel", reason="cleared")
    monkeypatch.undo()
    assert ConfigJournal(journal.path).all() == {}


def test_a_torn_entry_is_discarded_and_the_old_value_stands(journal):
    with journal.journal_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write('{"id": 2, "at": "2026-10-08T00:00:01Z", "by": "mich')    # crash mid-entry
    fresh = ConfigJournal(journal.path, clock=lambda: "2026-10-08T00:00:02Z")
    assert fresh.get("memory.minds_folder") == "D:/Minds"
    assert [c.id for c in fresh.history()] == [1]
    change = fresh.set("memory.minds_folder", "F:/Minds", by="michel", reason="after the crash")
    assert change.id == 2
    lines = journal.journal_path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["reason"] for line in lines] == ["first", "after the crash"]


def test_a_crash_before_the_entry_changes_nothing(journal, monkeypatch):
    def no_append(self, change):
        raise Crash("power cut before the journal entry")
    monkeypatch.setattr(ConfigJournal, "_append", no_append)
    with pytest.raises(Crash):
        journal.set("memory.minds_folder", "G:/Minds", by="michel", reason="never happened")
    monkeypatch.undo()
    fresh = ConfigJournal(journal.path)
    assert fresh.get("memory.minds_folder") == "D:/Minds" and len(fresh.history()) == 1


def test_a_value_changed_outside_the_journal_is_never_overwritten(journal):
    journal.path.write_text(json.dumps({"memory.minds_folder": "H:/Edited"}), encoding="utf-8")
    assert ConfigJournal(journal.path).get("memory.minds_folder") == "H:/Edited"
