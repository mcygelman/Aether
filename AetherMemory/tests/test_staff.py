import pytest

from memory_fabric import staff
from memory_fabric.staff import StaffError

SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4"/></svg>'


def test_reviews_are_written_by_others_and_read_only(engine):
    with pytest.raises(StaffError):
        engine.add_review("grace", "self", "I was great")
    engine.add_review("kirito", "overseer", "Reliable on GUI work.", 4.5, "Demo")
    engine.add_review("michel", "operator", "Clear returns.", 5)
    assert [r["reviewer"] for r in engine.reviews()] == ["michel", "kirito"]
    with pytest.raises(StaffError):
        engine.add_review("kirito", "overseer", "x", rating=9)


def test_levels_come_from_verified_outcomes_only(engine):
    with pytest.raises(StaffError):
        engine.record_outcome("gui", "T-1", "success", 3, verified_by="grace")
    assert engine.record_outcome("gui", "T-1", "success", 3, verified_by="kirito") == 2
    assert engine.record_outcome("gui", "T-2", "failure", 5, verified_by="kirito") == 2
    assert engine.record_outcome("gui", "T-3", "partial", 4, verified_by="kirito") == 2
    assert engine.record_outcome("gui", "T-4", "success", 5, verified_by="kirito") == 4   # 3+0+2+5 = 10 points


def test_levels_never_go_down_even_if_the_scale_changes(engine, monkeypatch):
    engine.record_outcome("backend", "T-1", "success", 5, verified_by="kirito")
    level = engine.card()["specialties"][0]["level"]
    monkeypatch.setattr(staff, "LEVEL_POINTS", tuple(p * 10 for p in staff.LEVEL_POINTS))
    engine.record_outcome("backend", "T-2", "failure", 1, verified_by="kirito")
    assert engine.card()["specialties"][0]["level"] == level


def test_agent_chooses_an_original_avatar(engine):
    with pytest.raises(StaffError):
        staff.set_avatar(engine.cart, SVG, "a circle", chosen_by="michel", original_design_attested=True)
    with pytest.raises(StaffError):
        engine.set_avatar(SVG, "a circle", original_design_attested=False)
    for bad in ("<png>", SVG.replace("<circle", "<script>alert(1)</script><circle"),
                SVG.replace('<circle', '<image href="https://x.y/a.png"/><circle'),
                SVG.replace('<circle', '<circle onload="x()"'),
                SVG.replace('<circle', '<foreignObject><div>hi</div></foreignObject><circle'),
                SVG.replace('<circle', '<image href="data:text/html,x"/><circle'),
                SVG.replace('<circle', '<rect style="fill:url(https://x.y/p)"/><circle'),
                SVG.replace('<circle', '<style>@import "https://x.y/s.css";</style><circle'),
                SVG.replace('<circle', '<use href="//x.y/a.svg#i"/><circle')):
        with pytest.raises(StaffError):
            engine.set_avatar(bad, "x", original_design_attested=True)
    engine.set_avatar(SVG, "A calm teal circle: steady, round, unhurried.", True)
    assert engine.card()["avatar_svg"] == SVG


def test_card_and_resume(engine):
    engine.set_scan_line("Grace: GUI and brand staff agent; not for backend work.")
    engine.record_outcome("gui", "T-1", "success", 4, verified_by="kirito")
    engine.add_review("kirito", "overseer", "Strong eye for layout.", 5)
    engine.remember("Keep contrast above 4.5:1", "accessibility habit", "standing lesson",
                    ["contrast"], stratum="Insight")
    engine.remember("Client palette codes", "secret hex values", "standing lesson",
                    ["palette"], stratum="Insight", proprietary=True)
    engine.remember("Fonts must be licensed", "", "safety warning", category="Alert")
    card = engine.card()
    assert card["specialties"][0]["specialty"] == "gui" and card["stats"]["verified_tasks"] == 1
    assert card["stats"]["average_rating"] == 5 and card["memory"]["active_alerts"] == 1
    resume = engine.resume()
    assert "Keep contrast above 4.5:1" in resume
    assert "Client palette codes" not in resume          # never proprietary
    assert "Fonts must be licensed" in resume and "Strong eye for layout." in resume
