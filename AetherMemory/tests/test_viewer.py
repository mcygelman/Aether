"""Viewer box: read-only snapshot + self-contained HTML page."""
import hashlib
import json
import re

import pytest

from memory_fabric import MemoryEngine
from memory_fabric import viewer
from memory_fabric.__main__ import main


def _mind(path, clock):
    eng = MemoryEngine.create(path, "grace", name="Grace", clock=clock)
    code = eng.remember("Door code", "4471 opens the upstairs door", "project fact",
                        ["upstairs", "door code"], switch_in_days=7)
    eng.remember("Door alarm", "Two wrong codes </script><script>alert(1)</script>",
                 "safety warning", ["door code", "alarm"], category="Alert",
                 links=[(code, "context", 0.9)])
    eng.remember("Client palette", "secret hex values", "lesson", ["palette"], proprietary=True)
    eng.set_avatar('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                   '<circle cx="5" cy="5" r="4" fill="teal"/></svg>', "A teal circle.", True)
    return eng


def test_snapshot_strength_over_time_matches_engine(tmp_path, clock):
    eng = _mind(str(tmp_path / "g.sqlite"), clock)
    snap = viewer.snapshot(eng)
    assert snap["horizons"] == [0, 7, 30, 90, 365]
    door = next(n for n in snap["notes"] if n["title"] == "Door code")
    # today's value is exactly what the engine reports, and a project fact fades after its switch
    assert door["strength"][0] == pytest.approx(eng.note(door["id"])["strength"], abs=1e-4)
    assert door["strength"][3] < door["strength"][1] <= door["strength"][0]
    assert len(snap["links"]) == 1 and len(snap["links"][0]["strength"]) == 5
    # the clock is restored afterwards
    assert eng.note(door["id"])["strength"] == pytest.approx(door["strength"][0], abs=1e-4)
    json.dumps(snap)


def test_privacy_options(tmp_path, clock):
    eng = _mind(str(tmp_path / "g.sqlite"), clock)
    snap = viewer.snapshot(eng, include_proprietary=False, include_bodies=False)
    assert all(not n["proprietary"] for n in snap["notes"])
    assert snap["counts"]["hidden_proprietary"] == 1
    assert all(n["body"] == "" for n in snap["notes"])


def test_html_is_self_contained_and_escapes_data(tmp_path, clock):
    eng = _mind(str(tmp_path / "g.sqlite"), clock)
    page = viewer.render_html(viewer.snapshot(eng))
    assert page.count("<script") == 2                       # the data block and the app
    assert "</script><script>alert(1)" not in page
    assert not re.search(r"(?:src|href)=\"https?:", page)   # no network
    assert "data:image/svg+xml;base64," in page             # avatar only as <img> data
    assert "<circle cx" not in page                         # raw SVG never inlined


def test_cli_view_writes_page_and_leaves_cartridge_untouched(tmp_path, clock, capsys):
    path = str(tmp_path / "g.sqlite")
    _mind(path, clock).close()
    before = hashlib.sha256(open(path, "rb").read()).hexdigest()
    out = str(tmp_path / "g.html")
    assert main(["view", path, out]) == 0
    assert "Grace" in open(out, encoding="utf-8").read()
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == before
    assert main(["view", path, path]) == 2                   # never overwrite the cartridge
    assert main(["view", path, out, "--bogus"]) == 2
