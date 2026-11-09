"""One staff agent's turn (contract §7): run_staff_turn(sprint, agent_id, wake) -> TurnResult.

The wake, in order: runtime paths and tool defaults, the agent's Staff Home identity (whole), the briefcase README, the Lexicon, the Overseer's
directives, the TeachFrames' cards, SPRINT_OVERVIEW and the pointers resolved; then the Timer's wake text. The agent
works with the staff tools for its wake kind, and its final words are the turn's text.

Never: retry (the Timer does), judge the work, or change a task the agent did not change itself.
"""
from __future__ import annotations

import re
import json
from pathlib import Path
from typing import Callable, Optional

from . import staff as staff_home
from .briefcase import briefcase_files
from .model import ContextCapacityError, TurnLoopError, Deployment, SetupError, tool_loop
from .turn_journal import Journal, EvidenceError, resume_messages
from .sprint import Sprint, TurnResult, Wake
from .tools import StaffTools, staff_schemas
from .path_context import staff_context

ENDINGS = {
    "TASK": "Give your final words now, without more tool calls: what you did and the task's status.",
    "MESSAGE": "Give your answer to the Overseer now, without more tool calls.",
    "CLOSURE": "Give your final words now, without more tool calls.",
}
_SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+\S+|api[_-]?key=\S+)", re.IGNORECASE)


def redact(text: str) -> str:
    return _SECRET.sub("***", text or "")


def profiles_path(sprint: Sprint) -> Path:
    data = sprint.record()
    return Path(data.get("profiles_path") or (sprint.data_root / "runtime_profiles.json"))


def deployment_name(sprint: Sprint, agent_id: str) -> str:
    deployments = sprint.record().get("deployments") or {}
    name = deployments.get(agent_id) or deployments.get("staff") or deployments.get("*")
    if not name:
        raise SetupError(f"sprint.json names no deployment for {agent_id} (deployments.{agent_id}, .staff or .*)")
    return name


def build_client(sprint: Sprint, agent_id: str):
    return Deployment.load(profiles_path(sprint), deployment_name(sprint, agent_id)).client()


def _card(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        from aether.knowledge.teachframe import generate_card
        return generate_card(text)
    except Exception:
        return text if len(text) <= 1600 else text[:1600] + "\n[… read_file for the whole frame]"


def wake_messages(sprint: Sprint, agent_id: str, wake: Wake) -> list:
    files = briefcase_files(sprint, agent_id)
    rel = lambda p: p.resolve().relative_to(sprint.root.resolve()).as_posix()
    parts = [staff_context(StaffTools(sprint, agent_id, wake)),
             f"⟪WAKE_CONTEXT⟫ {agent_id} · sprint {sprint.id} · read_file and list_dir paths are relative to the "
             f"sprint folder.",
             f"=== YOUR IDENTITY · Staff Home · {files['identity'].resolve()} ===", files["identity"].read_text(encoding="utf-8"),
             f"=== STAFF DIRECTIVE · {rel(files['readme'])} ===", _maybe(files["readme"]),
             f"=== LEXICON · {rel(files['crib'])} ===", _maybe(files["crib"])]
    if files["directives"]:
        parts.append("=== DIRECTIVES FROM THE OVERSEER ===")
        for p in files["directives"]:
            parts += [f"--- {rel(p)} ---", p.read_text(encoding="utf-8")]
    if files["teachframes"]:
        parts.append("=== TEACHFRAME CARDS (read_file the path for the whole frame) ===")
        for p in files["teachframes"]:
            parts += [f"--- {rel(p)} ---", _card(p)]
    overview = sprint.root / "SPRINT_OVERVIEW.md"
    parts += ["=== SPRINT OVERVIEW · SPRINT_OVERVIEW.md ===",
              overview.read_text(encoding="utf-8") if overview.exists() else "(not written yet)"]
    task = f"outputs/T{wake.task}/" if wake.task else "outputs/T<n>/"
    briefcase = rel(files["readme"].parent) + "/"
    parts += ["=== POINTERS RESOLVED ===",
              f"⟪BRIEFCASE::{briefcase}⟫ ⟪STAFF_DIRECTIVE::{briefcase}README.txt⟫ "
              f"⟪LEXICON::{briefcase}Aether_Crib_Sheet.txt⟫ ⟪DIRECTIVES::{briefcase}directives/⟫ "
              f"⟪TEACHFRAMES::{briefcase}teachframes/⟫ ⟪SPRINT_OVERVIEW::SPRINT_OVERVIEW.md⟫ ⟪OUTPUTS::{task}⟫ "
              f"⟪WAKE::{wake.kind}⟫"]
    return [dict(role="system", content="\n".join(parts)), dict(role="user", content=wake.text)]


def _maybe(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else f"(missing: {path.name}; briefcase_create makes it)"


def run_staff_turn(sprint: Sprint, agent_id: str, wake: Wake, *, client=None,
                   stopped: Optional[Callable[[], bool]] = None) -> TurnResult:
    try:
        if staff_home.entry(sprint.data_root, agent_id)["state"] != f"on:{sprint.id}":
            raise SetupError(f"{agent_id} is not hired for this sprint")
        if not staff_home.home_file(sprint.data_root, agent_id).is_file():
            raise SetupError(f"{agent_id}'s identity is missing from Staff Home")
        if client is None:
            client = build_client(sprint, agent_id)
        messages = wake_messages(sprint, agent_id, wake)
        messages = resume_messages(sprint,agent_id,wake,messages)
    except Exception as exc:
        setup = (SetupError, KeyError, FileNotFoundError, staff_home.StaffError)
        kind = "SetupError" if isinstance(exc, setup) else type(exc).__name__
        return TurnResult(False, "", {}, redact(f"{kind}: {exc}"))
    delegation = None
    if wake.kind != "CLOSURE":
        from .ultralight import configured
        delegation = configured(profiles_path(sprint), stopped=stopped)
    tools = StaffTools(sprint, agent_id, wake, delegation)
    from .json_recovery import JsonRecovery, replay, task_key
    previous = replay([json.loads(p.read_text(encoding='utf-8')) for p in (sprint.root/'turns').glob('*.json')])
    saved = previous.get(task_key(agent_id, wake.kind, tools.recovery_task))
    arguments = JsonRecovery.from_report(saved) if saved else JsonRecovery()
    journal=Journal(sprint,agent_id)
    def checkpoint(cost,phase,operation=None):
        cost['recovery_task']=tools.recovery_task
        cost['written']=list(tools.written)
        journal(cost,phase,operation)
    try:
        text, cost, calls = tool_loop(client, messages, staff_schemas(wake.kind, delegation is not None), tools.call,
                                      stopped=lambda: tools.halted or bool(stopped and stopped()),
                                      delegation=delegation, ending=ENDINGS.get(wake.kind, ENDINGS["TASK"]),
                                      argument_recovery=arguments,checkpoint=checkpoint)
    except ContextCapacityError as exc:
        return TurnResult(False, "", exc.cost, f"ContextCapacityError: {exc}")
    except TurnLoopError as exc:
        if exc.uncertain or isinstance(exc.__cause__,EvidenceError):
            tools.halt('operation_uncertain','Operation evidence is incomplete; preserve it and do not replay the action.')
        return TurnResult(False,'',exc.cost,redact(str(exc)))
    except Exception as exc:
        return TurnResult(False, "", {}, redact(f"{type(exc).__name__}: {exc}"))
    cost["tools"] = calls
    cost["written"] = tools.written
    cost['recovery_task'] = tools.recovery_task
    if tools.wake.kind == 'CLOSURE' and tools.recovery.pending:
        tools.halt('closure_unresolved', 'The closure turn ended without accepted filing.')
        cost['stopped'] = True
    if arguments.pending or arguments.violations:
        problem=arguments.violations[-1] if arguments.violations else dict(kind='json_unresolved',detail='Turn ended with unresolved tool arguments.')
        tools.halt(problem['kind'],problem['detail'])
        cost['stopped']=True
    return TurnResult(True, text or "", cost)
