"""L-02 School checkout: deterministic selection, cards, and audit journal.

The dispatcher calls :func:`checkout`; an agent never searches the catalog.
Selection is transparent token overlap, not embeddings, and every attempt is
journaled including zero-selection. Product lessons are read from resources;
never infer their location from writable installation data or copy resident memory.
"""
from __future__ import annotations
from aether.paths import BRIDGE_ROOT, LIGHTWEIGHT_ROOT, REPOSITORY_ROOT

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading
from typing import Any, Mapping, Sequence

from aether.models.budget import est_tokens
from aether.knowledge.teachframe import generate_card, metadata, section, validate as validate_teachframe

LIGHTWEIGHT_ROOT = LIGHTWEIGHT_ROOT
# REPOSITORY_ROOT is the checkout in source mode and the immutable resources
# folder in an installation. LIGHTWEIGHT_ROOT is writable user data there.
DEFAULT_CATALOG_PATH = REPOSITORY_ROOT / "AetherAgents/LightweightAgents/TeachFrames/CATALOG.json"
DEFAULT_JOURNAL_PATH = BRIDGE_ROOT / "school_journal" / "checkout.jsonl"

MU_CARD_LIMIT = 2
MU_TOKEN_LIMIT = 240

_JOURNAL_LOCK = threading.Lock()
_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_DECLINE_RE = re.compile(
    r"(?im)^\s*\[LESSON-DECLINED\]\s+([^\n—-]+?)\s*(?:—|--|-)\s*(.+?)\s*$"
)
_ASK_RE = re.compile(r"(?im)^\s*\[ASK\]\s*(.+?)\s*$")
_STOPWORDS = {
    "a", "an", "and", "any", "agent", "all", "as", "at", "be", "by", "can",
    "every", "for", "from", "in", "into", "is", "it", "of", "on", "or", "the",
    "their", "this", "to", "using", "with", "without",
}
_NORMAL_FORMS = {
    "numeric": "number",
    "numerical": "number",
    "numbers": "number",
    "results": "result",
    "tools": "tool",
    "claims": "claim",
    "references": "reference",
    "documents": "document",
    "intervals": "interval",
    "uncertain": "uncertainty",
    "validating": "validate",
    "validation": "validate",
    "reviewing": "review",
    "producing": "produce",
    "authoring": "author",
    "comparing": "compare",
    "performing": "perform",
}


class SchoolError(ValueError):
    """Catalog, checkout, or feedback data is invalid."""


@dataclass(frozen=True)
class CatalogFrame:
    name: str
    state: str
    path: Path
    title: str = ""
    audience: str = ""
    prevents: str = ""
    prerequisites: tuple[str, ...] = ()
    card: str = ""
    card_tokens: int = 0


@dataclass(frozen=True)
class RankedCandidate:
    frame: CatalogFrame
    score: int
    audience_matches: tuple[str, ...]
    keyword_matches: tuple[str, ...]

    @property
    def name(self) -> str:
        return self.frame.name


@dataclass
class CheckoutResult:
    cards: list[str]
    selected: list[str]
    dropped_for_budget: list[dict[str, Any]]
    candidates_considered: list[dict[str, Any]]
    token_cost: int
    journal_entry: dict[str, Any]


def _tokens(value: str | Sequence[str]) -> set[str]:
    text = value if isinstance(value, str) else " ".join(str(item) for item in value)
    found = set()
    for raw in _WORD_RE.findall(text.lower().replace("_", " ")):
        token = _NORMAL_FORMS.get(raw, raw)
        if token not in _STOPWORDS and len(token) > 1:
            found.add(token)
    return found


def _prerequisites(value: str) -> tuple[str, ...]:
    if not value or value.strip().upper() == "NONE":
        return ()
    parts = re.split(r"\s*(?:·|,|;)\s*", value.strip())
    return tuple(part.removeprefix("TEACHFRAME_").strip() for part in parts if part.strip())


def load_catalog(catalog_path: str | Path = DEFAULT_CATALOG_PATH) -> dict[str, CatalogFrame]:
    path = Path(catalog_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SchoolError(f"cannot load School catalog {path}: {exc}") from exc
    if raw.get("schema") != "TEACHFRAME-CATALOG-1" or not isinstance(raw.get("entries"), list):
        raise SchoolError("catalog must use TEACHFRAME-CATALOG-1 with an entries list")
    root = path.parent.parent
    frames: dict[str, CatalogFrame] = {}
    for entry in raw["entries"]:
        name, state, rel = entry.get("name"), entry.get("state"), entry.get("path")
        if not all(isinstance(value, str) and value.strip() for value in (name, state, rel)):
            raise SchoolError(f"malformed catalog entry: {entry!r}")
        if name in frames:
            raise SchoolError(f"duplicate catalog lesson: {name}")
        source = root / rel
        if not source.is_file():
            raise SchoolError(f"catalog path missing for {name}: {source}")
        if state != "CURRICULUM":
            frames[name] = CatalogFrame(name=name, state=state, path=source)
            continue
        text = source.read_text(encoding="utf-8")
        report = validate_teachframe(text)
        meta = metadata(text)
        card = generate_card(text)
        frames[name] = CatalogFrame(
            name=name,
            state=state,
            path=source,
            title=section(text, "TITLE"),
            audience=meta["AUDIENCE"],
            prevents=meta["PREVENTS"],
            prerequisites=_prerequisites(meta["PREREQUISITES"]),
            card=card,
            card_tokens=report.card_tokens,
        )
    return frames


def validate_lesson_names(
    lessons: Sequence[str], catalog_path: str | Path = DEFAULT_CATALOG_PATH
) -> None:
    frames = load_catalog(catalog_path)
    for name in lessons:
        frame = frames.get(name)
        if frame is None:
            raise SchoolError(f"lesson is absent from the catalog: {name}")
        if frame.state != "CURRICULUM":
            raise SchoolError(f"lesson is not CURRICULUM: {name} ({frame.state})")


def _audience_match(audience: str, role_context: set[str], granted: Sequence[str]) -> set[str]:
    lower = audience.lower()
    if lower in {"all", "any agent"}:
        return {"all"}
    if "scoped project tools" in lower and granted:
        return {"scoped", "project", "tool"}
    return _tokens(audience) & role_context


def _evaluate(
    agent: str,
    role: str,
    task: str,
    named_files: Sequence[str],
    granted: Sequence[str],
    catalog_path: str | Path,
) -> tuple[list[RankedCandidate], list[dict[str, Any]]]:
    del agent  # Names are recorded in the journal, never used as audience selectors.
    frames = load_catalog(catalog_path)
    role_context = _tokens([role, task, *named_files, *granted])
    keyword_context = _tokens([task, *granted])
    provisional: dict[str, RankedCandidate] = {}
    considered: list[dict[str, Any]] = []
    for frame in frames.values():
        record: dict[str, Any] = {"name": frame.name, "state": frame.state}
        if frame.state != "CURRICULUM":
            record.update(eligible=False, reason=f"state={frame.state}")
            considered.append(record)
            continue
        audience_hits = _audience_match(frame.audience, role_context, granted)
        keyword_hits = _tokens(frame.title + " " + frame.prevents) & keyword_context
        if not keyword_hits:
            record.update(eligible=False, reason="TITLE/PREVENTS had no packet keyword overlap")
        else:
            # L-02 closeout / L-03 carry-forward: AUDIENCE is a strong scoring
            # signal, not a hard visibility gate. A slightly stale audience now
            # demotes a relevant lesson while leaving an auditable rank.
            score = 3 * len(keyword_hits) + 2 * len(audience_hits)
            candidate = RankedCandidate(
                frame=frame,
                score=score,
                audience_matches=tuple(sorted(audience_hits)),
                keyword_matches=tuple(sorted(keyword_hits)),
            )
            provisional[frame.name] = candidate
            record.update(
                eligible=True,
                score=score,
                audience_matched=bool(audience_hits),
                audience_matches=list(candidate.audience_matches),
                keyword_matches=list(candidate.keyword_matches),
            )
        considered.append(record)

    eligible: list[RankedCandidate] = []
    for name, candidate in provisional.items():
        missing = [prereq for prereq in candidate.frame.prerequisites if prereq not in provisional]
        if missing:
            for record in considered:
                if record["name"] == name:
                    record.update(eligible=False, reason="prerequisites not matched: " + ", ".join(missing))
                    break
            continue
        eligible.append(candidate)
    eligible.sort(key=lambda item: (-item.score, item.name.casefold()))
    return eligible, considered


def select(
    agent: str,
    role: str,
    task: str,
    named_files: Sequence[str],
    granted: Sequence[str],
    *,
    catalog_path: str | Path = DEFAULT_CATALOG_PATH,
) -> list[RankedCandidate]:
    """Return deterministic ranked candidates for the dispatcher."""
    ranked, _ = _evaluate(agent, role, task, named_files, granted, catalog_path)
    return ranked


def _packet_value(packet: Any, key: str, default: Any = None) -> Any:
    if isinstance(packet, Mapping):
        return packet.get(key, default)
    return getattr(packet, key, default)


def _append_journal(path: str | Path, entry: dict[str, Any]) -> None:
    journal = Path(path)
    journal.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
    with _JOURNAL_LOCK:
        with journal.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)


def checkout(
    packet: Any,
    tier: str,
    *,
    catalog_path: str | Path = DEFAULT_CATALOG_PATH,
    journal_path: str | Path = DEFAULT_JOURNAL_PATH,
) -> CheckoutResult:
    """Select and journal lessons for one packet; zero-selection is still an event."""
    if tier.lower() not in {"mu", "µ", "ultralight"}:
        raise SchoolError("L-02 implements only the µ/Ultralight checkout path")
    packet_id = _packet_value(packet, "id")
    agent = _packet_value(packet, "to_agent") or _packet_value(packet, "agent")
    task = _packet_value(packet, "task")
    role = _packet_value(packet, "role", "")
    named_files = list(_packet_value(packet, "named_files", []) or [])
    granted = list(_packet_value(packet, "granted", []) or [])
    if not all(isinstance(value, str) and value.strip() for value in (packet_id, agent, task)):
        raise SchoolError("checkout packet requires non-empty id, agent/to_agent, and task")

    candidates, considered = _evaluate(agent, role, task, named_files, granted, catalog_path)
    by_name = {candidate.name: candidate for candidate in candidates}
    selected: list[RankedCandidate] = []
    selected_names: set[str] = set()
    dropped: list[dict[str, Any]] = []

    def dependency_bundle(candidate: RankedCandidate) -> list[RankedCandidate] | None:
        bundle: list[RankedCandidate] = []
        visiting: set[str] = set()

        def visit(item: RankedCandidate) -> bool:
            if item.name in selected_names or item.name in {part.name for part in bundle}:
                return True
            if item.name in visiting:
                raise SchoolError(f"cyclic lesson prerequisites at {item.name}")
            visiting.add(item.name)
            for prerequisite in item.frame.prerequisites:
                dependency = by_name.get(prerequisite)
                if dependency is None or not visit(dependency):
                    return False
            visiting.remove(item.name)
            bundle.append(item)
            return True

        return bundle if visit(candidate) else None

    for candidate in candidates:
        if candidate.name in selected_names:
            continue
        bundle = dependency_bundle(candidate)
        if bundle is None:
            dropped.append({"name": candidate.name, "reason": "prerequisite unavailable"})
            continue
        new_count = len(selected) + len(bundle)
        new_tokens = sum(item.frame.card_tokens for item in selected + bundle)
        if new_count > MU_CARD_LIMIT or new_tokens > MU_TOKEN_LIMIT:
            dropped.append({
                "name": candidate.name,
                "reason": "µ card/token budget",
                "bundle": [item.name for item in bundle],
                "projected_cards": new_count,
                "projected_tokens": new_tokens,
            })
            continue
        for item in bundle:
            if item.name not in selected_names:
                selected.append(item)
                selected_names.add(item.name)

    token_cost = sum(item.frame.card_tokens for item in selected)
    selected_list = [item.name for item in selected]
    entry = {
        "schema": "SCHOOL-CHECKOUT-1",
        "event": "checkout",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "packet_id": packet_id,
        "agent": agent,
        "role": role,
        "tier": "mu",
        "candidates_considered": considered,
        "selected": selected_list,
        "dropped_for_budget": dropped,
        "token_cost": token_cost,
        "zero_selection": not selected_list,
    }
    _append_journal(journal_path, entry)
    return CheckoutResult(
        cards=[item.frame.card for item in selected],
        selected=selected_list,
        dropped_for_budget=dropped,
        candidates_considered=considered,
        token_cost=token_cost,
        journal_entry=entry,
    )


def cards_for_lessons(
    lessons: Sequence[str], catalog_path: str | Path = DEFAULT_CATALOG_PATH
) -> list[str]:
    validate_lesson_names(lessons, catalog_path)
    frames = load_catalog(catalog_path)
    return [frames[name].card for name in lessons]


def render_lessons_block(
    lessons: Sequence[str], catalog_path: str | Path = DEFAULT_CATALOG_PATH
) -> str:
    if not lessons:
        return "--- LESSONS ---\n(no lessons matched this packet)\n"
    cards = cards_for_lessons(lessons, catalog_path)
    pieces = ["--- LESSONS ---"]
    for name, card in zip(lessons, cards):
        pieces.extend((f"lesson={name}", card.rstrip()))
    return "\n".join(pieces) + "\n"


def record_feedback(
    packet_id: str,
    agent: str,
    reply_text: str,
    *,
    journal_path: str | Path = DEFAULT_JOURNAL_PATH,
) -> list[dict[str, Any]]:
    """Log tagged declines and ASK misses; neither is an agent defect."""
    events: list[dict[str, Any]] = []
    timestamp = datetime.now(timezone.utc).isoformat()
    for match in _DECLINE_RE.finditer(reply_text or ""):
        events.append({
            "schema": "SCHOOL-CHECKOUT-1",
            "event": "selection_miss",
            "kind": "lesson_declined",
            "timestamp": timestamp,
            "packet_id": packet_id,
            "agent": agent,
            "lesson": match.group(1).strip(),
            "reason": match.group(2).strip(),
            "scored_against_agent": False,
        })
    for match in _ASK_RE.finditer(reply_text or ""):
        events.append({
            "schema": "SCHOOL-CHECKOUT-1",
            "event": "selection_miss",
            "kind": "ask",
            "timestamp": timestamp,
            "packet_id": packet_id,
            "agent": agent,
            "request": match.group(1).strip(),
            "scored_against_agent": False,
        })
    for event in events:
        _append_journal(journal_path, event)
    return events


def read_journal(journal_path: str | Path = DEFAULT_JOURNAL_PATH) -> list[dict[str, Any]]:
    path = Path(journal_path)
    if not path.exists():
        return []
    entries = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SchoolError(f"malformed School journal line {line_number}: {exc}") from exc
    return entries
