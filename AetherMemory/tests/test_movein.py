"""Move-in box: the agent's walkthrough and its own move-in sheet."""
import hashlib
import json
import os

import pytest

from memory_fabric import MemoryEngine
from memory_fabric import movein
from memory_fabric.__main__ import main
from memory_fabric.importer import ImporterError
from memory_fabric.movein import MoveInError
from tests.test_importer import FILES

INS = "INS_20260805_Configured_Is_Not_Capability"
INT = "INT_20260805_Configured_Is_Not_Capability"
EXP_OLD = "EXP_20260725_S00076_Scoped_Edits"
EXP_NEW = "EXP_20260805_HDEC021_Window"
AVATAR = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
          '<circle cx="5" cy="5" r="4" fill="#2a9d8f"/></svg>')


@pytest.fixture
def profile(tmp_path):
    memory = tmp_path / "Solace" / "memory"
    for rel, text in FILES.items():
        path = memory / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return str(memory)


@pytest.fixture
def home(tmp_path):
    folder = tmp_path / "minds"
    folder.mkdir()
    return folder


def _digest(root):
    h = hashlib.sha256()
    for folder, _, names in sorted(os.walk(root)):
        for name in sorted(names):
            path = os.path.join(folder, name)
            h.update(path.encode()); h.update(open(path, "rb").read())
    return h.hexdigest()


def _sheet(**extra):
    sheet = {
        "format": "aether-move-in/1", "agent": "solace", "written_by": "solace",
        "note": "The rule about ceilings is who I am; the old leg logs can rest.",
        "_comment": "comments are ignored",
        "card": {"name": "Solace", "scan_line": "Steward of the house; verifies from inside."},
        "records": {
            INS: {"importance": "vital", "tags_add": ["ceilings"], "_why": "my keystone"},
            EXP_OLD: {"kind": "lesson", "title": "edit_file was born here",
                      "tags_remove": ["experience"], "current_until": "2027-07-25"},
            EXP_NEW: {"bury": True},
            INT: {"proprietary": True},
        },
        "links": [{"from": INS, "to": EXP_OLD, "relation": "causal", "strength": "strong"}],
        "remember": [{"title": "Check from inside", "body": "Verify the house from inside it.",
                      "kind": "principle", "tags": ["verification"],
                      "links": [{"to": INS, "relation": "conceptual"}]}],
    }
    sheet.update(extra)
    return sheet


def test_walkthrough_shows_the_home_and_touches_nothing(profile, home, clock):
    before = _digest(os.path.dirname(profile))
    result = movein.walkthrough(profile, "solace", str(home / "walk"), clock=clock)
    plan_text = open(result["floor_plan"], encoding="utf-8").read()
    assert INS in plan_text and "Not moved" not in plan_text
    assert "Configured Is Not Capability" in open(result["preview"], encoding="utf-8").read()
    sheet = json.load(open(result["sheet"], encoding="utf-8"))
    assert sheet["written_by"] == "solace" and sheet["records"] == {}
    assert _digest(os.path.dirname(profile)) == before                # the profile is untouched
    assert len(os.listdir(home / "walk")) == 3                        # nothing else is kept
    with pytest.raises(ImporterError):
        movein.walkthrough(profile, "solace", os.path.join(profile, "..", "walk"), clock=clock)


def test_the_agent_arranges_its_own_home(profile, home, clock, tmp_path):
    svg = tmp_path / "avatar.svg"
    svg.write_text(AVATAR, encoding="utf-8")
    sheet_path = tmp_path / "MOVE_IN.json"
    sheet_path.write_text(json.dumps(_sheet(avatar={"svg_file": "avatar.svg", "original_design": True,
                                                    "description": "A calm teal circle."})))
    with MemoryEngine.create(str(home / "solace.sqlite"), "solace", clock=clock) as mind:
        report = mind.move_in(profile, str(sheet_path))
        assert report.imported["written"] == 4 and report.changed_records == 4
        assert report.buried == 1 and report.links_added == 1 and report.remembered == 1
        ins = mind.note(INS)
        assert ins["importance"] >= mind.settings()["fog_importance_min"]          # vital
        assert "ceilings" in ins["tags"]
        old = mind.note(EXP_OLD)
        assert old["title"] == "edit_file was born here" and old["profile"] == "standing_lesson"
        assert "experience" not in old["tags"] and old["switch_at"].startswith("2027-07-25")
        assert mind.note(EXP_NEW)["strength"] == pytest.approx(0.1)               # buried, kept
        assert mind.note(INT)["proprietary"] == 1
        card = mind.card()
        assert card["name"] == "Solace" and card["scan_line"].startswith("Steward")
        assert card["avatar_svg"] == AVATAR
        moving = mind.note(report.move_in_note_id)
        assert moving["title"] == "Moving in" and "who I am" in moving["body"]
        assert mind.recall(["move-in"]).ids() == [report.move_in_note_id]
        assert mind.recall(["verification"]).ids()[0] != INS          # the new memory leads


def test_only_the_agent_can_write_its_sheet(profile, home, clock):
    for author in ("claude", "operator", "praxis"):
        with MemoryEngine.create(str(home / f"{author}.sqlite"), "solace", clock=clock) as mind:
            with pytest.raises(MoveInError, match="only solace can arrange"):
                mind.move_in(profile, _sheet(written_by=author))
            assert mind.cart.conn.execute("SELECT count(*) FROM notes").fetchone()[0] == 2  # supplied orientation resources


def test_every_problem_is_listed_at_once_and_nothing_is_written(profile, home, clock):
    bad = _sheet(records={
        "EXP_19990101_Nope": {"bury": True},
        INS: {"importance": "extremely", "bury": True, "colour": "blue"},
        EXP_OLD: {"current_until": "someday", "current_for_days": 3},
    }, links=[{"from": INS, "to": "missing"}], remember=[{"title": "", "kind": "blorp"}])
    with MemoryEngine.create(str(home / "s.sqlite"), "solace", clock=clock) as mind:
        with pytest.raises(MoveInError) as caught:
            mind.move_in(profile, bad)
        problems = "\n".join(caught.value.problems)
        for expected in ("no such record", "importance: one of", "unknown field 'colour'",
                         "not both", "a date like", "links[0].to", "remember[0].title",
                         "remember[0].body", "remember[0].kind"):
            assert expected in problems
        assert mind.cart.conn.execute("SELECT count(*) FROM notes").fetchone()[0] == 2  # supplied orientation resources


def test_a_sheet_applies_once_on_move_in_day(profile, home, clock):
    with MemoryEngine.create(str(home / "s.sqlite"), "solace", clock=clock) as mind:
        mind.move_in(profile, _sheet())
        with pytest.raises(MoveInError, match="already applied"):
            mind.move_in(profile, _sheet())
        with pytest.raises(MoveInError, match="already moved in"):
            mind.move_in(profile, _sheet(note="changed my mind"))


def test_cli_move_in_is_all_or_nothing(profile, home, tmp_path):
    out = str(home / "solace.sqlite")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(_sheet(written_by="claude")))
    assert main(["import-profile", profile, "solace", out, "--move-in", str(bad)]) == 1
    assert not os.path.exists(out) and not os.path.exists(out + ".moving-in")
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_sheet()))
    assert main(["import-profile", profile, "solace", out, "--move-in", str(good)]) == 0
    with MemoryEngine.open(out, read_only=True) as mind:
        assert mind.open_report.status == "sealed"
        assert mind.card()["name"] == "Solace"


def test_walkthrough_previews_the_sheet_and_reports_problems(profile, home, tmp_path, clock):
    result = movein.walkthrough(profile, "solace", str(home / "walk"), _sheet(), clock=clock)
    assert result["problems"] == []
    assert "edit_file was born here" in open(result["preview"], encoding="utf-8").read()
    result = movein.walkthrough(profile, "solace", str(home / "walk2"),
                                _sheet(written_by="claude"), clock=clock)
    assert any("only solace" in p for p in result["problems"])
    bad_json = tmp_path / "broken.json"
    bad_json.write_text("{ not json")
    with pytest.raises(MoveInError, match="not valid JSON"):
        movein.load_sheet(str(bad_json))
