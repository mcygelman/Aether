"""The tools of the Tasker Process (contract §6): schemas a model sees, and their handlers.

    STAFF_TOOLS              a staff turn's tools (narrowed per wake by staff_schemas)
    OVERSEER_TASKER_TOOLS    the Overseer's Tasker tools; Praxis's Overseer turn adds the heavyweight tools
    OVERSEER_FILE_TOOLS      read_file / list_dir / write_file for a solo sprint (writes under outputs/ only)
    StaffTools(sprint, agent_id, wake).call(name, args) -> str
    OverseerTools(sprint).call(name, args) -> str

A refused setup or file call answers with the reason and what to do instead. Command and closure recovery
rules are enforced here; a terminal refusal or exhausted recovery stops dispatch without authoring a correction.
Never: change a task on a mind's behalf, write outside the roots below, or let a child process see a credential.
"""
from __future__ import annotations

import inspect
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from . import staff as staff_home
from .briefcase import BriefcaseError, create_briefcases
from .grants import GRANT, effective_roots
from .model import MAX_TOOL_RESULT, test_command, test_environment
from .notices import task_line
from .recovery import CommandResult, Recovery, COMMANDS, trackers
from .closure_recovery import ClosureResult, ClosureRecovery
from .sprint import Sprint, SprintError, Wake, operator_line, post_request
from .staff import StaffError
from .tasker import STATUSES, TaskerError, UNSET
from ._files import write_text, write_json
from .command_runner import run as run_program

STOPS = ("money", "consent", "secrets", "harm")
OVERVIEW_HEADER = ("⟪SPRINT_OVERVIEW⟫ ⟪EDITABLE_BY::OVERSEER_ONLY⟫\n"
                   "[ASSERT] → ⌜ONLY_EDITABLE_BY_THE_OVERSEER⌝ := ⌞ ONLY EDITABLE BY THE OVERSEER. Staff read this "
                   "file and never change it: not with a tool, not with a program they run. ⌟\n")
RUN_TIMEOUT = 120
WRITE_GRANTS = False      # Michel, 10-30 night (S2): off until a real sprint needs one; ⟪GRANT::WRITE::⟫ reads as a read grant
PYTHONS = ("python", "python3", "py", "python.exe")
REFUSALS = (TaskerError, StaffError, BriefcaseError, SprintError, PermissionError, FileNotFoundError, ValueError)


def _f(name, desc, props, req):
    return dict(type="function", function=dict(name=name, description=desc,
                parameters=dict(type="object", properties=props, required=req)))


_S, _I = dict(type="string"), dict(type="integer")
_LIST_I = dict(type="array", items=_I)

STAFF_TOOLS = [
    _f("tasker_read", "Your current task in full, and the title, status and note of each task it depends on.", {}, []),
    _f("task_start", "New → Active. Only the task the Aether Timer gave you in this wake. Call it first.",
       dict(number=_I), ["number"]),
    _f("task_close", "Active → Closed with a one-line result note; the detail goes in outputs/T<n>/.",
       dict(number=_I, note=_S), ["number", "note"]),
    _f("task_block", "Active → Blocked with a note saying exactly what you need to go on.",
       dict(number=_I, note=_S), ["number", "note"]),
    _f("read_file", "Read a file: your own Staff Home identity, your briefcase, SPRINT_GOALS.md, SPRINT_OVERVIEW.md, outputs/ of your tasks and of the "
       "tasks yours depends on, or a granted project root. Paths are relative to the sprint folder. A large file "
       "comes in parts: offset (characters) and limit choose the part.",
       dict(path=_S, offset=_I, limit=_I), ["path"]),
    _f("list_dir", "List the files under a folder you may read ('.' lists what you may read).", dict(path=_S), []),
    _f("write_file", "Write a file under outputs/T<n>/ of your Active task (a bare name such as COLORS.md lands there), "
       "or under a folder the Overseer granted you for writing (⟪GRANT::WRITE::<path>⟫).",
       dict(path=_S, text=_S), ["path", "text"]),
    _f("run_tests", "Run the tests under your outputs folder or a granted project root (pytest, or unittest).",
       dict(target=_S, retry=dict(type="boolean", description="True only when rerunning this task's failed test operation after correction; at most two corrective attempts.")), ["target"]),
    _f("run_command", "Run one program and get its exit code, stdout and stderr. argv is a list, never shell text "
       "(for example [\"python\", \"tempconv.py\", \"100\", \"C\", \"F\"]). It runs in cwd: your outputs folder by "
       "default, or the outputs of a task yours depends on, or a granted folder. No credentials reach it; 120 s limit.",
       dict(argv=dict(type="array", items=_S), cwd=_S, retry=dict(type="boolean", description="True only when rerunning this task's failed command after correction; at most two corrective attempts.")), ["argv"]),
    _f("identity_close", "Closure wake only: file your new System 1 (one complete ∆WMC block) and one reflection entry "
       "into your identity. s1 is only the inner WMC block, without SYSTEM markers; s2_entry retains its reflection markers. "
       "It files exactly your text. For a format_error, correct your text and retry with retry=true, at most twice. "
       "Stop for other refusals or exhausted recovery.", dict(s1=_S, s2_entry=_S,
       retry=dict(type="boolean", description="True only when correcting this agent's pending closure-format error.")), ["s1", "s2_entry"]),
]

OVERSEER_TASKER_TOOLS = [
    _f("tasker_read", "All tasks with dependencies and notes, or filtered by status or assignee, and the counts.",
       dict(filter=dict(type="object", properties=dict(status=dict(type="string", enum=list(STATUSES)),
                                                       assigned_to=_S))), []),
    _f("task_add", "Add a task (status New). The prompt is written in the Aether Lexicon. Returns its number.",
       dict(title=_S, prompt=_S, assigned_to=_S, depends_on=_LIST_I), ["title", "prompt"]),
    _f("task_edit", "Edit a New or Blocked task: title, prompt, assignee or dependencies.",
       dict(number=_I, title=_S, prompt=_S, assigned_to=_S, depends_on=_LIST_I), ["number"]),
    _f("task_unblock", "Blocked → New, with a note for the agent and optionally a new prompt or assignee.",
       dict(number=_I, note=_S, prompt=_S, assigned_to=_S), ["number", "note"]),
    _f("task_drop", "New or Blocked → Closed with 'dropped: <why>'. Tasks that depend on it treat it as Closed.",
       dict(number=_I, note=_S), ["number", "note"]),
    _f("staff_list", "The Staff Home roster: id, name, role, resume line, model recommendation, availability.", {}, []),
    _f("teachframe_list", "The TeachFrames you can put in a briefcase: name, state, title and what each prevents. "
       "Use these names in briefcase_create.", {}, []),
    _f("staff_hire", "Hire staff for this sprint; identities stay in Staff Home, outside the sprint and briefcases.",
       dict(agent_ids=dict(type="array", items=_S)), ["agent_ids"]),
    _f("sprint_overview_write", "Write SPRINT_OVERVIEW.md once, when staff are chosen: what the sprint is about and "
       "what it is trying to accomplish. It is a directive, so write it in the Aether Lexicon: one ∆WMC block with a "
       "WORLD_MODEL. Staff read it first.", dict(text=_S), ["text"]),
    _f("briefcase_create", "Build one hired agent's default briefcase. Only agent_id is required: the tool supplies "
       "the standard README, Lexicon and manifest. Add optional teachframes (names from teachframe_list) and "
       "directives (each one Lexicon ∆WMC block with a WORLD_MODEL) only when needed. Repeating an identical "
       "directive does not add another copy. For external input folders use read_roots: absolute existing folders; "
       "the tool installs the read grants and reports effective roots. Correct refused setup and retry before timer_start. "
       "Legacy directives may grant a project folder: ⟪GRANT::<path>⟫ to read, test and run programs there; "
       "write grants (⟪GRANT::WRITE::<path>⟫) are switched off and read as ⟪GRANT::<path>⟫.",
       dict(agent_id=_S, teachframes=dict(type="array", items=_S), directives=dict(type="array", items=_S),
            read_roots=dict(type="array", items=_S)), ["agent_id"]),
    _f("timer_start", "Turn on the Aether Timer's dispatch: ready tasks go to their agents.", {}, []),
    _f("message_agent", "Ask one staff agent something. Its answer comes back to you only, as a REPLY notice.",
       dict(agent_id=_S, text=_S), ["agent_id", "text"]),
    _f("closure_send", "Close the sprint for staff: every hired agent gets its closure turn, then the write-back. "
       "You get CLOSURE_DONE.", {}, []),
    _f("sprint_finish", "Record the outcome after staff closure: result pass or fail, with a note.",
       dict(result=dict(type="string", enum=["pass", "fail"]), note=_S), ["result", "note"]),
    _f("operator_note", "A line for the human operator. With stop set to money, consent, secrets or harm, dispatch "
       "pauses until the operator answers; otherwise it only informs.",
       dict(text=_S, stop=dict(type="string", enum=list(STOPS))), ["text"]),
]

OVERSEER_FILE_TOOLS = [
    _f("read_file", "Read a file in the sprint folder (paths relative to it). A large file comes in parts: offset "
       "(characters) and limit choose the part.", dict(path=_S, offset=_I, limit=_I), ["path"]),
    _f("list_dir", "List the files under a folder of the sprint.", dict(path=_S), []),
    _f("write_file", "Write a file under outputs/ (a solo sprint's deliverables).", dict(path=_S, text=_S),
       ["path", "text"]),
]

def staff_schemas(wake_kind: str, delegation: bool = False) -> list:
    """Every wake carries every staff tool; the wake's own road says which ones the moment calls for (Michel,
    10-30: a glaring sign, never a fence; design for the minds that will follow the road better)."""
    schemas = list(STAFF_TOOLS)
    if delegation:
        from .ultralight import schema
        schemas.append(schema())
    return schemas


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _read_text(path: Path, offset=0, limit=None) -> str:
    """A page of a file: characters offset to offset+limit (at most MAX_TOOL_RESULT). When more remains, the
    answer ends by saying exactly how to read the next page, so any part of a large file can be reached."""
    data = path.read_text(encoding="utf-8", errors="replace")
    start = max(0, int(offset or 0))
    size = min(int(limit or MAX_TOOL_RESULT), MAX_TOOL_RESULT)
    page = data[start:start + size]
    end = start + len(page)
    if start or end < len(data):
        more = f"; read_file(path, offset={end}) for the next part" if end < len(data) else "; that is the end"
        page += f"\n[characters {start}-{end} of {len(data)}{more}]"
    return page


def _listing(base: Path, shown_from: Path) -> str:
    files = []
    for p in sorted(base.rglob("*")):
        if p.is_file():
            try:
                files.append(p.relative_to(shown_from).as_posix())
            except ValueError:
                files.append(str(p))
    return "\n".join(files)[:MAX_TOOL_RESULT] or "(empty)"


def child_environment() -> dict:
    """The environment of a program a mind runs: the system's bootstrap variables only (no credential), UTF-8, and
    no Python or pytest cache files, so running a program never changes a folder as a side effect (M7 rerun: a
    pytest run in another task's outputs rewrote its .pytest_cache)."""
    return dict(test_environment(), PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1",
                PYTEST_ADDOPTS="-p no:cacheprovider")


NOTE_SIGN = 300


def _note_sign(note: str) -> str:
    """A sign, never a refusal: the note is filed as given; a long one is answered with the road for next time
    (both live runs closed tasks with 700 to 1,800 character notes; the Overseer reads every one)."""
    if len(note) <= NOTE_SIGN:
        return f" Note: {note}"
    return (f" Your note is filed as given ({len(note)} characters). The Overseer reads every note: next time keep it "
            f"to one line and put the detail in the files your task prompt names.")


def _call(handler, name: str, args) -> str:
    """Call a tool handler; only arguments that do not fit its signature are answered as wrong arguments."""
    args = dict(args or {})
    try:
        inspect.signature(handler).bind(**args)
    except TypeError as exc:
        return f"refused: {name} was called with the wrong arguments ({exc})"
    return handler(**args)


class StaffTools:
    """One staff turn's tools, for one agent and one wake."""

    def __init__(self, sprint: Sprint, agent_id: str, wake: Wake, delegation=None):
        self.sprint, self.agent, self.wake, self.delegation = sprint, agent_id, wake, delegation
        self.root = sprint.root.resolve()
        self.written: list = []
        self.remaining_calls = 40
        history = [json.loads(p.read_text(encoding='utf-8')) for p in (sprint.root/'turns').glob('*.json')]
        closing = wake.kind == 'CLOSURE'
        self.recovery_task = wake.task or self.active()
        key = f'{agent_id}:CLOSURE' if closing else f'{agent_id}:T{self.recovery_task}'
        self.recovery = trackers(history).get(key, ClosureRecovery() if closing else Recovery())
        self.halted = bool(self.recovery.violations)

    def halt(self, kind, detail):
        """Persist a deterministic stop; never retry a tool or author the agent's work."""
        self.halted = True
        from .terminal import stop
        stop(self.sprint,kind,detail,agent=self.agent,wake=self.wake.kind,task=self.recovery_task)

    @property
    def tasker(self):
        return self.sprint.tasker(self.agent)

    # what this agent may touch -------------------------------------------------------------------------------
    def active(self) -> Optional[int]:
        mine = self.tasker.list(status="Active", assigned_to=self.agent)
        return mine[0].number if mine else None

    def grants(self, write: bool = False) -> list:
        """Folders the Overseer granted in this agent's directives: all of them, or only those granted for writing."""
        roots = []
        folder = self.sprint.briefcase(self.agent) / "directives"
        for path in sorted(folder.glob("*.txt")) if folder.is_dir() else []:
            for writable, raw in GRANT.findall(path.read_text(encoding="utf-8")):
                if write and not writable:
                    continue
                p = Path(raw.strip())
                roots.append((p if p.is_absolute() else self.root / p).resolve())
        return roots

    def runnable(self) -> list:
        """Where tests and programs run: your outputs, the outputs of the tasks yours depends on, and grants."""
        numbers = set()
        for t in self.tasker.list(assigned_to=self.agent):
            numbers.add(t.number)
            numbers.update(t.depends_on)
        return [self.sprint.outputs(k).resolve() for k in sorted(numbers)] + self.grants()

    def _place(self, raw: str) -> Path:
        """A path as run_tests and run_command take it: absolute, from the sprint folder (outputs/...), inside a
        granted folder, or relative to your Active task's outputs folder."""
        raw = (raw or ".").strip().replace("\\", "/")
        n = self.active()
        if Path(raw).is_absolute() or raw.startswith("outputs/") or n is None:
            return self._resolve(raw)
        if any(_inside(self.root / raw, g) for g in self.grants()):
            return (self.root / raw).resolve()
        return (self.sprint.outputs(n) / raw).resolve()

    def readable(self) -> list:
        tk = self.tasker
        numbers = set()
        for t in tk.list(assigned_to=self.agent):
            numbers.add(t.number)
            numbers.update(t.depends_on)
        roots = [staff_home.home_file(self.sprint.data_root, self.agent).resolve(),
                 self.sprint.briefcase(self.agent).resolve(), self.root / "SPRINT_GOALS.md",
                 self.root / "SPRINT_OVERVIEW.md"]
        roots += [self.sprint.outputs(n).resolve() for n in sorted(numbers)]
        return roots + self.grants()

    def _resolve(self, raw: str) -> Path:
        raw = (raw or ".").strip()
        p = Path(raw)
        return (p if p.is_absolute() else self.root / p).resolve()

    def _may_read(self, path: Path) -> bool:
        return any(path == r or _inside(path, r) for r in self.readable())

    # the calls -----------------------------------------------------------------------------------------------
    def call(self, name: str, args: dict) -> str:
        from .terminal import failure
        if self.halted or failure(self.sprint):
            return 'not run: this task has stopped; preserve its evidence.'
        problem = self.recovery.before(name, args)
        if problem:
            self.halt(*problem)
            return 'not run: ' + problem[1]
        result = self._dispatch(name, args)
        if failure(self.sprint):
            self.halted = True
        if name in COMMANDS and not hasattr(result, 'command_outcome') and str(result).startswith('refused:'):
            result = CommandResult(str(result), 'refused')
        problem = self.recovery.observe(name, args, result, ref=len(self.recovery.attempts))
        if problem:
            self.halt(*problem)
        return result

    def _dispatch(self, name: str, args: dict) -> str:
        try:
            handler = getattr(self, "_" + name, None)
            if name == "delegate_ultralight":
                if self.delegation is None:
                    return "Ultralight delegation is unavailable here: no model connection named ultralight."
                return self.delegation.call(args, remaining_calls=self.remaining_calls)
            if handler is None or name not in {s["function"]["name"] for s in STAFF_TOOLS}:
                return f"unknown tool {name}"
            return _call(handler, name, args)
        except REFUSALS as exc:
            return f"refused: {exc}"
        except Exception as exc:  # a tool failure is a result for the mind, never a crash of the turn
            return f"{type(exc).__name__}: {exc}"

    def _tasker_read(self) -> str:
        tk = self.tasker
        number = self.wake.task or self.active()
        if number is None:
            mine = tk.list(assigned_to=self.agent)
            return "[YOUR_TASKS] := [\n" + ",\n".join("  " + task_line(t) for t in mine) + "\n]" if mine else "(no tasks)"
        t = tk.get(number)
        lines = [f"[CURRENT_TASK] := {task_line(t)}", "[PROMPT] := ⌞", t.prompt, "⌟", "[DEPENDS_ON] := ["]
        for d in t.depends_on:
            lines.append("  " + task_line(tk.get(d)) + f"  // {self.sprint.outputs(d).resolve()}")
        lines.append("]")
        lines.append(f"[OUTPUTS] := {self.sprint.outputs(t.number).resolve()}")
        return "\n".join(lines)

    def _task_start(self, number) -> str:
        number = int(number)
        if self.wake.kind != "TASK":
            # the contract's rule (a task starts only when the Timer gives it), said as a road
            raise PermissionError(f"this is a {self.wake.kind} wake, not a task: the Timer wakes you with a TASK "
                                  f"wake when your task is ready. Now, answer what this wake asks")
        if self.wake.task is not None and number != self.wake.task:
            raise PermissionError(f"the Timer gave you T{self.wake.task}; start that one, not T{number}")
        t = self.tasker.start(number)
        self.sprint.outputs(number).mkdir(parents=True, exist_ok=True)
        return (f"T{t.number} is Active. Output directory: {self.sprint.outputs(t.number).resolve()}. "
                'write_file takes the bare output filename; omit run_command cwd to run here.')

    def _task_close(self, number, note) -> str:
        t = self.tasker.close(int(number), note)
        return f"T{t.number} is Closed." + _note_sign(t.note)

    def _task_block(self, number, note) -> str:
        t = self.tasker.block(int(number), note)
        return f"T{t.number} is Blocked. The Overseer is told." + _note_sign(t.note)

    def _read_file(self, path, offset=0, limit=None) -> str:
        p = self._resolve(path)
        if not self._may_read(p):
            raise PermissionError(f"{path} is outside what you may read; list_dir('.') shows what you may read")
        if not p.exists():
            return self._not_found(path, p)
        if p.is_dir():
            return _listing(p, self.root)
        return _read_text(p, offset, limit)

    def _not_found(self, path, p: Path) -> str:
        """A missing file is an answer with the road, never a refusal (t2: a closure turn looked for a file the
        README promised and got a raw [Errno 2])."""
        folder = p.parent
        while not folder.exists() and folder.parent != folder:
            folder = folder.parent
        if folder.is_dir() and self._may_read(folder):
            try:
                where = folder.relative_to(self.root).as_posix() + "/"
            except ValueError:
                where = str(folder)
            return f"not found: {path}. {where} holds:\n{_listing(folder, self.root) or '(nothing yet)'}"
        return f"not found: {path}. list_dir('.') shows what you may read."

    def _list_dir(self, path=".") -> str:
        if (path or ".").strip() in (".", "", "./"):
            lines = []
            for r in self.readable():
                if r.exists():
                    try:
                        lines.append(r.relative_to(self.root).as_posix() + ("/" if r.is_dir() else ""))
                    except ValueError:
                        lines.append(str(r) + ("/" if r.is_dir() else "") + "   (granted)")
            return "\n".join(lines) or "(nothing yet)"
        p = self._resolve(path)
        if not self._may_read(p) and p.is_dir():
            # a folder that holds folders you may read (staff list "outputs" first: refused 4 times in two live
            # runs) shows those, so the road is paved instead of fenced
            inside = [r for r in self.readable() if _inside(r, p) and r.exists()]
            if inside:
                return "\n".join(_listing(r, self.root) if r.is_dir() else r.relative_to(self.root).as_posix()
                                 for r in sorted(inside))
        if not self._may_read(p):
            raise PermissionError(f"{path} is outside what you may read; list_dir('.') shows what you may read")
        return _listing(p, self.root) if p.is_dir() else p.name

    def _write_file(self, path, text) -> str:
        n = self.active()
        if n is None:
            raise PermissionError("you have no Active task; write_file writes under outputs/T<n>/ of your Active task")
        out = self.sprint.outputs(n).resolve()
        writable = self.grants(write=True) if WRITE_GRANTS else []
        raw = (path or "").strip().replace("\\", "/")
        if Path(raw).is_absolute():
            p = Path(raw).resolve()
        elif raw.startswith("outputs/") or any(_inside(self.root / raw, g) for g in writable):
            p = (self.root / raw).resolve()
        else:
            p = (out / raw).resolve()
        if p == out or not (_inside(p, out) or any(_inside(p, g) and p != g for g in writable)):
            granted = " or a folder granted for writing" if writable else ""
            if not WRITE_GRANTS and any(p == g or _inside(p, g) for g in self.grants(write=True)):
                raise PermissionError(f"write grants are switched off (Michel, 10-30): write under outputs/T{n}/; the "
                                      f"Overseer or the operator brings it into the project")
            raise PermissionError(f"you can write only under outputs/T{n}/{granted}; {path} is outside it")
        write_text(p, str(text))
        rel = p.relative_to(self.root).as_posix() if _inside(p, self.root) else str(p)
        self.written.append(rel)
        return f"wrote {len(str(text).encode('utf-8'))} bytes to {rel}"

    def _run_tests(self, target, retry=False) -> str:
        p = self._place(target)
        # your outputs, the outputs of the tasks yours depends on (a reviewer runs the tests it reviews), and grants
        root = next((r for r in self.runnable() if p == r or _inside(p, r)), None)
        if root is None or not p.exists():
            raise PermissionError(f"run_tests runs under your outputs, the outputs of tasks yours depends on, or a "
                                  f"granted root; {target} is not there")
        command, base, runner = test_command(p, root)
        # bytes, decoded as UTF-8: text mode decodes with the console code page, and on Windows a glyph it cannot
        # decode leaves stdout None (seen live: a reviewer's tests printing Lexicon glyphs)
        env = child_environment()
        try:
            proc = run_program(command, cwd=str(base), env=env, timeout=300)
        except subprocess.TimeoutExpired:
            return CommandResult("[timed out after 300 s; the tests were stopped]", "timed_out")
        except FileNotFoundError:
            return CommandResult("[no program named test runner was found]", "not_found")
        output = ((proc.stdout or b"") + (proc.stderr or b"")).decode("utf-8", errors="replace").strip()
        header = f"[{runner}, exit code {proc.returncode}]\n"
        return CommandResult(header + output[-(MAX_TOOL_RESULT-len(header)):],
                             "succeeded" if proc.returncode == 0 else "failed", proc.returncode)

    def _run_command(self, argv, cwd=None, retry=False) -> str:
        if isinstance(argv, str) or not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise ValueError('argv is a list of strings, for example ["python", "tool.py", "100", "C", "F"]; '
                             'never shell text')
        if cwd is None and self.active() is None:
            raise ValueError("you have no Active task; give cwd (an outputs folder you may run in, or a granted folder)")
        place = self._place(cwd or ".")
        if not place.is_dir() or not any(place == r or _inside(place, r) for r in self.runnable()):
            raise PermissionError(f"run_command runs in your outputs, the outputs of tasks yours depends on, or a "
                                  f"granted folder; {cwd or '.'} is not one of them. "
                                  f"Active task output directory: {self.sprint.outputs(self.active()).resolve() if self.active() else '(none)'}. "
                                  'For your own Active task omit cwd. No program was executed')
        command = list(argv)
        if command[0].lower() in PYTHONS:
            command[0] = sys.executable          # this runtime's Python, never whatever "python" resolves to
        env = child_environment()
        try:
            proc = run_program(command, cwd=str(place), env=env, timeout=RUN_TIMEOUT)
        except subprocess.TimeoutExpired:
            return CommandResult(f"[timed out after {RUN_TIMEOUT} s; the program was stopped]", "timed_out")
        except FileNotFoundError:
            return CommandResult(f"[no program named {argv[0]!r} was found]", "not_found")
        half = MAX_TOOL_RESULT // 2
        out = (proc.stdout or b"").decode("utf-8", errors="replace")[-half:]
        err = (proc.stderr or b"").decode("utf-8", errors="replace")[-half:]
        return CommandResult(f"[exit code {proc.returncode}]\n[cwd {place}]\n--- stdout ---\n{out}\n--- stderr ---\n{err}",
                             "succeeded" if proc.returncode == 0 else "failed", proc.returncode)

    def _identity_close(self, s1, s2_entry, retry=False) -> str:
        if retry and self.wake.kind != 'CLOSURE':
            raise ValueError('closure-format recovery is available only during your CLOSURE wake')
        try:
            filed = staff_home.close_identity(self.sprint, self.agent, s1, s2_entry)
        except staff_home.ClosureFormatError as exc:
            if self.wake.kind != 'CLOSURE': raise
            return ClosureResult(f'refused: format_error: {exc}. Correct only your closure text and call '
                                 'identity_close(s1, s2_entry, retry=true); at most two corrective attempts.', 'format_error')
        if self.wake.kind != "CLOSURE":                 # a sign, not a refusal: the last call before write-back counts
            return (f"filed, though this is a {self.wake.kind} wake: your identity is closed at the CLOSURE wake. "
                    f"Call identity_close again then; the last call is the one that goes home.")
        return ClosureResult(f"filed: System 1 ({filed['system1_chars']} characters) and reflection {filed['reflection'] or '(no id)'}"
                " in your Staff Home closure draft, exactly as given. Write-back saves them with backups.", 'succeeded')


class OverseerTools:
    """The Overseer's Tasker tools and its file tools, for one sprint."""

    def __init__(self, sprint: Sprint, *, catalog_path=None):
        self.sprint = sprint
        self.root = sprint.root.resolve()
        self.catalog_path = catalog_path
        self.actor = f"overseer:{sprint.overseer}"
        self.written: list = []

    @property
    def tasker(self):
        return self.sprint.tasker(self.actor)

    def call(self, name: str, args: dict) -> str:
        try:
            handler = getattr(self, "_" + name, None)
            known = {s["function"]["name"] for s in OVERSEER_TASKER_TOOLS + OVERSEER_FILE_TOOLS}
            if handler is None or name not in known:
                return f"unknown tool {name}"
            return _call(handler, name, args)
        except REFUSALS as exc:
            return f"refused: {exc}"
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"

    def _hired(self) -> list:
        return list(self.sprint.record().get("staff", []))

    def _require_hired(self, agent_id) -> None:
        if agent_id and agent_id not in self._hired():
            raise PermissionError(f"{agent_id} is not hired on this sprint; staff_hire first (staff_list shows who "
                                  f"is available)")

    def _tasker_read(self, filter=None) -> str:
        filter = filter or {}
        tk = self.tasker
        tasks = tk.list(status=filter.get("status"), assigned_to=filter.get("assigned_to"))
        lines = ["[TASKS] := ["] + ["  " + task_line(t) for t in tasks] + ["]"]
        lines.append("[TASKER]  := {" + ", ".join(f"{k}: {v}" for k, v in tk.counts().items()) + "}")
        return "\n".join(lines)

    def _task_add(self, title, prompt, assigned_to=None, depends_on=None) -> str:
        self._require_hired(assigned_to)
        n = self.tasker.add(title, prompt, assigned_to=assigned_to, depends_on=depends_on)
        return (f"added T{n}" + (f" for {assigned_to}" if assigned_to else " (unassigned)") +
                f"; output directory: {self.sprint.outputs(n).resolve()}; staff run_command omits cwd for this task")

    def _task_edit(self, number, title=None, prompt=None, assigned_to=UNSET, depends_on=None) -> str:
        if assigned_to is not UNSET:
            self._require_hired(assigned_to)
        t = self.tasker.edit(int(number), title=UNSET if title is None else title,
                             prompt=UNSET if prompt is None else prompt, assigned_to=assigned_to,
                             depends_on=UNSET if depends_on is None else depends_on)
        return "edited " + task_line(t)

    def _task_unblock(self, number, note, prompt=None, assigned_to=None) -> str:
        if assigned_to:
            self._require_hired(assigned_to)
        t = self.tasker.unblock(int(number), note, prompt=UNSET if prompt is None else prompt,
                                assigned_to=UNSET if assigned_to is None else assigned_to)
        return "unblocked " + task_line(t)

    def _task_drop(self, number, note) -> str:
        return "dropped " + task_line(self.tasker.drop(int(number), note))

    def _staff_list(self) -> str:
        rows = staff_home.roster(self.sprint.data_root)
        if not rows:
            return "(the Staff Home is empty; the operator seeds it with: python -m aether.tasker staff seed)"
        out = []
        for e in rows:
            state = e["state"].replace("on:", "on sprint ")
            out.append(f"{e['id']} · {e['name']} · {e['role']} · {state}\n    resume: {e['resume']}\n"
                       f"    model: {e['model_rec']}")
        return "\n".join(out)

    def _teachframe_list(self) -> str:
        """The catalog, so the Overseer chooses real frames (M7 rerun: a guessed name left every briefcase without
        TeachFrames). Archived frames are listed last and marked so."""
        from aether.knowledge.school import DEFAULT_CATALOG_PATH, load_catalog
        frames = load_catalog(self.catalog_path or DEFAULT_CATALOG_PATH)
        lines = []
        for name, f in sorted(frames.items(), key=lambda kv: (kv[1].state == "ARCHIVE", kv[0])):
            head = f"{name} · {f.state}" + (f" · {f.title}" if f.title else "")
            lines.append(head + (f"\n    prevents: {f.prevents}" if f.prevents else ""))
        return "\n".join(lines) + "\nPut these names in briefcase_create teachframes (TEACHFRAME_ prefix optional)."

    def _staff_hire(self, agent_ids) -> str:
        if isinstance(agent_ids, str):
            agent_ids = [a.strip() for a in agent_ids.split(",")]
        hired = staff_home.hire(self.sprint, agent_ids)
        return f"hired {', '.join(hired)}; identities stay in Staff Home. Next: briefcase_create for each agent; only agent_id is required."

    def _sprint_overview_write(self, text) -> str:
        if not (text or "").strip():
            raise ValueError("the overview needs words: what the sprint is about and what it is trying to accomplish")
        body = text if text.endswith("\n") else text + "\n"
        if not body.startswith(OVERVIEW_HEADER):        # Michel, 10-30: an embedded directive, not a lock
            body = OVERVIEW_HEADER + "\n" + body
        write_text(self.root / "SPRINT_OVERVIEW.md", body)
        sign = "" if ("∆WMC{" in text and "WORLD_MODEL" in text) else (
            " It is filed as given. The overview is a directive: write it in the Lexicon, one ∆WMC block with a "
            "WORLD_MODEL (Michel, 10-30).")
        return "wrote SPRINT_OVERVIEW.md (the header saying it is only editable by the Overseer is added for you)." + sign

    def _briefcase_create(self, agent_id, teachframes=None, directives=None, read_roots=None) -> str:
        self._require_hired(agent_id)
        try:
            made = create_briefcases(self.sprint, {agent_id: dict(teachframes=teachframes or [], directives=directives or [],
                                     read_roots=read_roots if read_roots is not None else [])}, catalog_path=self.catalog_path)
            roots = effective_roots(self.sprint, agent_id)
        except (BriefcaseError, ValueError) as exc:
            def failed(data):
                data.setdefault('briefcase_setup_errors', {})[agent_id] = str(exc)
                data['dispatch'] = False
            self.sprint.update(failed)
            raise
        self.sprint.update(lambda d: d.setdefault('briefcase_setup_errors', {}).pop(agent_id, None))
        return (f"briefcase ready: {agent_id} ({len(made[agent_id]['files'])} files). "
                f"Effective external read roots: {json.dumps(roots, ensure_ascii=False)}. "
                "Own task outputs and declared dependencies remain available; write grants are off.")

    def _timer_start(self) -> str:
        errors = self.sprint.record().get('briefcase_setup_errors', {})
        if errors:
            raise BriefcaseError('Dispatch remains off. Correct briefcase_create for ' + ', '.join(sorted(errors)) +
                                 ' and retry timer_start. ' + '; '.join(errors.values()))
        for agent in self._hired():
            folder = self.sprint.briefcase(agent)
            if not (folder/'README.txt').is_file() or not (folder/'manifest.json').is_file():
                raise BriefcaseError(f'Dispatch remains off: call briefcase_create for {agent} first.')
            effective_roots(self.sprint, agent)
        self.sprint.update(lambda d: d.update(dispatch=True, state="running"))
        note = "" if (self.root / "SPRINT_OVERVIEW.md").exists() else (
            " Note: SPRINT_OVERVIEW.md is not written yet; staff read it first.")
        return f"dispatch is on; {len(self.tasker.ready())} task(s) ready now.{note}"

    def _message_agent(self, agent_id, text) -> str:
        self._require_hired(agent_id)
        if not (text or "").strip():
            raise ValueError("a message needs words")
        item = post_request(self.sprint, "message", agent=agent_id, text=text)
        return f"queued message {item['id']} for {agent_id}; the answer comes back as a REPLY notice"

    def _closure_send(self) -> str:
        if not self._hired():
            raise ValueError("no staff were hired; a solo sprint goes straight to sprint_finish")
        self.sprint.update(lambda d: d.update(state="closing"))
        post_request(self.sprint, "closure")
        return "closure sent: each hired agent gets its closure turn, then the write-back; you get CLOSURE_DONE"

    def _sprint_finish(self, result, note) -> str:
        if result not in ("pass", "fail"):
            raise ValueError("result is pass or fail")
        data = self.sprint.record()
        from .completion import staff_complete
        if data.get("staff") and (not data.get("closure_done") or not staff_complete(self.sprint)):
            raise PermissionError("staff closure has not finished; call closure_send and wait for CLOSURE_DONE")
        if result == 'pass':
            from .verification import finish
            finish(self.sprint)
        from ._files import now
        self.sprint.update(lambda d: d.update(state=f"closed-{result}", dispatch=False,
                                              result={"result": result, "note": note, "at": now()}))
        return f"sprint {self.sprint.id} recorded as {result}. Next: roadmap_update and your own closure."

    def _operator_note(self, text, stop=None) -> str:
        if stop is not None and stop not in STOPS:
            raise ValueError(f"stop is one of {', '.join(STOPS)} (or leave it out to only inform)")
        operator_line(self.sprint, text, stop)
        if stop:
            self.sprint.update(lambda d: d.update(paused_for=stop))
            return f"the operator is told; dispatch pauses for {stop} until the operator answers"
        return "the operator is told"

    # the solo sprint's file tools ----------------------------------------------------------------------------------
    def _resolve(self, raw: str) -> Path:
        p = Path((raw or ".").strip())
        p = (p if p.is_absolute() else self.root / p).resolve()
        if not (p == self.root or _inside(p, self.root)):
            raise PermissionError(f"{raw} is outside the sprint folder")
        return p

    def _read_file(self, path, offset=0, limit=None) -> str:
        p = self._resolve(path)
        return _listing(p, self.root) if p.is_dir() else _read_text(p, offset, limit)

    def _list_dir(self, path=".") -> str:
        return _listing(self._resolve(path), self.root)

    def _write_file(self, path, text) -> str:
        raw = (path or "").strip().replace("\\", "/")
        p = self._resolve(raw if raw.startswith("outputs/") else "outputs/" + raw)
        out = (self.root / "outputs").resolve()
        if not _inside(p, out):
            raise PermissionError(f"write_file writes under outputs/; {path} is outside it")
        write_text(p, str(text))
        rel = p.relative_to(self.root).as_posix()
        self.written.append(rel)
        return f"wrote {rel}"
