"""The Aether Timer (contract §8): the carrier. One per sprint. It watches the Tasker and the sprint folder, wakes the
right mind with the right notice, and does nothing else.

Each tick: finished turns are taken in (retries, the stale watch, replies, closure progress); new changes are batched
for the Overseer; requests from the Overseer's tools are read; then turns start, within the caps: the Overseer's
next notice, retries, re-prompts, messages, closure turns, and ready tasks in number order. Then the quiet check and
the closure's end (write-back, CLOSURE_DONE).

Rules: one turn per mind at a time; the Overseer's turns one after another; a task only when every dependency is
Closed and its agent has no Active task; the Overseer is not woken by its own changes.
Never: read what a mind wrote to decide anything, change a task, reorder the dependency rule, or wake a mind that has
no reason in the table, the request queue or the closure list.
"""
from __future__ import annotations

import os
import threading
from copy import deepcopy
from pathlib import Path
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Optional

from aether.keys.redaction import redact

from . import meter
from . import notices
from . import transcripts
from . import staff as staff_home
from .terminal import stop, failure, Outcome
from .completion import staff_complete
from ._files import ProcessLock, now, read_json, write_json
from .sprint import DEFAULT_PRICES, Sprint, TurnResult, Wake, operator_line, requests_after

RETRY_DELAYS = (5.0, 30.0)          # two retries with backoff, then TURN_FAILED
NOT_RETRIED = ("SetupError", "ContextCapacityError")
TICK_ERRORS_MAX = 5                 # the same tick failing this often in a row ends the Timer, said in its EXIT line


@dataclass
class Turn:
    id: int
    mind: str                       # staff agent id, or 'overseer'
    wake: Wake
    attempt: int = 0
    question: str = ""              # a MESSAGE turn's question, for the REPLY
    reprompt: bool = False          # a TASK turn re-prompted because its task stayed Active
    started: str = ""
    future: object = None
    key: tuple = ()                 # the meter key of this turn's live spend


@dataclass
class Pending:
    """A staff turn waiting to start (a retry waits for its time)."""
    mind: str
    wake: Wake
    attempt: int = 0
    due: float = 0.0
    question: str = ""
    reprompt: bool = False


class Timer:
    def __init__(self, sprint: Sprint, *, overseer_runner: Callable, staff_runner: Callable,
                 clock: Callable[[], float] = time.time, interval: float = 3.0, log: Optional[Callable] = None,
                 retry_delays=RETRY_DELAYS):
        self.sprint = sprint
        self.overseer_runner, self.staff_runner = overseer_runner, staff_runner
        self.clock, self.interval = clock, interval
        self.retry_delays = tuple(retry_delays)
        self.echo = log
        self.timer_dir = sprint.root / "timer"
        self.lock: Optional[ProcessLock] = None
        self.pool: Optional[ThreadPoolExecutor] = None
        self.running: dict = {}                 # mind -> Turn
        self.overseer_queue: list = []          # Wake, or the string "CHANGES" (a batch still filling)
        self.pending_changes: list = []
        self.pending: list = []                 # Pending staff turns
        self.extra_lines: list = []             # caps and operator lines riding on the Overseer's next notice
        self.state = {}
        self._stopping = threading.Event()
        self._cost_cap = None                   # cached each tick, read by turns in flight through stopped()
        self._prices = DEFAULT_PRICES["*"]
        self._prints = None                     # the change record's last fingerprint (size and mtime, never content)
        self._finished: list = []               # turns taken in this tick, still answerable for what changed
        self._ids = 0

    # ---- life --------------------------------------------------------------------------------------------------

    def start(self) -> "Timer":
        self.lock = ProcessLock(self.timer_dir / "timer.lock",
                                f"a Timer is already running for sprint {self.sprint.id}; one Timer per sprint")
        saved = read_json(self.timer_dir / "timer.json", {}) or {}
        if list((self.timer_dir/'active').glob('*.json')):
            stop(self.sprint,'interrupted_turn','A launched turn was not durably collected; preserve its home execution journal and reconcile before any new work.')
        self.state = {
            "pid": os.getpid(), "started": now(), "heartbeat": now(),
            "cursor_changes": saved.get("cursor_changes", 0), "cursor_requests": saved.get("cursor_requests", 0),
            "turns_started": saved.get("turns_started", 0), "cost_used": saved.get("cost_used", 0.0),
            "tokens_used": saved.get("tokens_used", 0), "hired_sent": saved.get("hired_sent", False),
            "quiet_sent": saved.get("quiet_sent", False), "held": saved.get("held", {}),
            "failed": saved.get("failed", {}), "stale": saved.get("stale", {}), "closure": saved.get("closure"),
            "cap": saved.get("cap"), "operator_lines_seen": saved.get("operator_lines_seen", 0),
            "overseer_closure_corrections": saved.get("overseer_closure_corrections", 0),
        }
        self._ids = max([int(p.stem) for p in (self.sprint.root/'turns').glob('*.json')] + [saved.get('turns_started',0)])
        workers = int(self.caps().get("max_parallel_turns") or 3) + 1
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"timer-{self.sprint.id}")
        self._restore(saved.get("queue") or {})
        if not self.state["hired_sent"]:
            goals = (self.sprint.root / "SPRINT_GOALS.md").read_text(encoding="utf-8")
            self.overseer_queue.append(notices.hired_notice(self.sprint, goals))
            self.state["hired_sent"] = True
        self.log("START", f"pid {os.getpid()}")
        self._save()
        return self

    def _restore(self, queue: dict) -> None:
        """What the last run was still carrying, saved each tick: the Overseer's notices, the changes not yet shown
        to it, and the staff turns waiting to start. Then every Active task whose mind has nothing waiting gets its
        TASK wake again, marked RESUMED. An unresolved launch marker blocks execution after a crash."""
        def wake(item):
            return item if isinstance(item, str) else Wake(item["kind"], item["text"], item.get("task"))
        tk = self.sprint.tasker("operator")
        ids = set(queue.get("changes") or [])
        if ids:
            self.pending_changes = [c for c in tk.changes_after(min(ids) - 1) if c.id in ids]
        self.overseer_queue = [wake(x) for x in queue.get("overseer") or []
                               if x != "CHANGES" or self.pending_changes]
        self.pending = [Pending(p["mind"], wake(p["wake"]), p.get("attempt", 0), 0.0, p.get("question", ""),
                                p.get("reprompt", False)) for p in queue.get("staff") or []]
        waiting = {p.mind for p in self.pending}
        for t in tk.list(status="Active"):
            if t.assigned_to and t.assigned_to not in waiting:
                self.pending.append(Pending(t.assigned_to, notices.resumed(
                    notices.task_wake(self.sprint, t.assigned_to, t.number))))
                waiting.add(t.assigned_to)
                self.log("RESUMED", f"{t.assigned_to} T{t.number} is Active with no turn; woken again")

    def _queue(self) -> dict:
        def wake(item):
            return item if isinstance(item, str) else {"kind": item.kind, "text": item.text, "task": item.task}
        return {"overseer": [wake(x) for x in self.overseer_queue], "changes": [c.id for c in self.pending_changes],
                "staff": [{"mind": p.mind, "wake": wake(p.wake), "attempt": p.attempt, "question": p.question,
                           "reprompt": p.reprompt} for p in self.pending]}

    def close(self) -> None:
        if self.pool:
            self.pool.shutdown(wait=True)
        self._save()
        if self.lock:
            self.lock.release()
            self.lock = None

    def run(self) -> str:
        """The loop: tick until STOP (after in-flight turns) or the sprint is closed and quiet. Returns why it ended."""
        errors, last = 0, ""
        try:
            while True:
                try:
                    why = self.tick()
                    errors = 0
                except Exception as exc:  # the carrier must outlive one bad tick; it never orphans a sprint silently
                    errors += 1
                    text = f"{type(exc).__name__}: {exc}"
                    self.log("TIMER_ERROR", f"tick {errors} in a row: {text}")
                    if text != last:
                        self._operator(f"the Timer hit an error and keeps carrying: {text}")
                        last = text
                    why = None
                    if errors >= TICK_ERRORS_MAX:
                        stop(self.sprint,'timer_failed',f'the Timer failed {errors} ticks in a row: {text}')
                        why = failure(self.sprint)
                if why:
                    self.log("EXIT", why)
                    stop_path = self.timer_dir / "STOP"
                    if stop_path.exists() and not (self.timer_dir / 'RECOVERY_STOP.json').exists():
                        stop_path.unlink()
                    return why
                time.sleep(self.interval)
        finally:
            self.close()

    def drain(self, timeout: float = 30.0) -> None:
        """Wait for the turns in flight (tests and shutdown)."""
        for turn in list(self.running.values()):
            if turn.future is not None:
                turn.future.result(timeout=timeout)

    # ---- one tick ----------------------------------------------------------------------------------------------

    def tick(self) -> Optional[str]:
        record = self.sprint.record()
        self.sprint.staff = list(record.get("staff", []))
        # a sprint without prices still counts money, at the default estimate: a cost cap must always be able to trip
        self._prices = (record.get("prices") or {}).get("*") or DEFAULT_PRICES["*"]
        self._cost_cap = (record.get("caps") or {}).get("max_cost_per_sprint")
        resume_file = self.timer_dir / "RESUME"
        if resume_file.exists():
            resume_file.unlink()
            if self.state["held"]:
                self.log("RESUME", "the operator cleared " + ", ".join(sorted(self.state["held"])))
            self.state["held"] = {}
        self._collect()
        self._watch()
        self._changes()
        self._requests()
        self._operator_lines()
        stopping = (self.timer_dir / "STOP").exists() or (self.timer_dir / 'RECOVERY_STOP.json').exists()
        if stopping and not self._stopping.is_set():
            self._stopping.set()
            self.log("STOPPING", "STOP file found; finishing the turns in flight")
        if not stopping and self._caps_ok():
            self._start_overseer()
            self._start_staff()
        self._quiet()
        if not stopping and self._closure_end():
            self._prints = self._fingerprint()  # the write-back is the Timer's own change
        self.state["heartbeat"] = now()
        self._save()
        if (stopping or failure(self.sprint)) and not self.running:
            return failure(self.sprint) or Outcome("stopped by the STOP file")
        record = self.sprint.record()
        if (record.get("state", "").startswith("closed-") and not self.running and not self.overseer_queue
                and not self.pending_changes):
            if self._overseer_finished():
                if not staff_complete(self.sprint):
                    stop(self.sprint,'staff_closure_missing','Required applied staff closure receipts are missing or inconsistent.')
                    return failure(self.sprint)
                return Outcome(f"sprint {record['state']}",0 if record['state']=='closed-pass' else 2)
            corrections = self.state["overseer_closure_corrections"]
            if corrections >= 2:
                why = "Overseer closure receipt missing after two correction wakes"
                self._operator(why)
                self._save()
                stop(self.sprint,'overseer_closure_missing',why)
                return failure(self.sprint)
            self.state["overseer_closure_corrections"] = corrections + 1
            self._to_overseer(notices.closure_done_notice(self.sprint, [
                f"OVERSEER_CLOSURE_MISSING: correction {corrections + 1} of 2. The sprint outcome is already "
                "recorded, but your give_closing_words receipt is missing. Conversational closing words do not "
                "file memory. Read your own recent journal if needed, then call give_closing_words(text) with "
                "your own reflection. Correct only this missing filing action; do not repeat sprint_finish, "
                "staff closure or completed work. The Timer cannot author or file your reflection for you. "
                "Existing stop, cost and turn caps still apply."
            ]))
            self.log("OVERSEER_CLOSURE_CORRECTION", f"{corrections + 1} of 2; receipt missing")
            self._save()  # persist the allowance and pending wake before another turn can start
        return None

    # ---- taking finished turns in ------------------------------------------------------------------------------

    def _collect(self) -> None:
        self._finished = []
        for mind, turn in sorted(self.running.items()):
            if turn.future is None or not turn.future.done():
                continue
            try:
                result = turn.future.result()
            except Exception as exc:  # a runner that raised is a failed turn, never a dead Timer
                result = TurnResult(False, "", {}, f"{type(exc).__name__}: {exc}")
            if not isinstance(result, TurnResult):
                result = TurnResult(False, "", {}, "RunnerError: the runner returned no TurnResult")
            self._record(turn, result)
            self._account(result)
            del self.running[mind]
            self._finished.append(turn)
            meter.settle(turn.key)
            if result.ok and (result.cost or {}).get("stopped"):
                self._stopped_turn(turn)
            elif mind == "overseer":
                self._overseer_done(turn, result)
            else:
                self._staff_done(turn, result)
            self._save()
            (self.timer_dir/'active'/f'{turn.id:04d}.json').unlink(missing_ok=True)

    def _stopped_turn(self, turn: Turn) -> None:
        """A stop (STOP, or the cost cap) ended this turn between two model calls, never inside a tool call or a
        write. Nothing is judged: no re-prompt, no stale watch, no closure end. The wake waits, marked RESUMED, and is
        delivered again first when the Timer next runs (it is saved with the queue); the task stays as the mind left
        it."""
        wake = turn.wake
        if turn.mind == "overseer" and self._overseer_finished():
            self.log("STOPPED_MID_TURN", f"overseer {wake.kind}; the sprint is closed and its closing words are filed, "
                                         f"so nothing waits")
            return
        if turn.mind == "overseer":
            self.overseer_queue.insert(0, notices.resumed(wake))
        elif wake.kind == "TASK":
            t = self.sprint.tasker("operator").get(wake.task)
            if t.status == "Active" and t.assigned_to == turn.mind:      # a New task is dispatched as usual
                self.pending.insert(0, Pending(turn.mind, notices.resumed(
                    notices.task_wake(self.sprint, turn.mind, t.number))))
        else:
            self.pending.insert(0, Pending(turn.mind, notices.resumed(wake), question=turn.question))
        self.log("STOPPED_MID_TURN", f"{turn.mind} {wake.kind}" + (f" T{wake.task}" if wake.task else "") +
                 "; its wake waits for the next run")

    def _overseer_finished(self) -> bool:
        """The sprint is closed and the Overseer filed its closing words (t2: a stop landed in its last turn, after
        both). The receipt is the one aether.overseer.continuity.close writes: logs/tasker_closure/<sha256 of the
        sprint id, 24 hex>.json in the Overseer's home."""
        import hashlib
        if not str(self.sprint.record().get("state", "")).startswith("closed-"):
            return False
        key = hashlib.sha256(self.sprint.id.encode("utf-8")).hexdigest()[:24]
        path = (self.sprint.data_root / "minds" / self.sprint.overseer / "logs" / "tasker_closure"
                / f"{key}.json")
        try:
            receipt = read_json(path, {})
        except ValueError:
            return False
        return (isinstance(receipt, dict) and receipt.get("status") == "CLOSED"
                and receipt.get("source_ref") == self.sprint.id and receipt.get("closure_id") == key
                and bool(receipt.get("note_id")) and bool(receipt.get("text_sha256")))

    def _overseer_done(self, turn: Turn, result: TurnResult) -> None:
        if result.ok:
            self.state["overseer_attempt"], self.state["overseer_retry_at"] = 0, 0
            return
        if self._retry_due(turn, result):
            self.overseer_queue.insert(0, turn.wake)    # retried as soon as its delay passes (see _start_overseer)
            self.state["overseer_retry_at"] = self.clock() + self.retry_delays[turn.attempt]
            self.state["overseer_attempt"] = turn.attempt + 1
            return
        self.state["overseer_attempt"] = 0
        if result.error.startswith("SetupError"):
            self.state["held"]["overseer"] = result.error
            self.overseer_queue.insert(0, turn.wake)    # delivered again after: sprint resume
            self._operator(f"the Overseer cannot run: {result.error}. Fix it, then: python -m aether.tasker sprint resume")
            return
        self._operator(f"the Overseer's {turn.wake.kind} turn failed: {result.error}")

    def _staff_done(self, turn: Turn, result: TurnResult) -> None:
        kind, mind = turn.wake.kind, turn.mind
        if not result.ok:
            if result.error.startswith("SetupError"):
                self.state["held"][mind] = result.error
                self._operator(f"{mind} cannot run: {result.error}. Fix it, then: python -m aether.tasker sprint resume")
                if kind != "TASK":                      # a TASK is dispatched again from the table after resume
                    self.pending.append(Pending(mind, turn.wake, 0, 0.0, turn.question, turn.reprompt))
                return
            if self._retry_due(turn, result):
                self.pending.append(Pending(mind, turn.wake, turn.attempt + 1,
                                            self.clock() + self.retry_delays[turn.attempt], turn.question,
                                            turn.reprompt))
                self.log("RETRY", f"{mind} {kind} attempt {turn.attempt + 2} in {self.retry_delays[turn.attempt]:.0f} s: "
                                  f"{result.error}")
                return
            if kind == "CLOSURE":
                self._closure_turn_ended(mind, ok=False)
                return
            if turn.wake.task is not None:
                t = self.sprint.tasker("operator").get(turn.wake.task)
                self.state["failed"][str(t.number)] = t.modified_at
            self._to_overseer(notices.turn_failed_notice(self.sprint, mind, kind, result.error, turn.wake.task))
            self.log("TURN_FAILED", f"{mind} {kind} T{turn.wake.task}: {result.error}")
            return
        if kind == "TASK":
            t = self.sprint.tasker("operator").get(turn.wake.task)
            if t.status == "Active" and t.assigned_to == mind:
                if not turn.reprompt:
                    self.pending.append(Pending(mind, notices.task_wake(self.sprint, mind, t.number, reprompt=True),
                                                reprompt=True))
                    self.log("REPROMPT", f"{mind} T{t.number} still Active after its turn")
                else:
                    self.state["stale"][str(t.number)] = t.modified_at
                    self._to_overseer(notices.stale_notice(self.sprint, t))
                    self.log("STALE", f"{mind} T{t.number}")
        elif kind == "MESSAGE":
            self._to_overseer(notices.reply_notice(self.sprint, mind, turn.question, result.text))
            self.log("REPLY", f"{mind} answered the Overseer")
        elif kind == "CLOSURE":
            self._closure_turn_ended(mind, ok=True)

    def _retry_due(self, turn: Turn, result: TurnResult) -> bool:
        return (turn.attempt < len(self.retry_delays)
                and not any(result.error.startswith(k) for k in NOT_RETRIED))

    # ---- changes and requests ----------------------------------------------------------------------------------

    def _changes(self) -> None:
        tk = self.sprint.tasker("operator")
        rows = tk.changes_after(self.state["cursor_changes"])
        if not rows:
            return
        self.state["cursor_changes"] = rows[-1].id
        own = f"overseer:{self.sprint.overseer}"
        others = [c for c in rows if c.by != own]
        if rows:
            self.state["quiet_sent"] = False
        if others:
            self.pending_changes.extend(others)
            if "CHANGES" not in self.overseer_queue:
                self.overseer_queue.append("CHANGES")

    def _requests(self) -> None:
        items = requests_after(self.sprint, self.state["cursor_requests"])
        for item in items:
            self.state["cursor_requests"] = item["id"]
            if item["kind"] == "message":
                self.pending.append(Pending(item["agent"], notices.message_wake(self.sprint, item["agent"], item["text"]),
                                            question=item["text"]))
                self.log("MESSAGE", f"queued for {item['agent']}")
            elif item["kind"] == "closure" and not self.state["closure"]:
                staff = list(self.sprint.record().get("staff", []))
                self.state["closure"] = {"waiting": staff, "ended": {}, "done": False}
                for agent in staff:
                    name = staff_home.entry(self.sprint.data_root, agent).get("name")
                    self.pending.append(Pending(agent, notices.closure_wake(self.sprint, agent, name)))
                self.log("CLOSURE", f"closure turns queued for {', '.join(staff) or 'nobody'}")

    def _operator_lines(self) -> None:
        lines = self.sprint.record().get("operator_lines", [])
        for line in lines[self.state["operator_lines_seen"]:]:
            self.log("OPERATOR", (f"[stop: {line['stop']}] " if line.get("stop") else "") + line["text"])
        self.state["operator_lines_seen"] = len(lines)

    # ---- starting turns ----------------------------------------------------------------------------------------

    def caps(self) -> dict:
        return self.sprint.record().get("caps", {}) or {}

    def _caps_ok(self) -> bool:
        caps = self.caps()
        reached = None
        if caps.get("max_turns_per_sprint") is not None and self.state["turns_started"] >= int(caps["max_turns_per_sprint"]):
            reached = ("max_turns_per_sprint", self.state["turns_started"], caps["max_turns_per_sprint"])
        elif caps.get("max_cost_per_sprint") is not None and self._live_cost() >= float(caps["max_cost_per_sprint"]):
            reached = ("max_cost_per_sprint", round(self._live_cost(), 4), caps["max_cost_per_sprint"])
        if reached is None:
            if self.state.get("cap"):
                self.log("CAP_LIFTED", self.state["cap"])
                self.state["cap"] = None
                self.sprint.update(lambda d: d.update(cap_reached=None))
            return True
        if self.state.get("cap") != reached[0]:
            self.state["cap"] = reached[0]
            text = f"cap reached: {reached[0]} (used {reached[1]} of {reached[2]}); no new turns until it is raised"
            self._operator(text)
            self.extra_lines.append(notices.cap_line(*reached))
            self.sprint.update(lambda d: d.update(cap_reached=reached[0]))
        return False

    def _room(self) -> bool:
        return (not self._stopping.is_set() and not (self.timer_dir/'STOP').exists()
                and failure(self.sprint) is None and self._caps_ok()
                and len(self.running) < int(self.caps().get("max_parallel_turns") or 3))

    def _launch(self, mind: str, wake: Wake, attempt: int = 0, question: str = "", reprompt: bool = False) -> bool:
        if not self._room():return False
        self._ids += 1
        turn = Turn(self._ids, mind, wake, attempt, question, reprompt, now())
        self.running[mind] = turn
        self.state["turns_started"] += 1
        turn.key = (str(self.sprint.root), turn.id)
        write_json(self.timer_dir/'active'/f'{turn.id:04d}.json',dict(turn=turn.id,mind=mind,kind=wake.kind,task=wake.task))
        self._save()  # reserve the slot and record in-flight ownership before the worker can act
        stopped = self._turn_stopped
        if mind == "overseer":
            turn.future = self.pool.submit(_bound, turn.key, self.overseer_runner, self.sprint, wake, stopped=stopped)
        else:
            turn.future = self.pool.submit(_bound, turn.key, self.staff_runner, self.sprint, mind, wake,
                                           stopped=stopped)
        self.log("WAKE", f"{mind} {wake.kind}" + (f" T{wake.task}" if wake.task else "") +
                 (f" (attempt {attempt + 1})" if attempt else ""))
        return True

    def _to_overseer(self, wake: Wake) -> None:
        self.overseer_queue.append(wake)

    def _start_overseer(self) -> None:
        if "overseer" in self.running or "overseer" in self.state["held"] or not self.overseer_queue or not self._room():
            return
        if self.clock() < self.state.get("overseer_retry_at", 0):
            return
        item = self.overseer_queue.pop(0)
        if item == "CHANGES":
            item = notices.changes_notice(self.sprint, self.pending_changes,
                                          self.sprint.tasker("operator").counts())
            self.pending_changes = []
        if self.extra_lines:
            item = Wake(item.kind, item.text.replace("⟪END_TIMER_NOTICE⟫", "\n".join(self.extra_lines) +
                                                     "\n⟪END_TIMER_NOTICE⟫"), item.task)
            self.extra_lines = []
        if not self._launch("overseer", item, attempt=self.state.get("overseer_attempt", 0)):
            self.overseer_queue.insert(0,item)

    def _start_staff(self) -> None:
        record = self.sprint.record()
        if record.get("paused_for"):            # a stop (money, consent, secrets, harm): no staff turn until resume
            return
        dispatch = bool(record.get("dispatch"))
        now_ = self.clock()
        for p in sorted(self.pending, key=lambda p: (p.due, 0 if p.attempt else 1)):
            if not self._room():
                return
            if p.mind in self.running or p.mind in self.state["held"] or p.due > now_:
                continue
            if p.wake.kind == "TASK" and not dispatch:
                continue
            if not self._launch(p.mind, p.wake, p.attempt, p.question, p.reprompt):return
            self.pending.remove(p)
        if not dispatch or self.state["closure"]:
            return
        tk = self.sprint.tasker("operator")
        busy = {t.assigned_to for t in tk.list(status="Active")}
        for t in tk.ready():
            if not self._room():
                return
            agent = t.assigned_to
            if (agent in self.running or agent in busy or agent in self.state["held"]
                    or any(p.mind == agent for p in self.pending)
                    or self.state["failed"].get(str(t.number)) == t.modified_at):
                continue
            if not self._launch(agent, notices.task_wake(self.sprint, agent, t.number)):return
            busy.add(agent)
            self.state["quiet_sent"] = False

    # ---- quiet and closure -------------------------------------------------------------------------------------

    def _quiet(self) -> None:
        record = self.sprint.record()
        if not record.get("dispatch") or self.state["quiet_sent"] or self.state["closure"]:
            return
        if self.running or self.pending or self.overseer_queue or self.pending_changes:
            return
        tk = self.sprint.tasker("operator")
        if tk.list(status="Active"):
            return
        ready = [t for t in tk.ready() if self.state["failed"].get(str(t.number)) != t.modified_at
                 and t.assigned_to not in self.state["held"]]
        if ready and not record.get("paused_for") and self.state.get("cap") is None:
            return
        waiting = []
        for t in tk.list(status="New"):
            if t.assigned_to is None:
                why = "no assignee"
            elif self.state["failed"].get(str(t.number)) == t.modified_at:
                why = "its turn failed; change the task to dispatch it again"
            elif t.assigned_to in self.state["held"]:
                why = f"{t.assigned_to} cannot run (setup); the operator is told"
            else:
                open_deps = [d for d in t.depends_on if tk.get(d).status != "Closed"]
                why = ("waits on " + ", ".join(f"T{d}" for d in open_deps)) if open_deps else "paused or capped"
            waiting.append((t, why))
        self.state["quiet_sent"] = True
        self._to_overseer(notices.quiet_notice(self.sprint, tk.counts(), tk.list(status="Blocked"), waiting))
        self.log("QUIET", f"{len(waiting)} waiting, {len(tk.list(status='Blocked'))} blocked")

    def _closure_turn_ended(self, agent: str, ok: bool) -> None:
        closure = self.state["closure"]
        if closure is not None:
            closure["ended"][agent] = ok

    # ---- the change record ---------------------------------------------------------------------------------------
    # A record, not a fence: once a tick the Timer fingerprints what minds share (outputs/, briefcases/, the goals and
    # the overview) by size and modification time, and names any change that no running turn was allowed to make.
    # Programs a mind runs are not sandboxed (M7 rerun: a pytest run in another task's outputs rewrote its cache).

    WATCHED = ("outputs", "briefcases")
    WATCHED_FILES = ("SPRINT_GOALS.md", "SPRINT_OVERVIEW.md")

    def _fingerprint(self) -> dict:
        prints, root = {}, self.sprint.root
        for base in self.WATCHED:
            for folder, _dirs, files in os.walk(root / base):
                for name in files:
                    p = Path(folder) / name
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    prints[p.relative_to(root).as_posix()] = (st.st_size, st.st_mtime_ns)
        for name in self.WATCHED_FILES:
            try:
                st = (root / name).stat()
                prints[name] = (st.st_size, st.st_mtime_ns)
            except OSError:
                pass
        return prints

    def _allowed(self, turn: Turn, rel: str, solo: bool, active: dict) -> bool:
        if turn.mind == "overseer":
            return (rel == "SPRINT_OVERVIEW.md" or rel.startswith("briefcases/")
                    or (solo and rel.startswith("outputs/")))
        if turn.wake.kind == "CLOSURE":
            return rel.startswith(f"briefcases/{turn.mind}/")
        mine = set(active.get(turn.mind, ()))
        if turn.wake.task is not None:
            mine.add(turn.wake.task)
        return any(rel.startswith(f"outputs/T{n}/") for n in mine)

    def _watch(self) -> None:
        now_prints = self._fingerprint()
        before, self._prints = self._prints, now_prints
        if before is None:
            return
        changed = sorted(p for p in set(before) | set(now_prints) if before.get(p) != now_prints.get(p))
        if not changed:
            return
        turns = list(self.running.values()) + self._finished
        solo = not self.sprint.record().get("staff")
        active = {}
        for t in self.sprint.tasker("operator").list(status="Active"):
            active.setdefault(t.assigned_to, []).append(t.number)
        outside = [p for p in changed if not any(self._allowed(t, p, solo, active) for t in turns)]
        if not outside:
            return
        who = ", ".join(sorted({t.mind + (f" (T{t.wake.task})" if t.wake.task else "") for t in turns})) or "no mind"
        shown = ", ".join(outside[:8]) + (f" and {len(outside) - 8} more" if len(outside) > 8 else "")
        self.log("CHANGED_OUTSIDE", f"{shown}; running: {who}")
        self._operator(f"changed outside the folders the running minds may change: {shown} (running: {who})")
        self.extra_lines.append(notices.changed_outside_line(outside, who))

    def _closure_end(self) -> bool:
        closure = self.state["closure"]
        if not closure or closure["done"]:
            return False
        if set(closure["ended"]) < set(closure["waiting"]):
            return False
        lines = []
        for agent in closure["waiting"]:
            wb = staff_home.write_back(self.sprint, agent)
            if not wb.done:
                if staff_home.closed(self.sprint,agent):
                    stop(self.sprint,'staff_writeback_failed','; '.join(wb.lines),agent=agent)
                    return False
                corrections=closure.setdefault('corrections',{})
                used=corrections.get(agent,0)
                if used>=2:
                    stop(self.sprint,'staff_closure_missing','No own closure filing after two correction wakes.',agent=agent)
                    return False
                corrections[agent]=used+1
                closure['ended'].pop(agent,None)
                wake=notices.closure_wake(self.sprint,agent,staff_home.entry(self.sprint.data_root,agent).get('name'))
                self.pending.append(Pending(agent,Wake('CLOSURE',wake.text+'\nSTAFF_CLOSURE_MISSING: call identity_close with your own authored closure. Correction '+str(used+1)+' of 2. Do not repeat completed task work.')))
                self._save()
                return False
            lines += wb.lines
            for line in wb.lines:
                self.log("WRITE_BACK", line)
        if not staff_complete(self.sprint):
            stop(self.sprint,'staff_closure_inconsistent','Applied staff receipts do not match their Home identities.')
            return False
        closure["done"] = True
        self.sprint.update(lambda d: d.update(closure_done=True))
        self._to_overseer(notices.closure_done_notice(self.sprint, lines))
        self.log("CLOSURE_DONE", f"{len(closure['waiting'])} agent(s)")
        return True

    # ---- records -----------------------------------------------------------------------------------------------

    def _money(self, tin, tout) -> float:
        p = self._prices
        return (tin * float(p.get("input_per_million", 0)) + tout * float(p.get("output_per_million", 0))) / 1_000_000

    def _account(self, result: TurnResult) -> None:
        cost = result.cost or {}
        tin, tout = int(cost.get("prompt_tokens") or 0), int(cost.get("completion_tokens") or 0)
        self.state["tokens_used"] += tin + tout
        self.state["cost_used"] += self._money(tin, tout)

    def _live_cost(self) -> float:
        """Accounted cost plus what the turns in flight have spent so far, call by call."""
        tin, tout = meter.inflight([t.key for t in list(self.running.values())])
        return self.state["cost_used"] + self._money(tin, tout)

    def _turn_stopped(self) -> bool:
        """A turn's stop signal, read before each of its model calls: the STOP file, or the cost cap reached by the
        live count. A turn in flight can overshoot by at most the one call it is making."""
        if self._stopping.is_set():
            return True
        cap = self._cost_cap
        return cap is not None and self._live_cost() >= float(cap)

    def _record(self, turn: Turn, result: TurnResult) -> None:
        # Full conversations contain identities: retain them in the minds' homes, never the sprint folder.
        cost = deepcopy(result.cost) if isinstance(result.cost, dict) else {}
        refs = transcripts.save(self.sprint, turn.id, turn.mind, turn.wake.kind, cost)
        write_json(self.sprint.root / "turns" / f"{turn.id:04d}.json", {
            "turn": turn.id, "mind": turn.mind, "kind": turn.wake.kind, "task": turn.wake.task,
            "attempt": turn.attempt + 1, "started": turn.started, "ended": now(), "ok": result.ok,
            "error": result.error, "text": result.text, "cost": cost, "wake": turn.wake.text, "transcripts": refs})
        self.log("TURN_END", f"{turn.mind} {turn.wake.kind}" + (f" T{turn.wake.task}" if turn.wake.task else "") +
                 (" ok" if result.ok else f" failed: {result.error}"))

    def _operator(self, text: str) -> None:
        operator_line(self.sprint, text)
        self.state["operator_lines_seen"] += 1
        self.log("OPERATOR", text)

    def log(self, event: str, text: str) -> None:
        line = f"{now()} {event} {text}"
        self.timer_dir.mkdir(parents=True, exist_ok=True)
        with (self.timer_dir / "timer.log").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line + "\n")
        if self.echo:
            self.echo(line)

    def _save(self) -> None:
        write_json(self.timer_dir / "timer.json", dict(self.state, dispatch=bool(self.sprint.record().get("dispatch")),
                                                       running=sorted(self.running), pending=len(self.pending),
                                                       queue=self._queue()))


def _bound(key, runner, *args, **kwargs):
    """Run a turn in its worker thread with its live spend counted under key."""
    meter.begin(key)
    try:
        return runner(*args, **kwargs)
    finally:
        meter.leave()


def resume(sprint: Sprint) -> None:
    """The operator's answer to a stop or a setup failure: the pause is lifted, and the Timer clears its holds at
    its next tick (timer/RESUME). A cap is lifted by raising it in sprint.json (sprint caps)."""
    terminal = failure(sprint)
    if terminal:
        raise RuntimeError(str(terminal) + '; terminal failure cannot resume; preserve this run.')
    (sprint.root / "timer").mkdir(parents=True, exist_ok=True)
    (sprint.root / "timer" / "RESUME").write_bytes(b"")
    sprint.update(lambda d: d.update(paused_for=None))
