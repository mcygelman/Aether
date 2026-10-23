from memory_fabric import Event
from memory_fabric.host import memory_block, tags_from_text


def test_turns_do_nothing(engine):
    assert not memory_block(engine, Event.TURN, ["x"]).act


def test_planning_with_tags_returns_headers(engine):
    nid = engine.remember("Per-driver context allocation", "", "safety warning",
                          ["context allocation"], category="Alert")
    block = memory_block(engine, Event.PLANNING, ["context allocation"])
    assert block.act and block.note_ids == (nid,)
    assert "MEMORY CHECK (planning" in block.text and "Per-driver" in block.text
    assert engine.note(nid)["pull_count"] == 0          # the host never loads for the agent


def test_error_without_tags_derives_them(engine):
    nid = engine.remember("Port 8080 already in use", "", "safety warning", ["port", "8080"],
                          category="Alert")
    block = memory_block(engine, Event.TOOL_ERROR, error_text="Error: port 8080 already in use")
    assert "8080" in block.tags and nid in block.note_ids


def test_planning_without_tags_asks_the_agent(engine):
    block = memory_block(engine, Event.PLANNING)
    assert block.act and "choose the tags" in block.text


def test_board_full_asks_for_reformation(engine):
    block = memory_block(engine, Event.BOARD_NEARLY_FULL)
    assert block.action == "reform" and "Prime Context Stack stays on top" in block.text


def test_tags_from_text_skips_noise():
    assert tags_from_text("Error: the port 8080 failed to bind") == ("port", "8080", "bind")
