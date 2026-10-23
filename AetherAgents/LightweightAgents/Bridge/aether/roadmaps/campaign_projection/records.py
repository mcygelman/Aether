"""Campaign records box: gather one campaign's durable records, READ-ONLY, into the plain structure
state.campaign_state projects.

Inputs:  root, the Mission Control registry: the folder holding projects.json, or that file itself;
         campaign_id, or None for the selected (active) campaign; live_runs, run IDs the caller
         knows are in flight in its own process (nothing durable says so).
Outputs: load_campaign_records(...) -> dict (shape documented in state.py), including every file
         read, with its sha256, under "sources".
Never:   writes, creates a folder, takes a lock, follows a link or junction (every path is checked,
         component by component, before it is listed or read: the registry folder, the campaign
         root and the coordinator and attempt directories a record names from the drive root down
         (_Reader.anchor), and every folder and file below them (_Reader.entry); a linked one, at
         any depth, is refused and listed as a problem by its own name, its target never read),
         reads outside those folders, or fails on a missing store (a missing store means that
         stage has not started). A record it cannot read or parse is listed under "problems" and
         shows as UNRECOGNIZED, never skipped. Raises only CampaignNotFound (no such campaign) and
         CampaignRegistryUnavailable (the registry itself cannot be read now).

What it reads under <campaign root>/.aether/ (the stores' own layouts, as of 2026-10-09):
  planning/proposals/NNNNNN-{launch,started,result}.json   planning_generation (inspect_launch rule)
  staffing/NNNNNN-staffing.json                             staffing_workflow
  execution/<run>/activation.json, NNNNNN-{event,control}.json   staff_execution
  closure-packets/<sha256>.json                             closure_packets, native_closure_packets
  closure-dialogue/NNNNNN-response.json                     native_closure_dialogue
  closure-dialogue/operator-decisions/NNNNNN.json           native_closure_review
 Native host stores (pass 2), "host_runs": one row per run folder, the run's own result status,
 or, for a run with an intent and no result, the literal its own host reports for that state:
  planning/minder-qualification/supervision/<rid>/{00-intent,01-retirement}.json   minder_supervision
  planning/minder-assessment/<rid>/{00-intent,02-assessment}.json                  minder_assessment
  planning/minder/native-reviews/<rid>/{00-intent,01-result}.json                  reviewer_supervision
  planning/minder/native-staffing-runs/<rid>/{00-intent,01-result}.json            staffing_supervision
  planning/minder/native-staff-contributions/<rid>/{00-acquisition,02-result}.json native_staff_contribution
  planning/minder/native-closure-runs/<rid>/{00-intent,01-result}.json             closure_supervision
  planning/minder/native-artifact-effects/<slot>/{00-reservation,99-publication}.json native_contribution_effects
  Host attempt folders (minder host, reviewer host, desktop staffing host, contribution host, closure
  host): each host binds its supervised run to its own request ID, so an attempt whose run folder
  exists adds nothing (the run carries the outcome); one with a terminal record (04-normal-proof,
  01-captured) yields that record's literal; one with neither yields the host's own literal for an
  attempt without a recorded outcome (UNRESOLVED, HELD_OR_UNRESOLVED, UNRESOLVED_NO_REPLAY, HELD).
 Versioned native histories, "host_histories" (oldest first per store):
  planning/minder/native-staffing-history/NNNNNN-history.json    native_staffing_history
  planning/minder/native-staffing-decisions/NNNNNN-decision.json native_staffing_decisions
  planning/minder/native-task-decisions/NNNNNN-decision.json     native_task_completion
 Outside the campaign root, located from the campaign's own records (never guessed):
  generation attempts, "attempts": <coordinator>/runtime-attempts.sqlite3, opened read-only
    (mode=ro URI), for every (coordinator directory, generation_id) a planning start record or a
    supervised run intent names (coordinator.directory / owner.directory). The latest event of that
    generation counts; its source is "<ledger>#revision=N" with the sha256 of that event's bytes.
  interruption obligations, "closure_obligations": closure-obligation.json / closure-outcome.json
    (runtime/review_closure/closure_obligation.py) in <attempt_directory>/control, the failure
    controller of every host attempt a campaign record names (planning host-acquired, minder host
    01-acquired, reviewer host 02-acquired).
  planning/minder/native-staff-admission/NNNNNN-admission.json  native_staff_admission
  planning/minder/native-task-budgets/<slot>/budget.sqlite3 (read-only): TASK_BUDGET_ACTIVATED when
    the operator's activation is its only event, else TASK_BUDGET_CONSUMED (spend belongs to G5)
  overseer/runs/<rid>/{intent,outcome}.json, overseer/plan-changes/NNNNNN-ruling.json,
    overseer/successor-bindings/<uuid>.json, "amendments": the conversation's completed Propose candidates,
    the plan-change rulings and the successor rebinds (overseer_plan_changes, amendment_successor; Fable, 10-12)
Not read (gaps, documented): the
closure-obligation records of deployment-package controllers (foreground_generation and
reviewed_generation; no campaign record names their controller directory); the desktop staffing
host's generation (its 02-acquired names no coordinator directory); the board.
This loader does not re-validate the stores' integrity chains; the stores' own readers do that.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import stat
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional

from aether.roadmaps.project_engagement_contract import digest
from aether.roadmaps.project_setup import checked_path

MAX_RECORD_BYTES = 16 * 1024 * 1024
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_ENTRIES = 4096                     # per folder listed
MAX_COORDINATOR_EVENTS = 100_000
GENERATION_SCHEMA = "runtime-generation-v1-experimental"   # generation_attempts.SCHEMA
COORDINATOR_LEDGER = "runtime-attempts.sqlite3"             # runtime_attempts.RuntimeAttempts.path
_NUMBERED = re.compile(r"^(\d{6})-(launch|started|result)\.json$")
_REPARSE_POINT = 0x400

# Supervised runs: (store, folder under .aether, intent file, result file, status path in the result,
# the literal the run's own host reports for an intent without a result).
RUN_STORES = (
    ("minder-supervision", "planning/minder-qualification/supervision", "00-intent.json", "01-retirement.json",
     ("status",), "UNRESOLVED"),
    ("minder-assessment", "planning/minder-assessment", "00-intent.json", "02-assessment.json",
     ("outcome", "status"), "UNRESOLVED"),
    ("native-reviews", "planning/minder/native-reviews", "00-intent.json", "01-result.json", ("status",), "UNRESOLVED"),
    ("native-staffing-runs", "planning/minder/native-staffing-runs", "00-intent.json", "01-result.json",
     ("status",), "UNRESOLVED"),
    ("native-staff-contributions", "planning/minder/native-staff-contributions", "00-acquisition.json",
     "02-result.json", ("status",), "ACQUIRED_UNRESOLVED_NO_REPLAY"),
    ("native-closure-runs", "planning/minder/native-closure-runs", "00-intent.json", "01-result.json",
     ("status",), "UNRESOLVED"),
)
# Host attempts: (store, folder, the run folder it launches, the host's literal for an attempt without
# a recorded outcome, terminal record and its literal, acquisition record naming the attempt directory).
HOST_STORES = (
    ("minder-host-attempts", "planning/minder-qualification/host-attempts", "planning/minder-qualification/supervision",
     "UNRESOLVED", None, None, "01-acquired.json"),
    ("native-host-attempts", "planning/minder/native-host-attempts", "planning/minder/native-reviews",
     "UNRESOLVED", None, None, "02-acquired.json"),
    ("desktop-staffing-host", "planning/minder/desktop-staffing-host", "planning/minder/native-staffing-runs",
     "HELD_OR_UNRESOLVED", None, None, None),
    ("native-contribution-host", "planning/minder/native-contribution-host", "planning/minder/native-staff-contributions",
     "UNRESOLVED_NO_REPLAY", "04-normal-proof.json", "AETHER_NORMAL_COMPLETED", None),
    ("closure-host-attempts", "closure-dialogue/host-attempts", "planning/minder/native-closure-runs",
     "HELD", "01-captured.json", "CAPTURED_HELD", None),
)
# Versioned histories: (store, folder, file suffix, status paths tried in order, request-id path).
HISTORY_STORES = (
    ("native-staffing-history", "planning/minder/native-staffing-history", "-history.json",
     (("assessment", "status"), ("assessment", "integrity")), None),
    ("native-staffing-decisions", "planning/minder/native-staffing-decisions", "-decision.json",
     (("result", "status"),), None),
    # The operator's staff admission (native_staff_admission): ADMIT completes Staff (lane F, 10-10).
    ("native-staff-admission", "planning/minder/native-staff-admission", "-admission.json",
     (("confirmation", "proposal", "decision"),), None),
    ("native-task-decisions", "planning/minder/native-task-decisions", "-decision.json",
     (("confirmation", "proposal", "decision"),),
     ("confirmation", "proposal", "publication", "proposal", "generation", "request_id")),
)
# Task budget activations (native_task_budget): <slot>/budget.sqlite3, an event ledger without a status
# literal. Only the operator's activation and whether any spend followed it are projected: the
# activation alone is Authorize (authorized, launch the run); a ledger with later events is consumed
# authority whose run the contribution host reports (lane F, 10-10). Spend amounts belong to G5.
TASK_BUDGETS = "planning/minder/native-task-budgets"
TASK_BUDGET_LEDGER = "budget.sqlite3"
MAX_TASK_BUDGET_EVENTS = 4096
# Overseer conversation stores (Praxis, 10-12; projected here by Fable): completed Propose turns (candidates), the
# sibling plan-change rulings and the operator's successor staffing rebinds, under <campaign root>/.aether/overseer/.
OVERSEER_RUNS = "overseer/runs"
OVERSEER_RULINGS = "overseer/plan-changes"
OVERSEER_BINDINGS = "overseer/successor-bindings"


class CampaignNotFound(KeyError):
    """No campaign with that ID (or none selected) in this registry."""


class CampaignRegistryUnavailable(RuntimeError):
    """The campaign registry cannot be read now: missing, unreadable, being replaced, or malformed."""


def _dig(value, *keys):
    """value[k1][k2]..., or None when any level is missing or not an object (optional fields only)."""
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _linked(info) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


LINK_REFUSED = "linked folder or file refused (links and junctions are never followed; its target was not read)"
MISSING = "missing"


def _unique_pairs(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate JSON key {key!r}")
        seen[key] = value
    return seen


def _refuse_constant(name):
    raise ValueError(f"non-finite JSON number {name}")


def strict_loads(text):
    """The product's own stores refuse duplicate keys and NaN/Infinity; the projection must not read a
    record the product would reject (acceptance-checker review P2-1, 10-11). A refused record is a
    listed problem, never a signal of what its last duplicate said."""
    return json.loads(text, object_pairs_hook=_unique_pairs, parse_constant=_refuse_constant)


class _Reader:
    """Every path is checked before it is listed or read: an anchor (the registry folder, the campaign
    root, a coordinator or attempt directory a record names) from the drive root down, and every
    component between an anchor and the entry. A link or junction anywhere is refused and named,
    never followed; nothing outside an anchor is read."""

    def __init__(self):
        self.sources, self.problems = [], []
        self.documents = {}              # transient receipt input, never persisted
        self._anchors: list = []          # absolute folders checked from the drive root down
        self._folders: dict = {}          # component below an anchor -> True when a real folder
        self._refused: set = set()

    def problem(self, source, error):
        self.problems.append(dict(source=str(source), error=error))

    def guard(self, source, default, fn, *args):
        """fn(*args); any failure is a listed problem, never a failed request."""
        try:
            return fn(*args)
        except Exception as exc:      # noqa: BLE001 - a malformed record must be listed, not crash the page
            self.problem(source, f"{type(exc).__name__}: {exc}")
            return default

    def refuse(self, link: Path):
        """List a link or junction once, by its own name; its target is never read."""
        if str(link) not in self._refused:
            self._refused.add(str(link))
            self.problem(link, LINK_REFUSED)

    def anchor(self, path, source) -> Optional[Path]:
        """An absolute folder whose every existing component, from the drive root down, is a real
        folder (roadmaps.project_setup.checked_path, which also resolves it); None and a problem
        naming the first link or junction otherwise. A missing tail is allowed (not started yet)."""
        try:
            path = Path(path)
            if not path.is_absolute():
                raise ValueError(f"not an absolute folder: {str(path)!r}")
        except (TypeError, ValueError) as exc:
            self.problem(source, f"folder refused: {exc}")
            return None
        for part in reversed((path, *path.parents)):     # top-down: the first link is the one named
            try:
                info = part.lstat()
            except (FileNotFoundError, NotADirectoryError):
                break                                    # nothing below a missing component exists
            except OSError as exc:
                self.problem(part, f"{type(exc).__name__}: {exc}")
                return None
            if _linked(info):
                self.refuse(part)
                return None
        try:
            checked = checked_path(path)
        except (ValueError, OSError) as exc:             # changed since the walk above
            self.problem(source, f"folder refused: {exc}")
            return None
        self._anchors.append(checked)
        return checked

    def _folder(self, folder: Path) -> Optional[str]:
        """None when folder (below an anchor) is a real folder, else why not; checked once per load.
        A link, junction, non-folder or unreadable one is also listed as a problem (once)."""
        if folder not in self._folders:
            why = None
            try:
                info = folder.lstat()
            except (FileNotFoundError, NotADirectoryError):
                why = MISSING
            except OSError as exc:
                why = f"{type(exc).__name__}: {exc}"
                self.problem(folder, why)
            else:
                if _linked(info):
                    why = LINK_REFUSED
                    self.refuse(folder)
                elif not stat.S_ISDIR(info.st_mode):
                    why = "not a folder"
                    self.problem(folder, why)
            self._folders[folder] = why
        return self._folders[folder]

    def entry(self, path: Path) -> tuple:
        """(the entry's own metadata, None), or (None, why not), after every folder between its
        anchor and it is checked. why is MISSING when it or a folder above is absent; a link,
        junction, unreadable entry or a path outside every anchor is also listed as a problem."""
        anchor = max((a for a in self._anchors if path == a or a in path.parents),
                     key=lambda a: len(a.parts), default=None)
        if anchor is None:
            why = "path outside the campaign's checked folders refused"
            self.problem(path, why)
            return None, why
        folder = anchor
        for part in path.relative_to(anchor).parts[:-1]:
            folder = folder / part
            why = self._folder(folder)
            if why is not None:
                return None, why
        try:
            info = path.lstat()
        except (FileNotFoundError, NotADirectoryError):
            return None, MISSING
        except OSError as exc:
            why = f"{type(exc).__name__}: {exc}"
            self.problem(path, why)
            return None, why
        if _linked(info):
            self.refuse(path)
            return None, LINK_REFUSED
        return info, None

    def lstat(self, path: Path, *, missing_ok=True):
        """The entry's own metadata, or None: missing (a problem when missing_ok is False), or a
        link, junction or unreadable entry at or above it (always a problem)."""
        info, why = self.entry(path)
        if why == MISSING and not missing_ok:
            self.problem(path, "record missing")
        return info

    def is_dir(self, path: Path) -> bool:
        info = self.lstat(path)
        return info is not None and stat.S_ISDIR(info.st_mode)

    def listing(self, folder: Path, kind: str) -> list:
        """Sorted direct entries of a checked folder: 'dir' or 'file', each checked (links refused)."""
        if not self.is_dir(folder):
            return []
        try:
            entries = sorted(folder.iterdir())
        except OSError as exc:
            self.problem(folder, f"{type(exc).__name__}: {exc}")
            return []
        if len(entries) > MAX_ENTRIES:
            self.problem(folder, f"more than {MAX_ENTRIES} entries; only the first {MAX_ENTRIES} were read")
            entries = entries[:MAX_ENTRIES]
        wanted = stat.S_ISDIR if kind == "dir" else stat.S_ISREG
        result = []
        for entry in entries:
            info = self.lstat(entry)
            if info is not None and wanted(info.st_mode):
                result.append(entry)
        return result

    def json(self, path: Path) -> Optional[dict]:
        info = self.lstat(path, missing_ok=False)
        if info is None:
            return None
        try:
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("record is not a regular file")
            if info.st_size > MAX_RECORD_BYTES:
                raise ValueError(f"record larger than {MAX_RECORD_BYTES} bytes")
            raw = path.read_bytes()
            if len(raw) > MAX_RECORD_BYTES:
                raise ValueError(f"record larger than {MAX_RECORD_BYTES} bytes")
            value = strict_loads(raw.decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            self.problem(path, f"{type(exc).__name__}: {exc}")
            return None
        self.sources.append(dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest()))
        if not isinstance(value, dict):
            self.problem(path, "record is not a JSON object")
            return None
        self.documents[str(path)] = value
        return value

    def field(self, path: Path, value, *keys):
        """value[k1][k2]..., or None with a problem naming the missing field."""
        for key in keys:
            if not isinstance(value, dict) or key not in value:
                self.problem(path, "missing field " + ".".join(keys))
                return None
            value = value[key]
        return value


def _registry_file(root) -> Path:
    path = Path(root)
    return path if path.suffix == ".json" else path / "projects.json"


def _under(home: Path, folder: str) -> Path:
    return home.joinpath(*folder.split("/"))


def _framing(read: _Reader, source: str, framing) -> Optional[dict]:
    if framing is None:
        return None
    if not isinstance(framing, dict):
        read.problem(source, "framing is not a JSON object")
        return None
    approvals = framing.get("approvals") if isinstance(framing.get("approvals"), list) else []
    last = approvals[-1] if approvals and isinstance(approvals[-1], dict) else {}
    return dict(state=framing.get("state"), last_action=last.get("action"), last_decision=last.get("decision"),
                source=source)


def _planning(read: _Reader, home: Path) -> list:
    versions: dict = {}
    for path in read.listing(home / "planning" / "proposals", "file"):
        match = _NUMBERED.match(path.name)
        if match:
            versions.setdefault(int(match.group(1)), {})[match.group(2)] = path
    rows = []
    for version, phases in sorted(versions.items()):
        if "result" in phases:           # planning_generation.inspect_launch: result, else started, else launch
            record = read.json(phases["result"])
            status = None if record is None else read.field(phases["result"], record, "status")
            if record is None or status is None:
                continue
            rows.append(dict(version=version, status=status, source=str(phases["result"])))
        elif "started" in phases:
            read.json(phases["started"])
            rows.append(dict(version=version, status="UNRESOLVED", source=str(phases["started"])))
        elif "launch" in phases:
            read.json(phases["launch"])
            rows.append(dict(version=version, status="AUTHORIZED", source=str(phases["launch"])))
    return rows


def _staffing_row(read: _Reader, path: Path) -> Optional[dict]:
    record = read.json(path)
    result = None if record is None else read.field(path, record, "result")
    if record is None or result is None:
        return None
    if not isinstance(result, dict):
        read.problem(path, "staffing result is not a JSON object")
        return None
    status = result.get("status", result.get("integrity"))
    if status is None:
        read.problem(path, "staffing result names no status or integrity")
        return None
    return dict(status=status, operation=_dig(record, "confirmation", "proposal", "operation"), source=str(path))


def _staffing(read: _Reader, home: Path) -> list:
    rows = []
    for path in read.listing(home / "staffing", "file"):
        if path.name.endswith("-staffing.json"):
            row = read.guard(path, None, _staffing_row, read, path)
            if row is not None:
                rows.append(row)
    return rows


def _artifact_issue(read: _Reader, campaign_root: Path, name, ref) -> Optional[str]:
    """Why a completed receipt's file is missing or differs now, or None (read-only, streamed, no links)."""
    if not isinstance(name, str):
        return "receipt names no file path"
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name or not relative.parts:
        return "receipt names a path outside the campaign"
    path = campaign_root.joinpath(*relative.parts)
    info, why = read.entry(path)           # every folder from the campaign root down, then the file
    if info is None:
        return "the receipt's file is missing now" if why == MISSING else why
    if not stat.S_ISREG(info.st_mode):
        return "receipt names something that is not a file"
    if info.st_size > MAX_ARTIFACT_BYTES:
        return f"artifact larger than {MAX_ARTIFACT_BYTES} bytes; not hashed"
    sha = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                sha.update(chunk)
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    if not isinstance(ref, dict) or sha.hexdigest() != ref.get("sha256"):
        return "current file differs from the completed receipt"
    return None


def _execution_run(read: _Reader, run_dir: Path, campaign_root: Path, live_runs) -> Optional[dict]:
    activation_path = run_dir / "activation.json"
    if read.lstat(activation_path) is None:
        return None                      # a folder without an activation is not a run yet
    activation = read.json(activation_path)
    packets = read.field(activation_path, activation, "confirmation", "proposal", "packets") if activation else None
    events, controls = [], []
    files = read.listing(run_dir, "file")
    for suffix, rows in (("event", events), ("control", controls)):
        for path in files:
            if not path.name.endswith(f"-{suffix}.json"):
                continue
            row = read.json(path)
            payload = None if row is None else read.field(path, row, "payload")
            if isinstance(payload, dict):
                rows.append(payload)
            elif row is not None and payload is not None:
                read.problem(path, "payload is not a JSON object")
    artifacts = {}
    for event in events:
        if event.get("kind") == "RESULT" and event.get("status") == "COMPLETE" and isinstance(event.get("artifacts"), dict):
            artifacts.update(event["artifacts"])
    issues = {}
    for name, ref in artifacts.items():
        issue = _artifact_issue(read, campaign_root, name, ref)
        if issue is not None:
            issues[str(name)] = issue
    return dict(run_id=run_dir.name, packets=len(packets) if isinstance(packets, list) else 0,
                events=events, controls=controls, live=run_dir.name in live_runs,
                artifact_issues=issues, source=str(run_dir))


def _execution(read: _Reader, home: Path, campaign_root: Path, live_runs) -> list:
    runs = []
    for run_dir in read.listing(home / "execution", "dir"):
        run = read.guard(run_dir, None, _execution_run, read, run_dir, campaign_root, live_runs)
        if run is not None:
            runs.append(run)
    return runs


def _closure(read: _Reader, home: Path):
    packets = []
    for path in read.listing(home / "closure-packets", "file"):
        if path.suffix == ".json":
            record = read.json(path)
            status = None if record is None else read.field(path, record, "content", "status")
            if status is not None:
                packets.append(dict(status=status, packet_id=_packet_id(read, path, record), source=str(path)))
    dialogue = []
    for path in read.listing(home / "closure-dialogue", "file"):
        if path.name.endswith("-response.json"):
            record = read.json(path)
            status = None if record is None else read.field(path, record, "supervision", "status")
            if status is not None:
                selection = _dig(record, "preparation", "proposal", "selection")
                dialogue.append(dict(status=status, integrity=_dig(record, "assessment", "integrity"),
                                     record_digest=read.guard(path, None, digest, record),
                                     operation=_dig(selection, "operation"), packet_id=_dig(selection, "packet_id"),
                                     presentation_sha256=_dig(selection, "history_binding", "presentation_sha256"),
                                     source=str(path)))
    decisions = []
    for path in read.listing(home / "closure-dialogue" / "operator-decisions", "file"):
        if path.suffix == ".json":
            record = read.json(path)
            decision = None if record is None else read.field(path, record, "confirmation", "proposal", "decision")
            if decision is not None:
                proposal = _dig(record, "confirmation", "proposal")
                refs = {k: proposal.get(k) for k in ("packet_id", "presentation_sha256", "review_sha256")}
                decisions.append(dict(decision=decision, binds=[refs[k] for k in ("presentation_sha256", "review_sha256")
                                                                if isinstance(refs[k], str)],
                                      **refs, source=str(path)))
    return packets, dialogue, decisions


def _packet_id(read: _Reader, path: Path, record: dict) -> Optional[str]:
    """The packet's ID when it binds (named <ID>.json, the envelope names that ID, and the ID is the
    digest of its content, as closure_packets.read checks); None otherwise. Read-only, no repair."""
    if path.stem != record.get("packet_id") or read.guard(path, None, digest, record.get("content")) != path.stem:
        return None
    return path.stem


def _owner_ref(store: str, record, source: Path, refs: list):
    """A (coordinator directory, generation) pair named by a record: owner.* or coordinator.directory."""
    owner = _dig(record, "owner")
    directory, generation = _dig(owner, "directory"), _dig(owner, "generation_id")
    if directory is None:
        directory, generation = _dig(record, "coordinator", "directory"), _dig(record, "generation_id")
    if isinstance(directory, str) and isinstance(generation, str):
        refs.append(dict(store=store, directory=directory, generation_id=generation, source=str(source)))


def _run_folder(read, store, run_dir, intent_name, result_name, status_keys, unresolved, refs) -> Optional[dict]:
    names = {p.name: p for p in read.listing(run_dir, "file")}
    if intent_name in names:
        _owner_ref(store, read.json(names[intent_name]), names[intent_name], refs)
    if result_name in names:
        record = read.json(names[result_name])
        status = None if record is None else read.field(names[result_name], record, *status_keys)
        if status is None:
            return None
        return dict(store=store, run_id=run_dir.name, status=status, source=str(names[result_name]))
    if intent_name in names:
        return dict(store=store, run_id=run_dir.name, status=unresolved, source=str(names[intent_name]))
    return None                          # a folder without an intent is not a run yet


def _host_folder(read, home, store, attempt, run_folder, unresolved, done_name, done_literal, acquired_name,
                 attempt_dirs) -> Optional[dict]:
    names = {p.name: p for p in read.listing(attempt, "file")}
    if "00-intent.json" not in names:
        return None
    if acquired_name in names:
        acquired = read.json(names[acquired_name])
        directory = _dig(acquired, "attempt_directory")
        if isinstance(directory, str):
            attempt_dirs.append(dict(store=store, directory=directory, source=str(names[acquired_name])))
    if done_name in names:
        if read.json(names[done_name]) is None:
            return None
        return dict(store=store, run_id=attempt.name, status=done_literal, source=str(names[done_name]))
    read.json(names["00-intent.json"])
    if read.is_dir(_under(home, run_folder) / attempt.name):
        return None                      # the supervised run carries the outcome
    return dict(store=store, run_id=attempt.name, status=unresolved, source=str(names["00-intent.json"]))


def _effects_folder(read, slot: Path) -> Optional[dict]:
    names = {p.name: p for p in read.listing(slot, "file")}
    if "99-publication.json" in names:
        record = read.json(names["99-publication.json"])
        if record is None:
            return None
        return dict(store="native-artifact-effects", run_id=slot.name, status="PUBLICATION_COMPLETE",
                    request_id=_dig(record, "generation_request_id"), source=str(names["99-publication.json"]))
    if "00-reservation.json" in names:   # native_contribution_effects.inspect: no receipt is RECONCILIATION_REQUIRED
        record = read.json(names["00-reservation.json"])
        if record is None:
            return None
        return dict(store="native-artifact-effects", run_id=slot.name, status="RECONCILIATION_REQUIRED",
                    request_id=_dig(record, "confirmation", "proposal", "generation", "request_id"),
                    source=str(names["00-reservation.json"]))
    return None


def _host_runs(read: _Reader, home: Path, refs: list, attempt_dirs: list) -> list:
    rows = []
    for store, folder, intent_name, result_name, status_keys, unresolved in RUN_STORES:
        for run_dir in read.listing(_under(home, folder), "dir"):
            row = read.guard(run_dir, None, _run_folder, read, store, run_dir, intent_name, result_name,
                             status_keys, unresolved, refs)
            if row is not None:
                rows.append(row)
    for store, folder, run_folder, unresolved, done_name, done_literal, acquired_name in HOST_STORES:
        for attempt in read.listing(_under(home, folder), "dir"):
            row = read.guard(attempt, None, _host_folder, read, home, store, attempt, run_folder, unresolved,
                             done_name, done_literal, acquired_name, attempt_dirs)
            if row is not None:
                rows.append(row)
    for slot in read.listing(_under(home, "planning/minder/native-artifact-effects"), "dir"):
        row = read.guard(slot, None, _effects_folder, read, slot)
        if row is not None:
            rows.append(row)
    return rows


def _record_digest(record) -> Optional[str]:
    """digest(record) as the stores compute it, or None when the parsed record has no canonical form."""
    try:
        return digest(record)
    except (TypeError, ValueError):
        return None


def _history_row(read, store, path, status_paths, request_path) -> Optional[dict]:
    record = read.json(path)
    if record is None:
        return None
    status = next((s for s in (_dig(record, *keys) for keys in status_paths) if s is not None), None)
    if status is None:
        read.problem(path, f"{store} record names no status at " + " or ".join(".".join(k) for k in status_paths))
        return None
    request_id = _dig(record, *request_path) if request_path else None
    row = dict(store=store, status=status, request_id=request_id, source=str(path))
    # The staffing revision link (lane F, 10-11): a decision's version, and the operator revision a later
    # staffing record pins, so the projection can tell an answered REQUEST_REVISION from a pending one.
    # Praxis F2 (10-11): the decision's record digest (what a revision_ref's sha256 names: digest of the whole
    # record) and the history head it was taken on, and the history record's own version, so the projection
    # can require an exact reference from a LATER staffing record. Nothing here is verified; state.py decides.
    if store == "native-staffing-decisions":
        row["version"] = _dig(record, "confirmation", "proposal", "version")
        row["record_sha256"] = _record_digest(record)
        row["history_head_version"] = _dig(record, "confirmation", "proposal", "history_head", "version")
    elif store == "native-staffing-history":
        row["revision_ref"] = _dig(record, "preparation", "proposal", "selection", "revision_ref")
        row["version"] = _dig(record, "version")
    elif store == "native-staff-admission":   # 1.2.0: the approved amendment this admission binds (Fable, 10-12)
        row["amendment_approved_sha256"] = _dig(record, "confirmation", "proposal", "amendment", "approved_sha256")
        row["sprint_id"] = _dig(record, "confirmation", "proposal", "sprint_id")
        row["version"] = _dig(record, "confirmation", "proposal", "version")
        row["record_sha256"] = _record_digest(record)
        row["staffing_decision_sha256"] = _dig(record, "confirmation", "proposal", "staffing_decision_sha256")
        amendment = _dig(record, "confirmation", "proposal", "amendment")
        row["amendment"] = amendment if isinstance(amendment, dict) else None
    if store == "native-staffing-decisions":   # the approved document's deliverables per Sprint (supersession, 10-12)
        assignments = _dig(record, "result", "document", "assignments")
        found = {}
        for role in assignments if isinstance(assignments, list) else ():
            if isinstance(role, dict) and isinstance(role.get("deliverables"), list):
                found.setdefault(str(role.get("sprint_id")), []).extend(str(d) for d in role["deliverables"])
        row["deliverables"] = found
        row["tasks"] = [dict(task_id=r.get("task_id"), sprint_id=r.get("sprint_id"), deliverables=r.get("deliverables"))
                        for r in assignments if isinstance(r, dict)] if isinstance(assignments, list) else []
    elif store == "native-task-decisions":
        artifacts = _dig(record, "confirmation", "proposal", "publication", "proposal", "artifacts")
        row["artifacts"] = sorted(artifacts) if isinstance(artifacts, dict) else []
        prepared = _dig(record, "confirmation", "proposal", "publication", "proposal",
                        "generation", "proposal", "preparation", "proposal")
        row["version"] = _dig(record, "confirmation", "proposal", "version")
        row["admission_sha256"] = _dig(prepared, "selection", "admission_sha256")
        row["task_id"] = _dig(prepared, "selection", "task_id")
        row["staffing_decision_sha256"] = _dig(prepared, "task", "staffing_decision_sha256")
    return row


def _approval_digest(record):
    """The digest of the effective approval an APPROVE plan-change ruling yields, as PlanChanges._approval derives it
    (what a successor rebind's snapshot names); None for any other record."""
    if _dig(record, "confirmation", "proposal", "selection", "decision") != "APPROVE":
        return None
    try:
        from aether.roadmaps.plan_ruling_contract import plan_change_approval
        return digest(plan_change_approval(record))
    except (KeyError, TypeError, ValueError):
        return None


def _amendments(read: _Reader, home: Path) -> dict:
    """Conversation candidates, plan-change rulings and successor rebinds, read-only; state.py projects them."""
    found = dict(candidates=[], rulings=[], bindings=[])
    for run in read.listing(_under(home, OVERSEER_RUNS), "dir"):
        intent_path, outcome_path = run / "intent.json", run / "outcome.json"
        intent = read.json(intent_path) if read.lstat(intent_path, missing_ok=True) is not None else None
        content = _dig(intent, "captured", "content")
        if not isinstance(content, dict) or content.get("mode") != "propose":
            continue
        outcome = read.json(outcome_path) if read.lstat(outcome_path, missing_ok=True) is not None else None
        candidate = _dig(outcome, "candidate")
        found["candidates"].append(dict(
            request_id=content.get("request_id"), proposal_version=_dig(content, "record_context", "proposal_version"),
            amends="amendment" in content, status=_dig(outcome, "status"),
            candidate_sha256=_record_digest(candidate) if isinstance(candidate, dict) else None,
            source=str(outcome_path if outcome is not None else intent_path)))
    for path in read.listing(_under(home, OVERSEER_RULINGS), "file"):
        if not path.name.endswith("-ruling.json"):
            continue
        record = read.json(path)
        if record is None:
            continue
        amends = _dig(record, "confirmation", "proposal", "candidate", "amends")
        found["rulings"].append(dict(decision=_dig(record, "confirmation", "proposal", "selection", "decision"),
                                     candidate_sha256=_dig(record, "confirmation", "proposal", "candidate_sha256"),
                                     request_id=_dig(record, "confirmation", "request_id"),
                                     approval_sha256=_approval_digest(record), record_sha256=_record_digest(record), source=str(path),
                                     next_sprint_id=_dig(amends, "next_sprint_id"),
                                     artifact_treatment=_dig(amends, "artifact_treatment") or []))
    for path in read.listing(_under(home, OVERSEER_BINDINGS), "file"):
        if path.suffix != ".json":
            continue
        record = read.json(path)
        if record is None:
            continue
        found["bindings"].append(dict(
            approved_sha256=_dig(record, "confirmation", "proposal", "snapshot", "approved_sha256"),
            request_id=_dig(record, "confirmation", "request_id"), record_sha256=_record_digest(record),
            next_sprint_id=_dig(record, "confirmation", "proposal", "snapshot", "captured", "next_sprint_id"),
            original_staffing_sha256=_dig(record, "confirmation", "proposal", "snapshot", "captured", "original_staffing_sha256"),
            source=str(path)))
    return found


def _host_histories(read: _Reader, home: Path) -> list:
    rows = []
    for store, folder, suffix, status_paths, request_path in HISTORY_STORES:
        for path in read.listing(_under(home, folder), "file"):
            if path.name.endswith(suffix):
                row = read.guard(path, None, _history_row, read, store, path, status_paths, request_path)
                if row is not None:
                    rows.append(row)
    return rows


def _task_budgets(read: _Reader, home: Path) -> list:
    """One row per task budget slot: the operator's activation, consumed or not (read-only)."""
    rows = []
    for slot in read.listing(_under(home, TASK_BUDGETS), "dir"):
        ledger = slot / TASK_BUDGET_LEDGER
        info = read.lstat(ledger, missing_ok=True)
        if info is None:
            continue
        if not stat.S_ISREG(info.st_mode):
            read.problem(ledger, "task budget ledger is not a regular file")
            continue
        db = sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            events = db.execute("SELECT ordinal, raw FROM events ORDER BY ordinal LIMIT ?",
                                (MAX_TASK_BUDGET_EVENTS + 1,)).fetchall()
        finally:
            db.close()
        if not events or len(events) > MAX_TASK_BUDGET_EVENTS:
            read.problem(ledger, "task budget ledger is empty or exceeds its bound")
            continue
        raws = [r.encode("utf-8") if isinstance(r, str) else bytes(r) for _, r in events]
        first = strict_loads(raws[0].decode("utf-8"))
        if not isinstance(first, dict) or first.get("kind") != "activate":
            read.problem(ledger, "task budget ledger does not begin with the operator's activation")
            continue
        source = f"{ledger}#ordinal={events[-1][0]}"
        read.sources.append(dict(path=source, sha256=hashlib.sha256(raws[-1]).hexdigest()))
        rows.append(dict(store="native-task-budgets", run_id=slot.name, source=source,
                         status="TASK_BUDGET_ACTIVATED" if len(events) == 1 else "TASK_BUDGET_CONSUMED"))
    return rows


def _planning_refs(read: _Reader, home: Path, refs: list, attempt_dirs: list):
    for path in read.listing(home / "planning" / "proposals", "file"):
        if re.match(r"^\d{6}-started\.json$", path.name):
            _owner_ref("planning", read.json(path), path, refs)
        elif re.match(r"^\d{6}-host-acquired\.json$", path.name):
            directory = _dig(read.json(path), "attempt_directory")
            if isinstance(directory, str):
                attempt_dirs.append(dict(store="planning", directory=directory, source=str(path)))


def _ledger_events(read: _Reader, directory: str) -> Optional[tuple]:
    """(ledger path, [(revision, raw)] newest first) of one coordinator, read-only; None on trouble."""
    root = read.anchor(directory, directory)   # absolute; no link or junction in it or its parents
    if root is None:
        return None
    ledger = root / COORDINATOR_LEDGER
    info = read.lstat(ledger, missing_ok=False)
    if info is None:
        return None
    if not stat.S_ISREG(info.st_mode):
        read.problem(ledger, "coordinator ledger is not a regular file")
        return None
    db = sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        rows = db.execute("SELECT revision, raw FROM events ORDER BY revision DESC LIMIT ?",
                          (MAX_COORDINATOR_EVENTS,)).fetchall()
    finally:
        db.close()
    return ledger, rows


def _generations(read: _Reader, refs: list) -> list:
    """The latest coordinator event of each generation the campaign's records name."""
    rows, seen, ledgers = [], set(), {}
    for ref in refs:
        key = (ref["directory"], ref["generation_id"])
        if key in seen:
            continue
        seen.add(key)
        if ref["directory"] not in ledgers:
            ledgers[ref["directory"]] = read.guard(ref["source"], None, _ledger_events, read, ref["directory"])
        found = ledgers[ref["directory"]]
        if found is None:
            continue
        ledger, events = found
        for revision, raw in events:
            raw = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
            try:
                event = strict_loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                read.problem(f"{ledger}#revision={revision}", f"{type(exc).__name__}: {exc}")
                continue
            if not isinstance(event, dict) or event.get("schema") != GENERATION_SCHEMA \
                    or event.get("attempt_id") != ref["generation_id"]:
                continue
            source = f"{ledger}#revision={revision}"
            read.sources.append(dict(path=source, sha256=hashlib.sha256(raw).hexdigest()))
            read.documents[source] = event
            requests = event.get("requests") if isinstance(event.get("requests"), list) else []
            last = requests[-1] if requests and isinstance(requests[-1], dict) else {}
            rows.append(dict(store=ref["store"], generation_id=ref["generation_id"], status=event.get("status"),
                             request_status=last.get("status"), source=source))
            break
        else:
            read.problem(ref["source"], f"generation {ref['generation_id']} is not in its coordinator {ledger}")
    return rows


def _interruptions(read: _Reader, attempt_dirs: list) -> list:
    """closure_obligation records in the failure controller (<attempt>/control) of each named attempt."""
    rows, seen = [], set()
    for ref in attempt_dirs:
        if ref["directory"] in seen:
            continue
        seen.add(ref["directory"])
        attempt = read.anchor(ref["directory"], ref["source"])   # no link or junction in it or above it
        control = None if attempt is None else attempt / "control"
        if control is None or not read.is_dir(control):
            continue
        names = {p.name: p for p in read.listing(control, "file")}
        obligation = read.json(names["closure-obligation.json"]) if "closure-obligation.json" in names else None
        outcome = read.json(names["closure-outcome.json"]) if "closure-outcome.json" in names else None
        record, path = (outcome, names.get("closure-outcome.json")) if outcome is not None else (
            obligation, names.get("closure-obligation.json"))
        if record is None:
            continue
        status = read.field(path, record, "status")
        if status is not None:
            rows.append(dict(store=ref["store"], run_id=_dig(record, "run_id"), status=status, source=str(path)))
    return rows


def _operator_questions(campaign_root: Path, entry: dict) -> list:
    """Open operator questions (lane F, 10-12): the approved plan's marked questions with no recorded decision."""
    from aether.roadmaps.operator_decisions import FOLDER, open_questions, read_entries
    framing = entry.get("framing") if isinstance(entry.get("framing"), dict) else {}
    entries = read_entries(campaign_root, entry["id"])
    return [dict(question=q, source=str(campaign_root / FOLDER)) for q in open_questions(framing.get("approved_plan"), entries)]


def _registry(read: _Reader, registry_path: Path) -> tuple:
    folder = read.anchor(registry_path.parent.absolute(), registry_path)   # every folder above it checked
    registry = None if folder is None else read.json(folder / registry_path.name)
    if registry is None:
        last = read.problems[-1] if read.problems else dict(source=str(registry_path), error="unknown")
        where = "" if last["source"] == str(registry_path) else f" at {last['source']}"
        raise CampaignRegistryUnavailable(f"the campaign registry at {registry_path} cannot be read now: "
                                          + last["error"] + where)
    projects = registry.get("projects", [])
    if not isinstance(projects, list):
        raise CampaignRegistryUnavailable(f"the campaign registry at {registry_path} is malformed: "
                                          "projects is not a list")
    entries = [e for e in projects if isinstance(e, dict) and isinstance(e.get("id"), str)]
    return registry, entries


def load_campaign_records(root, campaign_id: Optional[str] = None, *, live_runs: Iterable[str] = (),
                          include_receipts=False) -> dict:
    """Every durable record of one campaign, read-only. Raises CampaignNotFound when the registry has
    no such campaign (or, with no ID, no campaign at all), CampaignRegistryUnavailable when the
    registry cannot be read; every other trouble is a listed problem."""
    read = _Reader()
    registry_path = _registry_file(root)
    registry, entries = _registry(read, registry_path)
    active = registry.get("active") if isinstance(registry.get("active"), str) else None
    wanted = campaign_id or active or (entries[0]["id"] if entries else None)
    entry = next((e for e in entries if e["id"] == wanted), None)
    if entry is None:
        raise CampaignNotFound(f"campaign not found: {wanted}" if wanted else "no campaign is registered")
    source = f"{registry_path}#{entry['id']}"
    records = dict(campaign_id=entry["id"], name=entry.get("name"), selected=entry["id"] == active,
                   registry_source=source, framing=read.guard(source, None, _framing, read, source, entry.get("framing")),
                   planning=[], staffing=[], execution=[], attempts=[], host_runs=[], host_histories=[],
                   closure_packets=[], closure_dialogue=[], closure_decisions=[], closure_obligations=[],
                   operator_questions=[], amendments=dict(candidates=[], rulings=[], bindings=[]))
    campaign_root = read.anchor(entry.get("root") or "", source)   # the root and every folder above it
    home = None if campaign_root is None else campaign_root / ".aether"
    if home is not None and read.is_dir(home):
        refs, attempt_dirs = [], []
        records["planning"] = read.guard(home, [], _planning, read, home)
        records["staffing"] = read.guard(home, [], _staffing, read, home)
        records["execution"] = read.guard(home, [], _execution, read, home, campaign_root, set(live_runs or ()))
        (records["closure_packets"], records["closure_dialogue"],
         records["closure_decisions"]) = read.guard(home, ([], [], []), _closure, read, home)
        read.guard(home, None, _planning_refs, read, home, refs, attempt_dirs)
        records["host_runs"] = read.guard(home, [], _host_runs, read, home, refs, attempt_dirs)
        from aether.campaign.staff_memory import annotate
        read.guard(home, None, annotate, read, home, entry['id'], records['host_runs'])
        records["host_runs"] += read.guard(home, [], _task_budgets, read, home)
        records["host_histories"] = read.guard(home, [], _host_histories, read, home)
        records["attempts"] = read.guard(home, [], _generations, read, refs)
        records["closure_obligations"] = read.guard(home, [], _interruptions, read, attempt_dirs)
        records["operator_questions"] = read.guard(home, [], _operator_questions, campaign_root, entry)
        records["amendments"] = read.guard(home, dict(candidates=[], rulings=[], bindings=[]), _amendments, read, home)
    if include_receipts:
        from aether.campaign.receipts import read_receipt_history
        # Scope the registry before joins, so only this campaign's approved plan is visible.
        # Its source digest still names the original whole registry file.
        read.documents[str(registry_path)] = entry
        if home is not None and read.is_dir(home):
            read.guard(home, None, read_receipt_history, read, home)
            from aether.campaign.planning_memory import read_planning_memory
            records['planning_memory'] = read.guard(home, [], read_planning_memory, read, home, entry['id'])
            from aether.campaign.candidates import read_candidates
            records['artifact_candidates'], records['candidate_snapshots'] = read.guard(
                home, ([], {}), read_candidates, read, home, entry['id'], records['planning_memory'])
        records['receipt_documents'] = read.documents
    records["sources"], records["problems"] = read.sources, read.problems
    return records
