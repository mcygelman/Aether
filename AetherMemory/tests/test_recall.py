import pytest


def _door(engine):
    project = engine.remember("UI Brand Revamp", "one-week project", "project fact",
                              ["upstairs"], switch_in_days=7)
    code = engine.remember("Door code", "4471", "project fact", ["door code", "upstairs"],
                           switch_in_days=7, links=[(project, "context", 0.9)])
    warn = engine.remember("Alarm warning", "two wrong codes trigger the alarm", "safety warning",
                           ["door code", "alarm"], category="Alert", switch_in_days=7,
                           links=[(code, "context", 0.9)])
    return project, code, warn


def test_tags_in_headers_out_and_links_followed(engine):
    project, code, warn = _door(engine)
    result = engine.recall(["alarm"])
    assert result.ids()[0] == warn                     # alerts first
    assert set(result.ids()) == {project, code, warn}  # structure surfaces via links
    header = result.headers[0]
    assert header.via == "tag:alarm" and header.hops == 0
    assert result.headers[1].via.startswith("link:")
    assert "Alarm warning" in result.render()


def test_recall_does_not_refresh_but_load_does(engine, clock):
    _, _, warn = _door(engine)
    engine.recall(["alarm"])
    assert engine.note(warn)["pull_count"] == 0
    engine.load([warn])
    assert engine.note(warn)["pull_count"] == 1


def test_stale_baggage_drops_after_switch(engine, clock):
    project, code, warn = _door(engine)
    clock.advance(10)
    assert engine.recall(["alarm"]).ids() == [warn]
    assert engine.recall(["upstairs"]).ids() == []


def test_budget_limits_headers_and_reports_more(engine):
    for i in range(20):
        engine.remember(f"lesson number {i}", "some body text " * 5, "standing lesson", ["gpu"])
    full = engine.recall(["gpu"])
    small = engine.recall(["gpu"], budget_tokens=full.headers[0].tokens() * 3)
    assert len(small.headers) == 3 and small.more_available == 17
    assert "more beyond the budget" in small.render()
    assert len(engine.recall(["gpu"], max_headers=5).headers) == 5


def test_filters_categories_and_proprietary(engine):
    a = engine.remember("alert", "", "safety warning", ["x"], category="Alert")
    s = engine.remember("secret", "", "project fact", ["x"], proprietary=True)
    assert engine.recall(["x"], categories=["Alert"]).ids() == [a]
    assert s not in engine.recall(["x"], include_proprietary=False).ids()
    flags = {h.note_id: h.proprietary for h in engine.recall(["x"]).headers}
    assert flags[s] is True and flags[a] is False


def test_fog_reaches_only_very_important_buried_notes(engine, clock):
    vital = engine.remember("vital", "", "project fact", ["old"], importance=0.95, switch_in_days=7)
    plain = engine.remember("plain", "", "project fact", ["old"], switch_in_days=7)
    clock.advance(60)
    new = engine.remember("new work", "", "project fact", ["new"],
                          links=[(vital, "causal", 0.9), (plain, "context", 0.9)])
    ids = {h.note_id: h for h in engine.recall(["new"]).headers}
    assert vital in ids and ids[vital].through_fog
    assert plain not in ids
    direct = engine.recall(["old"]).ids()          # a direct tag hit also sees through the fog
    assert vital in direct and plain not in direct
    assert new


def test_recurring_flag_after_repeated_pulls(engine, clock):
    a = engine.remember("stale allocation", "", "safety warning", ["alloc"], category="Alert")
    for _ in range(3):
        engine.load([a])
        clock.advance(1)
    header = engine.recall(["alloc"]).headers[0]
    assert header.recurring and "RECURRING" in header.render()


def test_hops_are_limited(engine):
    ids = [engine.remember(f"n{i}", "", "standing lesson", [f"t{i}"]) for i in range(6)]
    for a, b in zip(ids, ids[1:]):
        engine.link(a, b, "thread", 1.0)
    engine.change_setting("max_hops", 2)
    assert engine.recall(["t0"]).ids() == ids[:3]


def test_load_reinforces_links_between_notes_used_together(engine):
    a = engine.remember("a", "", "standing lesson")
    b = engine.remember("b", "", "standing lesson", links=[(a, "conceptual", 0.5)])
    engine.load([a, b])
    row = engine.cart.conn.execute("SELECT strength, reinforce_count FROM links").fetchone()
    assert row["strength"] == pytest.approx(0.55) and row["reinforce_count"] == 1


def test_empty_tags_return_nothing(engine):
    engine.remember("a", "", "lesson")
    assert engine.recall([]).headers == []


def test_phrase_match_finds_squashed_and_hyphenated_tags(engine):
    a = engine.remember("LMStudio loaded state mismatch", "b", "project fact", ["lmstudio"])
    b = engine.remember("Unload on idle", "b", "safety warning", ["lm-studio idle"], category="Alert")
    c = engine.remember("Studio lighting", "b", "project fact", ["studio"])
    result = engine.recall(["lm studio"])
    assert set(result.ids()) == {a, b}
    assert all(h.via.startswith("phrase:") for h in result.headers)
    assert c not in result.ids()


def test_exact_tag_outranks_phrase_match(engine):
    exact = engine.remember("Exact", "b", "project fact", ["gpu offload"])
    phrase = engine.remember("Phrase", "b", "project fact", ["gpuoffload layers"])
    headers = {h.note_id: h for h in engine.recall(["gpu offload"]).headers}
    assert headers[exact].reach > headers[phrase].reach
    assert headers[exact].via == "tag:gpu offload"


def test_short_queries_match_exact_tags_only(engine):
    engine.remember("GPU", "b", "project fact", ["gpu"])
    engine.remember("GPU layers", "b", "project fact", ["gpu layers"])
    assert len(engine.recall(["gpu"]).ids()) == 1


def test_fan_effect_keeps_hubs_from_flooding_recall(engine):
    hub = engine.remember("Continuity thread", "everything", "project fact", ["continuity"])
    for i in range(60):
        engine.remember(f"Record {i}", "b", "project fact", [f"rec{i}"], links=[(hub, "context")])
    small = engine.remember("Small topic", "b", "project fact", ["small topic"])
    friend = engine.remember("Friend", "b", "project fact", ["friend"], links=[(small, "context")])
    hub_recall = engine.recall(["continuity"])
    assert hub_recall.ids() == [hub]                     # the hub, not its 60 neighbours
    assert friend in engine.recall(["small topic"]).ids()   # ordinary links still spread
    from memory_fabric.recall import fan_factor
    assert fan_factor(8) == 1.0 and fan_factor(32) == 0.5


def test_multi_word_tag_falls_back_to_its_words_only_when_it_finds_nothing(engine):
    core = engine.remember("C2 Governor Core", "b", "project fact", ["governor"])
    assert engine.recall(["context governor"]).headers[0].via == "word:context governor"
    assert engine.recall(["context governor"]).ids() == [core]
    exact = engine.remember("Context governor design", "b", "project fact", ["context governor"])
    assert engine.recall(["context governor"]).ids() == [exact]     # found: no fallback


def test_one_line_skips_markdown_metadata():
    from memory_fabric.recall import _one_line
    body = ("# INS_20260805_Configured_Is_Not_Capability\n\n**Date:** 2026-08-05\n"
            "**Cross-experience pattern drawn from:** EXP_20260805\n\n## The pattern\n\n"
            "Aether's constants and the model's capabilities are different classes of fact.\n"
            "A configured default is a decision.\n\n## The rule\n\nVerify ceilings.")
    assert _one_line(body) == ("Aether's constants and the model's capabilities are different "
                               "classes of fact. A configured default is a decision.")
    assert _one_line("plain body") == "plain body"
    assert _one_line("# only a heading") == "# only a heading"
    assert _one_line("- first point\n- second") == "first point second"
