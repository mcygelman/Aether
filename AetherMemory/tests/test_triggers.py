from memory_fabric import Event, MemoryEngine
from memory_fabric.triggers import classify_tool_call, decide


def test_agreed_priorities():
    assert decide(Event.PLANNING).priority == 1 and decide(Event.PLANNING).triggered_by == "agent"
    assert decide(Event.TASK_START).priority == 2 and decide(Event.TASK_START).triggered_by == "host"
    assert decide(Event.TOOL_ERROR).priority == 3
    assert decide(Event.IRREVERSIBLE_ACTION).priority == 4
    assert decide(Event.BOARD_NEARLY_FULL).action == "reform"
    assert decide(Event.TURN).action == "none"        # no every-turn checks


def test_host_classifies_tool_calls():
    assert classify_tool_call("read_file").action == "none"
    assert classify_tool_call("write_file").action == "memory_check"
    assert classify_tool_call("read_file", failed=True).reason.startswith("Have I seen")
    assert classify_tool_call("custom", irreversible_tools={"custom"}).priority == 4


def test_pcs_directive_names_the_habit():
    text = MemoryEngine.pcs_directive()
    assert "RULE::MEMORY_CHECK" in text and "tags" in text and "RECURRING" in text
