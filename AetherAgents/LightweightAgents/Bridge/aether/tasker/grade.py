"""Grade a finished sprint from its own records: python -m aether.tasker sprint grade PATH [--json].

Reads only what the run already left behind (sprint.json, turns/*.json, timer/timer.log, tasker.sqlite and the Staff
Home's backups) and counts what went wrong, each with a fixed severity, so that "clean" is a number and not an
opinion. Each finding is also a **host** fault (our code failed: it must not happen) or a **mind** deviation (an agent
took a wrong turn that the road answered). Michel, 10-30: design for the minds that will follow the road better and
accept 1 to 3% now. A sprint is **clean** when it closed as a pass, has no high or medium host fault, and its mind
deviations (high or medium) are at most 3% of its tool calls. Lexicon discipline is reported beside it as a number.

    high    the outcome is not closed-pass; a turn failed; a reply was cut off; a task went STALE; a change outside
            the allowed folders; a cost cap was reached; a setup failure reached the operator; a staff identity was
            not written back
    medium  a re-prompt; a tool refused a staff agent or the Overseer on a rule (not on its arguments); a tool error;
            the overview written more than once; a linked project without roadmap_update; a TASK turn that left
            its task's status unchanged; a program that timed out
    low     a call with the wrong arguments; a task note over 400 characters; an Overseer turn over 15 model calls;
            cost over $3

Never: judge the quality of the work. The Overseer and the reviewer do that; the grade counts process faults.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from . import staff as staff_home
from .sprint import load_sprint
from .recovery import replay, outcome

ERROR = re.compile(r"^[A-Z][A-Za-z]*(Error|Exception):")
ARGUMENTS = ("was called with the wrong arguments", "missing a required argument", "unexpected keyword argument",
             "not valid JSON")
SEVERITIES = ("high", "medium", "low")
HOST = ("turn_failed", "cut_reply", "tool_error", "write_back_missing", "operator_line", "cap_reached",
        "timer_error", "timer_died")
DEVIATION_BAR = 0.03
STREAM = re.compile(r"\[(DEF|WHY|HOW|RESULT|ASSERT|REMEMBER|SUMMARY|QUOTE|CURRENT_TASK|TRANS)\]")


def lexicon(text) -> bool:
    """One ∆WMC block with a WORLD_MODEL: the crib sheet's own conformance rule, the part a count can check."""
    t = text or ""
    return "∆WMC{" in t and "}∆WMC" in t and "WORLD_MODEL" in t


def _when(text):
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


def _finding(out, severity, kind, detail, turn=None):
    out.append(dict(severity=severity, kind=kind, detail=detail, turn=turn))


def grade(path) -> dict:
    sprint = load_sprint(path)
    root = sprint.root
    record = sprint.record()
    timer = json.loads((root / "timer" / "timer.json").read_text(encoding="utf-8")) \
        if (root / "timer" / "timer.json").exists() else {}
    log = (root / "timer" / "timer.log").read_text(encoding="utf-8").splitlines() \
        if (root / "timer" / "timer.log").exists() else []
    turns = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((root / "turns").glob("*.json"))]
    conn = sqlite3.connect(str(root / "tasker.sqlite"))
    try:
        tasks = conn.execute("SELECT number, title, assigned_to, status, note FROM tasks ORDER BY number").fetchall()
        changes = conn.execute("SELECT at, task, field, new, by FROM changes WHERE field = 'status'").fetchall()
    finally:
        conn.close()
    found: list = []

    # ---- outcome -------------------------------------------------------------------------------------------------
    state = record.get("state")
    if state != "closed-pass":
        _finding(found, "high", "outcome", f"the sprint ended as {state}")
    if not (state or "").startswith("closed-") and not any(" EXIT " in ln for ln in log):
        # the Timer stopped without saying why: no mind did that (suite r1, analysis)
        _finding(found, "high", "timer_died", "the sprint is not closed and the Timer left no EXIT line")
    if record.get("cap_reached"):
        _finding(found, "high", "cap_reached", f"cap {record['cap_reached']} reached")
    for line in record.get("operator_lines", []):
        text = line.get("text", "")
        if line.get("stop") or any(k in text for k in ("cannot run", "cap reached", "turn failed", "changed outside")):
            _finding(found, "high", "operator_line", text[:200])
        else:
            _finding(found, "low", "operator_note", text[:200])

    # ---- the Timer's log ----------------------------------------------------------------------------------------
    for line in log:
        event = line.split(" ", 2)[1] if line.count(" ") >= 2 else ""
        rest = line.split(" ", 2)[2] if line.count(" ") >= 2 else ""
        if event == "TIMER_ERROR":
            _finding(found, "high", "timer_error", rest[:200])
        elif event in ("TURN_FAILED", "STALE", "CHANGED_OUTSIDE"):
            _finding(found, "high", event.lower(), rest[:200])
        elif event == "REPROMPT":
            _finding(found, "medium", "reprompt", rest[:200])

    # ---- each turn --------------------------------------------------------------------------------------------------
    overview_writes, roadmap_updates, run_commands, tool_calls = 0, 0, 0, 0
    lex = {k: [0, 0] for k in ("overview", "task_prompts", "briefcase_directives", "messages", "staff_words", "overseer_words")}

    def mark(kind, text):
        lex[kind][1] += 1
        lex[kind][0] += lexicon(text)
    for agent in record.get('staff', []):
        for directive in sorted((sprint.briefcase(agent) / 'directives').glob('*.txt')):
            text = directive.read_text(encoding='utf-8')
            mark('briefcase_directives', text)
            if not lexicon(text):
                _finding(found, 'low', 'briefcase_not_lexicon', directive.relative_to(root).as_posix())
    for t in turns:
        n, mind, cost = t.get("turn"), t.get("mind"), t.get("cost") or {}
        who = "overseer" if mind == "overseer" else "staff"
        if not t.get("ok"):
            _finding(found, "high", "turn_failed", f"{mind} {t.get('kind')}: {str(t.get('error'))[:160]}", n)
        for call in cost.get("calls", []) or []:
            if call.get("finish_reason") == "length":
                _finding(found, "high", "cut_reply", f"{mind} {t.get('kind')}: a reply was cut at the output limit", n)
        if mind == "overseer" and (cost.get("model_calls") or 0) > 15:
            _finding(found, "low", "heavy_overseer_turn", f"{cost['model_calls']} model calls in a {t.get('kind')} turn", n)
        if t.get("text"):
            mark("overseer_words" if mind == "overseer" else "staff_words", t["text"])
        for c in cost.get("tools", []) or []:
            name, result = c.get("name", ""), str(c.get("result", ""))
            args = c.get("arguments") or {}
            tool_calls += 1
            if c.get('argument_outcome') and c.get('dispatched') is False:
                continue  # Typed envelope attempts are graded separately, without counting command execution.
            if name == "sprint_overview_write":
                mark("overview", args.get("text", ""))
            elif name == "task_add":
                mark("task_prompts", args.get("prompt", ""))
            elif name == "message_agent":
                mark("messages", args.get("text", ""))
            if name == "sprint_overview_write" and result.startswith("wrote"):
                overview_writes += 1
            if name == "roadmap_update" and not result.startswith(("refused", "PermissionError", "ValueError")):
                roadmap_updates += 1
            if name == "run_command":
                run_commands += 1
            from .closure_recovery import outcome as closure_outcome
            closure = closure_outcome(name, result, c.get('closure_outcome'))
            if t.get('kind') == 'CLOSURE' and closure and closure['status'] in ('format_error', 'succeeded'):
                continue  # Typed closure attempts are graded with their recovery history below.
            execution = outcome(name, result, c.get('command_outcome'))
            if execution and execution['status'] != 'unknown':
                if mind == 'overseer' and execution['status'] != 'succeeded':
                    _finding(found, 'low', 'command_attempt_failed', f"overseer: {execution}", n)
                continue  # Execution attempts are graded by the shared recovery history above.
            if any(a in result for a in ARGUMENTS):
                _finding(found, "low", "wrong_arguments", f"{mind} {name}: {result[:140]}", n)
            elif result.startswith("refused: [Errno"):          # an OS error answered as a refusal: ours
                _finding(found, "medium", "tool_error", f"{mind} {name}: {result[9:160]}", n)
            elif result.startswith("refused:"):
                _finding(found, "medium", f"{who}_refused", f"{mind} {name}: {result[9:160]}", n)
            elif result.startswith("PermissionError:"):         # a rule answered a reach outside scope: the mind's
                _finding(found, "medium", f"{who}_refused", f"{mind} {name}: {result[17:160]}", n)
            elif result.startswith(("FileNotFoundError:", "not found:")):
                _finding(found, "low", "missing_file", f"{mind} {name}: {result[:140]}", n)   # ordinary exploration
            elif ERROR.match(result):
                _finding(found, "medium", "tool_error", f"{mind} {name}: {result[:160]}", n)
        if mind != "overseer" and t.get("kind") == "TASK" and t.get("task") is not None:
            start, end = _when(t.get("started")), _when(t.get("ended"))
            moved = [c for c in changes if c[1] == t["task"] and c[4] == mind and start and end
                     and start - timedelta(seconds=1) <= (_when(c[0]) or start) <= end + timedelta(seconds=1)]
            if not moved:
                _finding(found, "medium", "idle_task_turn", f"{mind}'s TASK turn left T{t['task']} as it was", n)
    if overview_writes > 1:
        _finding(found, "medium", "overview_rewritten", f"SPRINT_OVERVIEW.md written {overview_writes} times")
    if record.get("project") and not roadmap_updates and state and state.startswith("closed-"):
        _finding(found, "medium", "roadmap_skipped", f"project {record['project']} is linked; no roadmap_update")

    recovery = replay(turns)
    for key, history in recovery.items():
        domain = 'closure' if history['policy'] == 'closure-format-recovery-v1' else 'command'
        for attempt in history['attempts']:
            if attempt['status'] != 'succeeded':
                _finding(found, 'low', domain + '_attempt_failed',
                         f"{key}: {attempt['status']} (exit {attempt['exit_code']}); retained in recovery history",
                         (attempt['ref'] or {}).get('turn'))
        for fixed in history['recovered']:
            _finding(found, 'low', domain + '_recovered', f"{key}: {fixed}")
        for problem in history['violations']:
            _finding(found, 'high', problem['kind'], f"{key}: {problem['detail']}",
                     (problem['ref'] or {}).get('turn'))
        if history['pending']:
            _finding(found, 'high', domain + '_unresolved', f"{key}: {history['pending']}")

    from .json_recovery import replay as replay_json
    json_recovery = replay_json(turns)
    for key, history in json_recovery.items():
        for attempt in history['attempts']:
            if not attempt['valid']:
                _finding(found, 'low', 'json_attempt_failed', f"{key}: malformed JSON; no tool executed; attempt {attempt['ref']}")
        for fixed in history['recovered']:
            _finding(found, 'low', 'json_recovered', f"{key}: {fixed}; tool result checked separately")
        for problem in history['violations']:
            _finding(found, 'high', problem['kind'], f"{key}: {problem['detail']}")
        if history['pending']:
            _finding(found, 'high', 'json_unresolved', f"{key}: {history['pending']}")

    # ---- tasks and closure ----------------------------------------------------------------------------------------
    for number, title, agent, status, note in tasks:
        if len(note or "") > 400:
            _finding(found, "low", "long_note", f"T{number} note is {len(note)} characters")
    if record.get("closure_done"):
        for agent in record.get("staff", []):
            kinds = [v["kind"] for v in staff_home.versions(sprint.data_root, agent) if v["sprint_id"] == sprint.id]
            if "sprint-copy" not in kinds:
                _finding(found, "high", "write_back_missing", f"{agent}'s identity was not written back")
    cost = float(timer.get("cost_used") or 0)
    if cost > 3:
        _finding(found, "low", "cost", f"about ${cost:.2f}")

    for f in found:
        f["source"] = "host" if f["kind"] in HOST else "mind"
    counts = {s: sum(1 for f in found if f["severity"] == s) for s in SEVERITIES}
    host_faults = [f for f in found if f["source"] == "host" and f["severity"] in ("high", "medium")]
    deviations = [f for f in found if f["source"] == "mind" and f["severity"] in ("high", "medium")
                  and f["kind"] != "outcome"]
    rate = len(deviations) / max(1, tool_calls)
    created, finished = _when(record.get("created")), _when((record.get("result") or {}).get("at"))
    teachframes = sum(len(list((sprint.briefcase(a) / "teachframes").glob("*")))
                      for a in record.get("staff", []) if (sprint.briefcase(a) / "teachframes").is_dir())
    return dict(
        sprint=sprint.id, path=str(root), state=state,
        clean=state == "closed-pass" and not host_faults and rate <= DEVIATION_BAR
              and not any(h["pending"] or h["violations"] for h in [*recovery.values(),*json_recovery.values()]),
        recovery=recovery,
        json_recovery=json_recovery,
        counts=counts, host_faults=len(host_faults), deviations=len(deviations), deviation_rate=round(rate, 4),
        lexicon={k: f"{a}/{b}" for k, (a, b) in lex.items()}, findings=found,
        facts=dict(turns=len(turns), minutes=round((finished - created).total_seconds() / 60, 1)
                   if created and finished else None, cost=round(cost, 2), tokens=timer.get("tokens_used"),
                   tasks=len(tasks), staff=record.get("staff", []), run_commands=run_commands, tool_calls=tool_calls,
                   teachframes_given=teachframes, overview_writes=overview_writes, roadmap_updates=roadmap_updates))


def render(g: dict) -> str:
    f = g["facts"]
    lines = [f"# Grade: {g['sprint']}", "",
             f"**{'CLEAN' if g['clean'] else 'NOT CLEAN'}** · state {g['state']} · host faults {g['host_faults']} · "
             f"mind deviations {g['deviations']} of {f['tool_calls']} tool calls ({g['deviation_rate']:.1%}; bar "
             f"{DEVIATION_BAR:.0%}) · high {g['counts']['high']} · medium {g['counts']['medium']} · low "
             f"{g['counts']['low']}", "",
             "Lexicon: " + " · ".join(f"{k.replace('_', ' ')} {v}" for k, v in g["lexicon"].items()), "",
             f"{f['tasks']} tasks · {f['turns']} turns · {f['minutes']} min · about ${f['cost']} · staff "
             f"{', '.join(f['staff']) or '-'} · run_command {f['run_commands']} · TeachFrames given "
             f"{f['teachframes_given']} · overview writes {f['overview_writes']}", ""]
    for sev in SEVERITIES:
        rows = [x for x in g["findings"] if x["severity"] == sev]
        if rows:
            lines += [f"## {sev.capitalize()}", ""]
            lines += [f"- [{x['source']}] {x['kind']}" + (f" (turn {x['turn']})" if x["turn"] else "") +
                      f": {x['detail']}" for x in rows]
            lines.append("")
    return "\n".join(lines)
