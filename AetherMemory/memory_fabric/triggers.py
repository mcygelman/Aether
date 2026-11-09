"""Triggers box: which moments call for a memory check (decision 12).

Inputs:  an event the host observed (or the agent declared).
Outputs: a TriggerAction: what to do (memory check, reform, nothing), who
         triggers it (host or agent), and why.

Priority order:
  1. overseer planning              (agent-declared: only the agent knows)
  2. an agent starting its own task (host-detected)
  3. after an error or surprise     (host-detected for tool errors)
  4. before irreversible/costly actions (host-detected from the tool called)
  plus: the board nearly full -> reformation (host-detected, decision 25).
No every-turn checks.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Event(str, Enum):
    PLANNING = "planning"
    TASK_START = "task_start"
    TOOL_ERROR = "tool_error"
    SURPRISE = "surprise"
    IRREVERSIBLE_ACTION = "irreversible_action"
    BOARD_NEARLY_FULL = "board_nearly_full"
    TURN = "turn"


@dataclass(frozen=True)
class TriggerAction:
    action: str      # "memory_check" | "reform" | "none"
    priority: int    # 1 = highest; 0 when no action
    triggered_by: str  # "agent" | "host" | "-"
    reason: str
    tag_hint: str    # where the agent should draw its tags from


_POLICY = {
    Event.PLANNING: TriggerAction(
        "memory_check", 1, "agent",
        "Planning decisions multiply: a wrong assumption is copied into every subtask.",
        "the parts of the plan: backends, tools, projects, people, risks"),
    Event.TASK_START: TriggerAction(
        "memory_check", 2, "host",
        "Memory is distributed: only this agent can check its own experience.",
        "the task's subject, tools and deliverables"),
    Event.TOOL_ERROR: TriggerAction(
        "memory_check", 3, "host",
        "Have I seen this before?",
        "the tool name and the key words of the error"),
    Event.SURPRISE: TriggerAction(
        "memory_check", 3, "agent",
        "Something unexpected happened; prior experience may explain it.",
        "what was expected and what happened instead"),
    Event.IRREVERSIBLE_ACTION: TriggerAction(
        "memory_check", 4, "host",
        "Rare but high stakes: check for warnings before acting.",
        "the action, its target and its cost"),
    Event.BOARD_NEARLY_FULL: TriggerAction(
        "reform", 1, "host",
        "The working board is nearly full: set things down into memory, keep what matters.",
        "-"),
    Event.TURN: TriggerAction("none", 0, "-", "No every-turn checks (too costly).", "-"),
}

# Tools whose effects are irreversible or cost money. Hosts may pass their own.
DEFAULT_IRREVERSIBLE_TOOLS = frozenset({
    "write_file", "edit_file", "delete_file", "move_file", "send_message", "send_email",
    "commit", "push", "deploy", "paid_call", "purchase",
})


def decide(event: Event) -> TriggerAction:
    return _POLICY[Event(event)]


def classify_tool_call(tool_name: str, failed: bool = False,
                       irreversible_tools=DEFAULT_IRREVERSIBLE_TOOLS) -> TriggerAction:
    """Host helper: what a tool call means for memory."""
    if failed:
        return decide(Event.TOOL_ERROR)
    if tool_name in irreversible_tools:
        return decide(Event.IRREVERSIBLE_ACTION)
    return decide(Event.TURN)


def pcs_directive() -> str:
    """The Prime Context Stack directive that makes planning a memory moment.

    Like the Lexicon once did, it gives the agent a functional reason to come
    back to the stack: before planning, it must choose tags, and choosing
    tags is itself the thinking.
    """
    return (
        "⟪RULE::MEMORY_CHECK⟫ Before you plan, delegate, or take an action you "
        "cannot undo: choose the tags this work touches and recall them from your "
        "memory. Read the headers; load only what helps. After an error, recall "
        "the error's key words before retrying. If a recalled alert is marked "
        "RECURRING, consider fixing its cause for good."
    )
