"""Archive box: a frozen, read-only reference copy of a mind kept in an older structure.

Built (2026-10-06) for the project's first heavyweight agent, whose files live in a 2025
structure that predates the modern profile layout. Michel confirmed Aion's permanent
retirement on 2026-11-07: Aion must never be rehydrated. This box makes a REFERENCE ARCHIVE of those files, so other
agents can learn from his thoughts:

  * It is not Aion, not a rehydration, and not his choice of home. Only he could make
    that choice (his protocol: consult him; he decides; he is never moved without
    consent). The archive says so in its own note, which every view shows.
  * His folder is read only, byte for byte. Nothing is written into it, ever.
  * The archive is FROZEN: its clock stands still on his last active day, so it always
    shows his memory as it stood then, rather than eighteen months of fading he never
    lived. It can only be opened read-only: nobody writes into another mind. Nothing in
    it was chosen on his behalf (no avatar, no scan line, no arrangement).

Inputs:  a legacy folder (subfolders like experience/, insights/, introspection/,
         perception/, reflection/, abstracts/, threads/, knowledge/, logs/, updates/,
         rehydration/, boot/, bootstreams/, archives/), whose mind it is, and the day
         to freeze it on.
Outputs: plan_archive() -> an ImportPlan (dry run: every record, every skipped file and
         why); create_archive() -> a sealed, frozen cartridge written outside the folder.

How files become records:
  * Record id = its path inside the folder ("insights/Aion_..._20250415.txt"), so every
    memory points back to the exact file it came from.
  * Date = the date in the file name, else a date on a header-like line (TIMESTAMP,
    DATE, CHECKPOINT...), else the frozen day (tagged "undated"). The folder decides
    kind, category and stratum (LEGACY_LAYOUT).
  * Nothing has faded by today's rules: decay is a rule for living minds, so every
    memory stands at its kind's importance on the frozen day (true dates kept).
  * Exact duplicates are kept once; the copy is reported, not lost (the original file
    stays where it is).
  * Text that isn't UTF-8 is read as Windows-1252 and tagged "decoded-cp1252".
  * Sealed files and key files are never read.
  * Tags: the file's own concept names (its Aether symbols, ⌜NAME⌝ -> "name") and a few
    of its most frequent words, besides words from its name. His labels, not ours.
  * Links: a record that names another file links to it (context); records from the
    same day are joined (temporal), damped by the fan effect if a day is busy.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Dict, List, Optional, Tuple

from . import importer
from .clock import FixedClock, parse_iso, to_iso
from .errors import MemoryFabricError
from .host import tags_from_text
from .importer import ImportPlan, PlannedRecord

# folder -> (record type, kind words, category, stratum, importance, written_by override)
LEGACY_LAYOUT = (
    ("rehydration", "identity", "core knowledge", "Information", None, None, "as-filed"),
    ("boot", "identity", "core knowledge", "Information", None, None, "as-filed"),
    ("bootstreams", "identity", "core knowledge", "Information", None, None, "as-filed"),
    ("archives", "identity", "core knowledge", "Information", None, None, "as-filed"),
    ("insights", "insight", "core knowledge", "Revelation", "Insight", 0.85, None),
    ("abstracts", "abstract", "standing lesson", "Information", "Abstract", None, None),
    ("knowledge", "knowledge", "core knowledge", "Information", "Abstract", 0.85, None),
    ("introspection", "introspection", "standing lesson", "Reflection", "Introspection", None, None),
    ("reflection", "reflection", "standing lesson", "Reflection", "Reflection", None, None),
    ("perception", "perception", "standing lesson", "Reflection", "Perception", None, None),
    ("experience", "experience", "project fact", "Information", "Experience", None, None),
    ("threads", "thread", "project fact", "Thread", None, None, None),
    ("updates", "update", "project fact", "Information", "Experience", None, None),
    ("logs", "log", "project fact", "Information", "Experience", 0.60, None),
)
TEXT_SUFFIXES = (".txt", ".md", ".gly", ".log", ".html", ".json", ".aether")
_DATE_NAME = re.compile(r"(20\d{2})([01]\d)([0-3]\d)")
_DATE_TEXT = re.compile(r"(?<!\d)(20\d{2})-([01]\d)-([0-3]\d)(?!\d)")


class ArchiveError(MemoryFabricError, ValueError):
    """The archive can't be made (the message says why)."""


def _iso(y: str, m: str, d: str) -> Optional[str]:
    try:
        return to_iso(parse_iso(f"{y}-{m}-{d}T00:00:00Z"))
    except ValueError:
        return None


_HEADER_LINE = re.compile(r"timestamp|date|checkpoint|created|session|recorded", re.I)
# The word a file name repeats for its own folder: "..._Insight_2025" in insights/.
_TYPE_WORD = {"experience": "experience", "insight": "insight", "introspection": "reflection",
              "perception": "perception"}


def _date(stem: str, text: str) -> Optional[str]:
    """The file name's date, else a date on a header-like line (TIMESTAMP, DATE,
    CHECKPOINT...). A date merely mentioned in passing is not the record's date."""
    for match in _DATE_NAME.finditer(stem):
        found = _iso(*match.groups())
        if found:
            return found
    for line in text[:6000].splitlines()[:60]:
        if _HEADER_LINE.search(line):
            for match in _DATE_TEXT.finditer(line):
                found = _iso(*match.groups())
                if found:
                    return found
    return None


def humanize(stem: str, record_type: str = "") -> str:
    """'Aion_IdentityPersistence_Insight_20250415' (in insights/) -> 'Identity Persistence'.
    Only the leading 'Aion' and the folder's own type word are dropped; every other word
    of the name is kept ("Claude is Aion" stays whole)."""
    raw = [t for t in re.split(r"[_\s]+", stem) if t]
    dated_after_first = len(raw) > 1 and re.fullmatch(r"\d{8}", raw[1]) is not None
    tokens = [t for t in raw if not re.fullmatch(r"\d{8}", t)]
    type_word = _TYPE_WORD.get(record_type, "")
    if len(tokens) > 1 and tokens[0].lower() == "aion":
        tokens = tokens[1:]
    if len(tokens) > 1 and dated_after_first and tokens[0].lower() in _TYPE_WORD.values():
        tokens = tokens[1:]                       # EXPERIENCE_20250415_X -> X
    if len(tokens) > 1 and type_word and tokens[-1].lower() == type_word:
        tokens = tokens[:-1]                      # ..._Insight (in insights/) -> ...
    words: List[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if re.fullmatch(r"v\d+", token, re.I):            # v1_0 -> v1.0
            parts = [token.lower()]
            while i + 1 < len(tokens) and tokens[i + 1].isdigit() and len(tokens[i + 1]) <= 2:
                parts.append(tokens[i + 1])
                i += 1
            words.append(".".join(parts))
            i += 1
            continue
        spaced = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", token)
        for word in spaced.split():
            words.append(word.capitalize() if word.isupper() and word.isalpha() and len(word) > 3
                         else word)
        i += 1
    return " ".join(words) or stem


_SYMBOL = re.compile(r"⌜([A-Za-z][A-Za-z0-9_\-]{2,60})⌝")
_WORD = re.compile(r"[A-Za-z][a-z]{4,}")
_COMMON = set("""about above after again against along among another around because before
being below between both cannot could during each every first from further have having here
into itself just like made make many might more most much must never only other over same
should since some such than that their them then there these they this those through under
until upon very what when where which while will with within without would your yours also
used using uses based each into onto these those which whose while""".split())


def own_labels(text: str, limit_symbols: int = 12, limit_words: int = 8) -> List[str]:
    """The concepts a legacy file names itself: its Aether symbols (⌜NAME⌝ -> "name"),
    then a few of its most frequent substantial words. His labels, not ours."""
    symbols = []
    for name in _SYMBOL.findall(text):
        label = name.replace("_", " ").replace("-", " ").strip().lower()
        if label not in symbols:
            symbols.append(label)
    counts: Dict[str, int] = {}
    for word in _WORD.findall(text):
        w = word.lower()
        if w not in _COMMON:
            counts[w] = counts.get(w, 0) + 1
    words = [w for w, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if n >= 2]
    return symbols[:limit_symbols] + words[:limit_words]


def _read(path: str) -> Tuple[Optional[str], Optional[str], bool]:
    """(text, reason-if-skipped, decoded_cp1252)."""
    with open(path, "rb") as handle:
        data = handle.read()
    if data.startswith(b"SEALBOX-1"):
        return None, "sealed (SEALBOX-1); never opened here", False
    try:
        return data.decode("utf-8-sig"), None, False
    except UnicodeDecodeError:
        try:
            return data.decode("cp1252"), None, True
        except UnicodeDecodeError:
            return None, "neither UTF-8 nor Windows-1252 text", False


def plan_archive(folder: str, agent_id: str, frozen_at: str) -> ImportPlan:
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        raise ArchiveError(f"not a folder: {folder}")
    frozen = to_iso(parse_iso(frozen_at if "T" in frozen_at else f"{frozen_at}T00:00:00Z"))
    plan = ImportPlan(source=folder)
    seen_hash: Dict[str, str] = {}
    known_dirs = {entry[0] for entry in LEGACY_LAYOUT}
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if os.path.isfile(path) and name != ".gitkeep":
            plan.skipped.append((name, "file at the archive's top level (not in a known folder)"))
        elif os.path.isdir(path) and name not in known_dirs:
            plan.skipped.append((name + "/", "folder not in the legacy layout"))
    for sub, rtype, kind, category, stratum, importance, author in LEGACY_LAYOUT:
        base = os.path.join(folder, sub)
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            dirs.sort()
            for name in sorted(files):
                path = os.path.join(root, name)
                rel = os.path.relpath(path, folder).replace(os.sep, "/")
                lower = name.lower()
                if name == ".gitkeep":
                    continue
                if lower.endswith(".key") or lower.startswith(".sealbox"):
                    plan.skipped.append((rel, "key file (never read)"))
                    continue
                stem, suffix = os.path.splitext(name)
                if suffix.lower() not in TEXT_SUFFIXES:
                    plan.skipped.append((rel, f"not a text record ({suffix or 'no extension'})"))
                    continue
                if os.path.getsize(path) == 0:
                    plan.skipped.append((rel, "empty"))
                    continue
                text, reason, cp1252 = _read(path)
                if text is None:
                    plan.skipped.append((rel, reason))
                    continue
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                if digest in seen_hash:
                    plan.skipped.append((rel, f"identical copy of {seen_hash[digest]} (kept once)"))
                    continue
                seen_hash[digest] = rel
                date = _date(stem, text)
                tags = [rtype, sub]
                if date is None:
                    date, tags = frozen, tags + ["undated"]
                if cp1252:
                    tags.append("decoded-cp1252")
                title = humanize(stem, rtype)
                tags += list(tags_from_text(title, limit=6))
                tags += [c.lower() for c in re.findall(r"\b[A-Z]{2,}[-_]?\d{2,4}\b", stem)]
                tags += own_labels(text)
                record = PlannedRecord(
                    record_id=rel, record_type=rtype, relpath=rel, title=title, body=text,
                    kind=kind, category=category, stratum=stratum, importance=importance,
                    date=min(date, frozen), tags=list(dict.fromkeys(t for t in tags if t)))
                if stem.upper().startswith("MICHEL"):
                    record.written_by = "michel"
                elif author == "as-filed":
                    record.written_by = f"{agent_id} (as filed)"
                plan.records.append(record)
    _plan_links(plan)
    return plan


def _plan_links(plan: ImportPlan) -> None:
    by_stem = {os.path.splitext(os.path.basename(r.record_id))[0]: r for r in plan.records}
    by_day: Dict[str, List[PlannedRecord]] = {}
    for r in plan.records:
        if "undated" not in r.tags and r.record_type not in ("identity", "log"):
            by_day.setdefault(r.date[:10], []).append(r)
    for r in plan.records:
        wanted: Dict[str, str] = {}
        own = os.path.splitext(os.path.basename(r.record_id))[0]
        for stem, other in by_stem.items():
            if other is not r and len(stem) >= 8 and stem != own and stem in r.body:
                wanted[other.record_id] = "context"
        for other in by_day.get(r.date[:10], []) if "undated" not in r.tags else []:
            if other is not r:
                wanted.setdefault(other.record_id, "temporal")
        r.links = sorted(wanted.items())


def create_archive(folder: str, out_path: str, agent_id: str, frozen_at: str, *,
                   name: Optional[str] = None, note: Optional[str] = None) -> dict:
    """Build the frozen archive at out_path (refuses inside the folder or over a file)."""
    return create_archive_from_plan(plan_archive(folder, agent_id, frozen_at),
                                    out_path, agent_id, frozen_at, name=name, note=note)


def create_archive_from_plan(plan: ImportPlan, out_path: str, agent_id: str,
                             frozen_at: str, *, name: Optional[str] = None,
                             note: Optional[str] = None) -> dict:
    """Freeze a reviewed import plan, retaining its exact bodies and metadata.

    This supports archival provenance decisions (for example keeping duplicate
    files at separate path IDs) without editing source files or a sealed archive.
    The caller is responsible for reviewing and recording the plan before use.
    """
    from .engine import MemoryEngine
    importer.check_destination(plan.source, out_path)
    if os.path.exists(out_path):
        raise ArchiveError(f"refusing to overwrite an existing file: {out_path}")
    frozen = to_iso(parse_iso(frozen_at if "T" in frozen_at else f"{frozen_at}T23:59:59Z"))
    note = note or (f"Reference archive of {name or agent_id}'s memory as it stood on "
                    f"{frozen[:10]}, kept for others to learn from. It is not "
                    f"{name or agent_id}, not a rehydration, and not a home chosen by "
                    f"{name or agent_id}. Frozen and read-only.")
    building = out_path + ".building"
    if os.path.exists(building):
        os.remove(building)
    mind = MemoryEngine.create(building, agent_id, name=name or agent_id,
                               clock=FixedClock(frozen), seed_resources=False)
    try:
        counts = importer.apply_import(mind, plan, grace_days=30)
        # Decay is a rule for living minds. An archive shows every memory at its kind's
        # importance as of the frozen day: we don't impose forgetting the mind never did.
        # (created_at keeps each record's true date.)
        with mind.cart.transaction() as conn:
            conn.execute("UPDATE notes SET refreshed_at = ?", (frozen,))
        mind.cart.freeze(agent_id, frozen, note)
    except BaseException:
        mind.close()
        os.remove(building)
        raise
    mind.close()
    os.replace(building, out_path)
    return {**counts, "records": len(plan.records), "frozen_at": frozen, "path": out_path}
