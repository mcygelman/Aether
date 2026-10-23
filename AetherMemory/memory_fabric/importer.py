"""Importer box: bring an existing agent's markdown memory into a cartridge.

Inputs:  an AgentProfile memory folder (the Aether heavyweight layout:
         archive/experiences/EXP_*, archive/introspections/INT_*,
         archive/perceptions/*, index/insights/INS_*, index/abstracts/*,
         threads/*, and index/experience_index.md for significance).
Outputs: plan_import()  -> ImportPlan: every record it would write (kind,
                           category, importance, dates, tags, links) and
                           every file it skipped and why. Nothing is written.
         apply_import() -> writes the plan into an open engine; returns
                           counts. Re-running skips records already present,
                           so an import can be resumed or repeated safely.

Read-only on the source, always: the importer only reads files, never
writes, moves or renames anything in the profile. It never opens key files
(*.key, .sealbox_key) and skips sealed files (SEALBOX-1) with a reason. A
sealed profile has to be unsealed by its owner's own tooling first.

How old records are dated (an import is not a fresh memory):
  * created_at / refreshed_at = the record's own date, so recency is real.
  * switch date = the record's date + its kind's horizon, but never earlier
    than import time + grace_days (default 30). Without the grace, an
    agent's whole history would start fading on its first day in the
    cartridge; with it, the agent has a month to use what matters (pulling
    a memory up refreshes it), and what it never touches fades naturally.

How records map (all choices are dials here, in one place):
  experience     -> project fact,     Information, stratum Experience,
                    importance from the index's significance column
  introspection  -> standing lesson,  Reflection,  stratum Introspection
  perception     -> standing lesson,  Reflection,  stratum Perception
  insight        -> core knowledge,   Revelation,  stratum Insight, importance
                    0.85 (slow like a principle, but below the fog bar)
  abstract       -> standing lesson,  Information, stratum Abstract
  thread         -> project fact,     Thread
Links: an introspection and an insight with the same name are joined
("derived"); a record that names another record's id links to it
("context"); an insight's "drawn from" dates link it to those days'
experiences ("derived").
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .clock import add_days, parse_iso, to_iso
from .errors import MemoryFabricError
from .fabric import normalize_tag
from .host import tags_from_text

DEFAULT_GRACE_DAYS = 30
MAX_TAGS = 12

SIGNIFICANCE_IMPORTANCE = {"CRITICAL": 0.85, "HIGH": 0.80, "STANDARD": 0.70, "MEDIUM": 0.70,
                           "LOW": 0.50}

# folder (relative to memory/) -> (record type, kind words, category, stratum, importance)
LAYOUT = (
    ("archive/experiences", "experience", "project fact", "Information", "Experience", None),
    ("archive/introspections", "introspection", "standing lesson", "Reflection", "Introspection", None),
    ("archive/perceptions", "perception", "standing lesson", "Reflection", "Perception", None),
    # Insights are an agent's distilled principles: core-knowledge pace, but importance
    # 0.85 (below the fog bar). As standing lessons they sank below the surface after
    # ~4 months unused (importer test, 2026-10-06), while the old system loaded them
    # every session.
    ("index/insights", "insight", "core knowledge", "Revelation", "Insight", 0.85),
    ("index/abstracts", "abstract", "standing lesson", "Information", "Abstract", None),
    ("threads", "thread", "project fact", "Thread", None, None),
)
INDEX_FILES = ("index/experience_index.md", "index/thread_index.md", "README.md")
TEXT_SUFFIXES = (".md", ".txt")
_RECORD_ID = re.compile(r"\b((?:EXP|INT|INS|PER|ABS|THR)_\d{8}(?:_[A-Za-z0-9]+)+)\b")
_DATE = re.compile(r"(20\d{2})[-_]?([01]\d)[-_]?([0-3]\d)")
_CODE = re.compile(r"\b([A-Z]{1,6}-?\d{2,5}[A-Z]?)\b")
_DRAWN = re.compile(r"drawn from[:*\s]*(.+?)(?:\n\n|\Z)", re.I | re.S)


class ImporterError(MemoryFabricError, ValueError):
    """The import cannot proceed (the message says why)."""


@dataclass
class PlannedRecord:
    record_id: str
    record_type: str
    relpath: str
    title: str
    body: str
    kind: str
    category: str
    stratum: Optional[str]
    importance: Optional[float]
    date: str                       # ISO timestamp of the record's own date
    tags: List[str] = field(default_factory=list)
    links: List[Tuple[str, str]] = field(default_factory=list)   # (other record id, relation)
    significance: Optional[str] = None
    proprietary: Optional[bool] = None   # the agent's own choice (move-in sheet), else the default
    switch_at: Optional[str] = None      # the agent's own date (move-in sheet), else computed
    written_by: Optional[str] = None     # who wrote it, when not the agent itself (archives)


@dataclass
class ImportPlan:
    source: str
    records: List[PlannedRecord] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)   # (relpath, reason)
    index_rows: int = 0

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for record in self.records:
            counts[record.record_type] = counts.get(record.record_type, 0) + 1
        counts["links"] = sum(len(r.links) for r in self.records)
        counts["skipped"] = len(self.skipped)
        return counts

    def render(self) -> str:
        lines = [f"IMPORT PLAN for {self.source}"]
        for key, value in self.summary().items():
            lines.append(f"  {key}: {value}")
        for relpath, reason in self.skipped[:50]:
            lines.append(f"  skipped {relpath}: {reason}")
        if len(self.skipped) > 50:
            lines.append(f"  (+{len(self.skipped) - 50} more skipped)")
        return "\n".join(lines)


def _is_key_file(name: str) -> bool:
    lower = name.lower()
    return lower.endswith(".key") or lower.startswith(".sealbox")


def _read(path: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (text, None) or (None, reason). Never decrypts anything."""
    with open(path, "rb") as handle:
        data = handle.read()
    if data.startswith(b"SEALBOX-1\n") or data.startswith(b"SEALBOX-1\r\n"):
        return None, "sealed (SEALBOX-1); unseal with the owner's tooling first"
    try:
        return data.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return None, "not UTF-8 text"


def _date_of(*texts: str) -> Optional[str]:
    for text in texts:
        match = _DATE.search(text or "")
        if match:
            y, m, d = match.groups()
            try:
                return to_iso(parse_iso(f"{y}-{m}-{d}T00:00:00Z"))
            except ValueError:
                continue
    return None


def _humanize(record_id: str) -> str:
    parts = record_id.split("_")
    words = [p for p in parts[2:]] if len(parts) > 2 else parts
    return " ".join(words).strip() or record_id


def _index_rows(memory_dir: str) -> Dict[str, dict]:
    """Rows of the experience/thread index tables, keyed by record id."""
    rows: Dict[str, dict] = {}
    for rel in INDEX_FILES:
        path = os.path.join(memory_dir, *rel.split("/"))
        if not os.path.isfile(path):
            continue
        text, _ = _read(path)
        if text is None:
            continue
        header: Optional[List[str]] = None
        for line in text.splitlines():
            if not line.strip().startswith("|"):
                header = None if not line.strip() else header
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            if header is None:
                header = [c.lower() for c in cells]
                continue
            row = dict(zip(header, cells))
            record_id = row.get("id") or ""
            if _RECORD_ID.fullmatch(record_id):
                rows[record_id] = row
    return rows


def _tags(record_type: str, record_id: str, title: str, body: str, extra: str = "") -> List[str]:
    tags = [record_type]
    tags += [code.lower() for code in _CODE.findall(f"{title}\n{extra}")]
    tags += list(tags_from_text(title, limit=6))
    tags += list(tags_from_text(extra, limit=4))
    clean = []
    for tag in tags:
        tag = tag.strip().lower()
        if tag and tag not in clean and not tag.isdigit():
            clean.append(tag)
    return clean[:MAX_TAGS]


def plan_import(profile_memory_dir: str) -> ImportPlan:
    memory_dir = os.path.abspath(profile_memory_dir)
    if not os.path.isdir(memory_dir):
        raise ImporterError(f"not a folder: {profile_memory_dir}")
    plan = ImportPlan(source=memory_dir)
    index = _index_rows(memory_dir)
    plan.index_rows = len(index)
    seen: Dict[str, PlannedRecord] = {}
    for folder, record_type, kind, category, stratum, importance in LAYOUT:
        base = os.path.join(memory_dir, *folder.split("/"))
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            path = os.path.join(base, name)
            relpath = f"{folder}/{name}"
            if os.path.isdir(path):
                plan.skipped.append((relpath, "sub-folder (not imported)"))
                continue
            if _is_key_file(name):
                plan.skipped.append((relpath, "key file (never read)"))
                continue
            stem, suffix = os.path.splitext(name)
            if suffix.lower() not in TEXT_SUFFIXES:
                plan.skipped.append((relpath, f"not a text record ({suffix or 'no extension'})"))
                continue
            if "TEMPLATE" in stem.upper() and not _DATE.search(stem):
                # Blank forms (EXPERIENCE_TEMPLATE.txt) carry no date; a dated record
                # about templates is a real memory (three of Praxis's were, 2026-10-06).
                plan.skipped.append((relpath, "template (not a memory)"))
                continue
            text, reason = _read(path)
            if text is None:
                plan.skipped.append((relpath, reason))
                continue
            if not text.strip():
                plan.skipped.append((relpath, "empty"))
                continue
            record_id = stem
            if record_id in seen:
                plan.skipped.append((relpath, f"duplicate record id {record_id}"))
                continue
            row = index.get(record_id, {})
            significance = (row.get("significance") or "").upper() or None
            if significance is None:
                match = re.search(r"\*\*Significance:\*\*\s*([A-Z]+)", text)
                significance = match.group(1) if match else None
            rec_importance = importance
            if record_type == "experience" and significance in SIGNIFICANCE_IMPORTANCE:
                rec_importance = SIGNIFICANCE_IMPORTANCE[significance]
            title = row.get("title") or _humanize(record_id)
            date = _date_of(row.get("date", ""), record_id, text)
            if date is None:
                plan.skipped.append((relpath, "no date found in name, index or text"))
                continue
            triggers = ""
            match = re.search(r"\*\*Triggers?:\*\*\s*(.+)", text)
            if match:
                triggers = match.group(1)
            record = PlannedRecord(
                record_id=record_id, record_type=record_type, relpath=relpath, title=title,
                body=text, kind=kind, category=category, stratum=stratum,
                importance=rec_importance, date=date, significance=significance,
                tags=_tags(record_type, record_id, title, text, f"{row.get('summary', '')} {triggers}"))
            seen[record_id] = record
            plan.records.append(record)

    _plan_links(plan, seen)
    return plan


def _plan_links(plan: ImportPlan, seen: Dict[str, PlannedRecord]) -> None:
    by_suffix: Dict[str, List[PlannedRecord]] = {}
    by_date: Dict[str, List[PlannedRecord]] = {}
    for record in plan.records:
        suffix = "_".join(record.record_id.split("_")[1:])
        by_suffix.setdefault(suffix, []).append(record)
        if record.record_type == "experience":
            by_date.setdefault(record.date[:10].replace("-", ""), []).append(record)

    for record in plan.records:
        wanted: Dict[str, str] = {}
        if record.record_type == "insight":
            suffix = "_".join(record.record_id.split("_")[1:])
            for twin in by_suffix.get(suffix, []):
                if twin is not record:
                    wanted[twin.record_id] = "derived"
            drawn = _DRAWN.search(record.body)
            if drawn:
                for day in re.findall(r"EXP_(\d{8})", drawn.group(1)):
                    for exp in by_date.get(day, []):
                        wanted.setdefault(exp.record_id, "derived")
        for other in _RECORD_ID.findall(record.body):
            if other in seen and other != record.record_id:
                wanted.setdefault(other, "context")
        record.links = sorted(wanted.items())


def _canonical(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def profile_root(memory_dir: str) -> str:
    """The agent's whole profile folder: the parent of memory/, or the folder itself."""
    folder = _canonical(memory_dir)
    return os.path.dirname(folder) if os.path.basename(folder) == "memory" else folder


def check_destination(memory_dir: str, cartridge_path: str) -> None:
    """Refuse a cartridge anywhere inside the agent's profile (the importer never
    writes there). Case-insensitive on Windows; follows links."""
    if cartridge_path == ":memory:":
        return
    root, target = profile_root(memory_dir), _canonical(cartridge_path)
    try:
        inside = os.path.commonpath([root, target]) == root
    except ValueError:            # different drives on Windows
        inside = False
    if inside:
        raise ImporterError("refusing to write inside the agent's profile folder: the engine "
                            "never writes there (keep cartridges and walkthrough files outside it; "
                            "the agent itself may copy its sheet wherever it likes)")


def apply_import(engine, plan: ImportPlan, *, grace_days: float = DEFAULT_GRACE_DAYS,
                 proprietary: bool = False) -> Dict[str, int]:
    """Write the plan into the engine's cartridge. Returns counts.

    Records whose id already exists in the cartridge are skipped, and links are
    only made for records written in this run, so a repeated or interrupted
    import can simply be run again without changing what is already there.
    """
    check_destination(plan.source, engine.cart.path)
    cart = engine.cart
    now = cart.clock.now()
    floor_switch = add_days(now, grace_days)
    profiles = {p["profile"]: p for p in cart.profiles()}
    from .kinds import resolve
    written = existing = linked = 0
    written_ids = set()
    with cart.transaction() as conn:
        present = {r[0] for r in conn.execute("SELECT note_id FROM notes")}
        for record in plan.records:
            if record.record_id in present:
                existing += 1
                continue
            profile = resolve(record.kind, list(profiles))
            date = min(record.date, now)
            switch = record.switch_at or max(add_days(date, profiles[profile]["horizon_days"]),
                                             floor_switch)
            conn.execute(
                "INSERT INTO notes(note_id, title, body, profile, category, stratum, created_at,"
                " refreshed_at, switch_at, importance_override, proprietary, written_by,"
                " source_ref) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (record.record_id, record.title, record.body, profile, record.category,
                 record.stratum, date, date, switch, record.importance,
                 int(proprietary if record.proprietary is None else record.proprietary),
                 record.written_by or engine.agent_id, f"import:{record.relpath}"))
            tags = {normalize_tag(t) for t in record.tags} | {normalize_tag(record.title)}
            tags.discard("")
            conn.executemany("INSERT OR IGNORE INTO tags(note_id, tag) VALUES (?, ?)",
                             [(record.record_id, t) for t in sorted(tags)])
            present.add(record.record_id)
            written_ids.add(record.record_id)
            written += 1
    done = set()
    for record in plan.records:
        for other, relation in record.links:
            pair = (frozenset((record.record_id, other)), relation)
            fresh = record.record_id in written_ids or other in written_ids
            if other in present and fresh and pair not in done:
                engine.link(record.record_id, other, relation)
                done.add(pair)
                linked += 1
    return {"written": written, "already_present": existing, "links": linked,
            "skipped_files": len(plan.skipped)}
