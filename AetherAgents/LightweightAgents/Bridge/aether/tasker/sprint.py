"""The Sprint Workspace Folder (contract §4) and the seam both lanes share (contract §7).

    <data root>/sprints/<sprint id>/
      SPRINT_GOALS.md  SPRINT_OVERVIEW.md  sprint.json  tasker.sqlite  knowledge.sqlite (optional)
      briefcases/<agent id>/   outputs/T<n>/   timer/   turns/
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from ._files import file_lock, now, read_json, write_json, write_text
from .tasker import Tasker

STATES = ("forming", "running", "closing", "closed-pass", "closed-fail")
# Michel, 10-30 night: every sprint gets a $5 runaway cap by default; `sprint caps` raises it.
DEFAULT_CAPS = {"max_parallel_turns": 3, "max_turns_per_sprint": 200, "max_cost_per_sprint": 5.0}
# A deliberately high DeepSeek estimate (US dollars per million tokens); the prompt cache makes real bills lower.
DEFAULT_PRICES = {"*": {"input_per_million": 0.30, "output_per_million": 1.20}}
OVERSEER_WAKES = ("HIRED", "CHANGES", "QUIET", "STALE", "TURN_FAILED", "REPLY", "CLOSURE_DONE")
STAFF_WAKES = ("TASK", "MESSAGE", "CLOSURE")


class SprintError(ValueError):
    """A sprint folder operation that cannot go ahead; the message says why."""


@dataclass
class Sprint:
    id: str
    root: Path
    data_root: Path
    overseer: str
    staff: list = field(default_factory=list)

    def tasker(self, actor: str) -> Tasker:
        return Tasker.open(self.root / "tasker.sqlite", actor)

    def briefcase(self, agent_id: str) -> Path:
        return self.root / "briefcases" / agent_id

    # helpers beyond the seam
    @property
    def json_path(self) -> Path:
        return self.root / "sprint.json"

    def outputs(self, number: int) -> Path:
        return self.root / "outputs" / f"T{int(number)}"

    def record(self) -> dict:
        return read_json(self.json_path, {}) or {}

    def update(self, change: Callable[[dict], None]) -> dict:
        """Change sprint.json under the sprint's lock; returns the record written."""
        with file_lock(self.root / "timer" / ".sprint.lock"):
            data = self.record()
            change(data)
            write_json(self.json_path, data)
        self.staff = list(data.get("staff", []))
        return data


@dataclass
class Wake:
    kind: str             # Overseer: HIRED | CHANGES | QUIET | STALE | TURN_FAILED | REPLY | CLOSURE_DONE
    text: str             # staff: TASK | MESSAGE | CLOSURE
    task: Optional[int] = None


@dataclass
class TurnResult:
    ok: bool
    text: str
    cost: dict
    error: str = ""


def slug(goals_text: str, words: int = 3) -> str:
    for line in (goals_text or "").splitlines():
        line = re.sub(r"^[#>*\-\s]+", "", line).strip()
        if line:
            parts = re.findall(r"[a-z0-9]+", line.lower())
            text = "-".join(parts[:words])[:24].strip("-")
            if text:
                return text
    return "sprint"


def new_sprint(data_root, goals_text: str, overseer: str, project: Optional[str] = None,
               caps: Optional[dict] = None, *, name: Optional[str] = None, day: Optional[str] = None,
               contracts: Optional[dict] = None) -> Sprint:
    """Create the folder, SPRINT_GOALS.md, sprint.json (state forming) and the Tasker."""
    data_root = Path(data_root).resolve()
    if contracts is not None:
        from .verification import validate
        contracts = validate(contracts)
    if not (goals_text or "").strip():
        raise SprintError("a sprint needs goals")
    if not (overseer or "").strip():
        raise SprintError("a sprint needs an Overseer id")
    unknown = sorted(set(caps or {}) - set(DEFAULT_CAPS))
    if unknown:
        raise SprintError(f"unknown cap {', '.join(unknown)}; the caps are {', '.join(DEFAULT_CAPS)}")
    day = day or datetime.now(timezone.utc).strftime("%Y%m%d")
    base = f"S-{day}-{slug(name or goals_text)}"
    sprints = data_root / "sprints"
    sprints.mkdir(parents=True, exist_ok=True)
    with file_lock(sprints / ".new.lock"):
        sid, letter = base, ord("b")
        while (sprints / sid).exists():
            sid = f"{base}-{chr(letter)}"
            letter += 1
        root = sprints / sid
        for sub in ("briefcases", "outputs", "timer", "turns"):
            (root / sub).mkdir(parents=True)
    goals = goals_text if goals_text.endswith("\n") else goals_text + "\n"
    write_text(root / "SPRINT_GOALS.md", goals)
    write_json(root / "sprint.json", {
        "id": sid,
        "created": now(),
        "project": project,
        "overseer": overseer,
        "staff": [],
        "data_root": str(data_root),
        "deployments": {},
        "caps": {**DEFAULT_CAPS, **(caps or {})},
        "prices": json.loads(json.dumps(DEFAULT_PRICES)),
        "state": "forming",
        "result": None,
        "operator_lines": [],
    })
    Tasker.create(root / "tasker.sqlite", sid, overseer)
    sprint = Sprint(sid, root, data_root, overseer, [])
    if contracts is not None:
        from .verification import configure
        configure(sprint, contracts)
    return sprint


def load_sprint(path) -> Sprint:
    root = Path(path).resolve()
    data = read_json(root / "sprint.json")
    if not data:
        raise SprintError(f"no sprint.json in {root}")
    return Sprint(data["id"], root, Path(data.get("data_root") or root.parent.parent), data["overseer"],
                  list(data.get("staff", [])))


def set_state(sprint: Sprint, state: str) -> None:
    if state not in STATES:
        raise SprintError(f"unknown state {state!r}; the states are {', '.join(STATES)}")
    sprint.update(lambda d: d.__setitem__("state", state))


def operator_line(sprint: Sprint, text: str, stop: Optional[str] = None) -> dict:
    """A line for the human operator in sprint.json (the Timer also logs it)."""
    line = {"at": now(), "text": text, "stop": stop, "answered": None}
    sprint.update(lambda d: d.setdefault("operator_lines", []).append(line))
    return line


# ---- requests to the Timer (timer/requests.jsonl) ------------------------------------------------------------------
# The Overseer's tools post here (message_agent, closure_send); the Timer reads them in order by their id.

def post_request(sprint: Sprint, kind: str, **payload) -> dict:
    path = sprint.root / "timer" / "requests.jsonl"
    with file_lock(sprint.root / "timer" / ".requests.lock"):
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        item = {"id": len(lines) + 1, "at": now(), "kind": kind, **payload}
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(item, ensure_ascii=False) + "\n")
    return item


def requests_after(sprint: Sprint, cursor: int = 0) -> list:
    path = sprint.root / "timer" / "requests.jsonl"
    if not path.exists():
        return []
    with file_lock(sprint.root / "timer" / ".requests.lock"):
        lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines[int(cursor):] if line.strip()]
