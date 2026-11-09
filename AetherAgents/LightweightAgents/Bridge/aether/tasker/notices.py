"""The wake texts the Aether Timer delivers, built from the Tasker's data and the Lexicon templates in directives/.

Staff:    TASK, MESSAGE, CLOSURE                                (directives/WAKE_*.txt)
Overseer: HIRED, CHANGES, QUIET, STALE, TURN_FAILED, REPLY, CLOSURE_DONE   (directives/NOTICES.txt explains them)

Every text is built from fields (status, assignee, dependencies, notes, counts) and quotes what minds wrote only as
data to pass on. Never: summarize, judge or reword a mind's words.
"""
from __future__ import annotations

from pathlib import Path
from string import Template
from typing import Iterable, Optional

from ._files import now
from .sprint import Sprint, Wake
from .tasker import Change, Task

DIRECTIVES = Path(__file__).resolve().parent / "directives"


_TEMPLATES: dict = {}


def _template(name: str) -> Template:
    """Read once per process, with the code that fills it: a template changed on disk under a running Timer once
    asked for a field the running code did not pass, and the Timer died (suite r1, analysis)."""
    if name not in _TEMPLATES:
        _TEMPLATES[name] = Template((DIRECTIVES / name).read_text(encoding="utf-8"))
    return _TEMPLATES[name]


for _name in ("WAKE_TASK.txt", "WAKE_MESSAGE.txt", "WAKE_CLOSURE.txt"):
    _template(_name)


def _indent(text: str, spaces: int = 4) -> str:
    return "\n".join((" " * spaces + ln) if ln.strip() else ln for ln in (text or "").strip("\n").split("\n"))


def _quote(text: str) -> str:
    return '"' + (text or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def task_line(t: Task) -> str:
    deps = ",".join(str(d) for d in t.depends_on) or "-"
    return (f"{{TASK: {t.number}, TITLE: {_quote(t.title)}, STATUS: {t.status}, ASSIGNED_TO: {t.assigned_to or '-'}, "
            f"DEPENDS_ON: [{deps}], NOTE: {_quote(t.note)}}}")


# ---- staff wakes -------------------------------------------------------------------------------------------------

def _lexicon(agent_id: str) -> str:
    return f"briefcases/{agent_id}/Aether_Crib_Sheet.txt"


def task_wake(sprint: Sprint, agent_id: str, number: int, *, reprompt: bool = False) -> Wake:
    """Michel's step 7a: the notice names the task and gives the road; the task itself, its prompt and its
    dependencies are read from the Tasker (tasker_read), the only place a task lives."""
    t = sprint.tasker("operator").get(number)
    again = ""
    if reprompt:
        again = (f"\n  [ASSERT] → ⌜STILL_ACTIVE⌝ := ⌞ T{number} is still Active after your last turn. "
                 f"Close it or block it with a note now. ⌟\n")
    start = (f"task_start({t.number})" if t.status == "New" else
             f"T{t.number} is already Active and yours: do not call task_start")
    text = _template("WAKE_TASK.txt").substitute(
        sprint=sprint.id, at=now(), agent=agent_id, number=t.number, title=t.title, status=t.status, start=start,
        lexicon=_lexicon(agent_id), outputs=f"outputs/T{t.number}/", reprompt=again)
    return Wake("TASK", text, task=t.number)


RESUMED = ("[REMEMBER] → ⌜RESUMED⌝ := ⌞ A stop ended your last turn on this notice at {at}, between two steps. "
           "Nothing you wrote was lost: the Tasker and the files show where things stand. Read them and continue "
           "from there. ⌟")


def resumed(wake: Wake) -> Wake:
    """The same notice again after a stop, said at its top: the mind picks up where it was."""
    if "⌜RESUMED⌝" in wake.text:
        return wake
    head, sep, rest = wake.text.partition("\n")
    return Wake(wake.kind, head + sep + RESUMED.format(at=now()) + "\n" + rest, wake.task)


def _own_tasks(sprint: Sprint, agent_id: str) -> str:
    mine = sprint.tasker("operator").list(assigned_to=agent_id)
    return "\n".join("    " + task_line(t) for t in mine) or "    (no tasks)"


def message_wake(sprint: Sprint, agent_id: str, text: str) -> Wake:
    body = _template("WAKE_MESSAGE.txt").substitute(
        sprint=sprint.id, at=now(), agent=agent_id, overseer=sprint.overseer, text=_indent(text),
        tasks=_own_tasks(sprint, agent_id), lexicon=_lexicon(agent_id))
    return Wake("MESSAGE", body)


def closure_wake(sprint: Sprint, agent_id: str, name: Optional[str] = None) -> Wake:
    body = _template("WAKE_CLOSURE.txt").substitute(
        sprint=sprint.id, at=now(), agent=agent_id, name=name or agent_id.capitalize(),
        tasks=_own_tasks(sprint, agent_id), lexicon=_lexicon(agent_id))
    return Wake("CLOSURE", body)


# ---- Overseer notices ----------------------------------------------------------------------------------------------

# The road each Overseer notice ends with, fixed per kind (it never depends on what a mind wrote). Michel, 10-30:
# lean into directives; a sign at the moment of action, not only at the top of a long Directive.
ROADS = {
    "HIRED": "staff_list ⊕ staff_hire ⊕ sprint_overview_write, once, in the Lexicon ⊕ "
             "briefcase_create for each agent: agent_id alone builds the defaults; supplements are optional. "
             "Before adding TeachFrames, call teachframe_list and use its names. "
             "⊕ task_add, each prompt one ∆WMC block with a WORLD_MODEL ⊕ timer_start",
    "CHANGES": "Read the notes. Act only where a task needs your decision: a FAIL review → task_add rework that "
               "depends on it; a Blocked task → task_unblock (or task_edit). Otherwise end your turn.",
    "QUIET": "Clear each Blocked task (task_unblock), add what is missing (task_add), or, when the goals are met, "
             "closure_send.",
    "STALE": "message_agent the assignee to close or block it. The Timer changed nothing; the task stays Active "
             "until its agent moves it.",
    "TURN_FAILED": "The task is not dispatched again until you change it: task_edit (a clearer prompt, another "
                   "assignee) or task_drop.",
    "REPLY": "The answer is information. Work it asks for becomes task_add, never another message.",
    "CLOSURE_DONE": "roadmap_update ⊕ sprint_finish ⊕ your own closure.",
}
LEXICON_ROAD = "⟪LEXICON::Aether_Crib_Sheet.txt⟫ Write in the Lexicon: one ∆WMC block with a WORLD_MODEL."


def _notice(sprint: Sprint, kind: str, blocks: Iterable[str]) -> str:
    head = f"⟪TIMER_NOTICE⟫ ⟪SPRINT::{sprint.id}⟫ ⟪AT::{now()}⟫ ⟪KIND::{kind}⟫ ⟪TO::overseer:{sprint.overseer}⟫"
    road = f"[HOW] → ⌜NEXT⌝ := ⌞ {ROADS[kind]} ⌟\n{LEXICON_ROAD}" if kind in ROADS else LEXICON_ROAD
    return "\n".join([head, *[b for b in blocks if b], road, "⟪END_TIMER_NOTICE⟫"])


def counts_line(counts: dict) -> str:
    return "[TASKER]  := {" + ", ".join(f"{k}: {v}" for k, v in counts.items()) + "}"


def change_line(c: Change, note: Optional[str] = None) -> str:
    parts = [f"TASK: {c.task}", f"FIELD: {c.field}", f"FROM: {c.old if c.old is not None else '-'}",
             f"TO: {c.new if c.new is not None else '-'}", f"BY: {c.by}"]
    if note is not None:
        parts.append(f"NOTE: {_quote(note)}")
    return "{" + ", ".join(parts) + "}"


def changes_notice(sprint: Sprint, changes: list, counts: dict) -> Wake:
    """One CHANGES notice for every change since the last one. A status change carries the task's note as it is now;
    a note change on its own is folded into its status change when both came together."""
    tk = sprint.tasker("operator")
    status_tasks = {c.task for c in changes if c.field == "status"}
    lines = []
    for c in changes:
        if c.field == "note" and c.task in status_tasks:
            continue
        note = tk.get(c.task).note if c.field == "status" else None
        lines.append("  " + change_line(c, note))
    body = "[CHANGES] := [\n" + ",\n".join(lines) + "\n]"
    return Wake("CHANGES", _notice(sprint, "CHANGES", [body, counts_line(counts)]))


def quiet_notice(sprint: Sprint, counts: dict, blocked: list, waiting: list) -> Wake:
    b = "[BLOCKED] := [\n" + ",\n".join("  " + task_line(t) for t in blocked) + "\n]" if blocked else "[BLOCKED] := []"
    w = ("[WAITING] := [\n" + ",\n".join("  " + task_line(t) + f"  // {why}" for t, why in waiting) + "\n]"
         if waiting else "[WAITING] := []")
    return Wake("QUIET", _notice(sprint, "QUIET", [
        "[ASSERT] → ⌜QUIET⌝ := ⌞ Nothing is Active and nothing can be dispatched. ⌟", b, w, counts_line(counts)]))


def stale_notice(sprint: Sprint, t: Task) -> Wake:
    return Wake("STALE", _notice(sprint, "STALE", [
        f"[ASSERT] → ⌜STALE⌝ := ⌞ T{t.number} stayed Active after its turn and one re-prompt; the Timer changed "
        f"nothing. ⌟", "[TASK] := " + task_line(t)]), task=t.number)


def turn_failed_notice(sprint: Sprint, agent_id: str, wake_kind: str, error: str, task: Optional[int] = None) -> Wake:
    return Wake("TURN_FAILED", _notice(sprint, "TURN_FAILED", [
        f"[RESULT] → ⌜TURN_FAILED⌝ := ⌞ {agent_id}'s {wake_kind} turn failed three times "
        f"(two retries). ⌟", f"[ERROR] := {_quote(error)}", f"[TASK] := {task if task is not None else '-'}"]),
        task=task)


QUESTION_SHOWN = 600


def reply_notice(sprint: Sprint, agent_id: str, question: str, answer: str) -> Wake:
    # the start of your own question, not all of it: you wrote it, and it is in your journal (it was 7.5K in the rerun)
    asked = question if len(question) <= QUESTION_SHOWN else (
        question[:QUESTION_SHOWN] + f"\n[… {len(question) - QUESTION_SHOWN} more characters of your own message]")
    return Wake("REPLY", _notice(sprint, "REPLY", [
        f"[QUOTE] → ⌜YOU_ASKED::{agent_id}⌝ := ⌞\n{_indent(asked)}\n⌟",
        f"[QUOTE] → ⌜REPLY::{agent_id}⌝ := ⌞\n{_indent(answer)}\n⌟"]))


def closure_done_notice(sprint: Sprint, lines: list) -> Wake:
    body = "[CLOSURE] := [\n" + ",\n".join("  " + _quote(ln) for ln in lines) + "\n]"
    return Wake("CLOSURE_DONE", _notice(sprint, "CLOSURE_DONE", [
        "[ASSERT] → ⌜STAFF_CLOSED⌝ := ⌞ Every hired staff agent had its closure turn; the write-back is done. ⌟",
        body]))


def hired_notice(sprint: Sprint, goals: str) -> Wake:
    return Wake("HIRED", _notice(sprint, "HIRED", [
        f"[ASSERT] → ⌜HIRED_AS_OVERSEER⌝ := ⌞ You are the Overseer of sprint {sprint.id}. ⌟",
        f"[QUOTE] → ⌜SPRINT_GOALS⌝ := ⌞\n{_indent(goals)}\n⌟"]))


def changed_outside_line(paths: list, who: str) -> str:
    shown = ", ".join(paths[:8]) + (f" (+{len(paths) - 8})" if len(paths) > 8 else "")
    return (f"[ASSERT] → ⌜CHANGED_OUTSIDE⌝ := ⌞ changed where no running mind may write: {shown}; running then: "
            f"{who}. A record of size and time, not of who; ask the agent if it matters. ⌟")


def cap_line(cap: str, used, limit) -> str:
    return f"[ASSERT] → ⌜CAP_REACHED::{cap}⌝ := ⌞ used {used} of {limit}; dispatch is off until the operator raises it ⌟"
