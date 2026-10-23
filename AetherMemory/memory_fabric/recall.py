"""Recall box: tags in, ranked short headers out; load what the agent chooses.

Inputs:  an open Cartridge, the task's tags, an optional token budget.
Outputs: RecallResult: ranked Headers (title, kind, strength, one line, how
         it was reached), plus how many more were available.

The flow (decision 2): the agent submits tags, gets SHORT HEADERS back, and
loads onto its working board only the notes it chooses (load()). Looking at
headers changes nothing; loading a note is "pulling it up": it refreshes
its decay clock and strengthens links among notes used together.

Reach rules:
  * A note tagged with one of the tags is a direct hit. Its reach is its
    current strength. A phrase match (the tag's spacing-insensitive form
    inside one of the note's tags, e.g. "lm studio" in "LMStudio loaded
    state") is a direct hit worth PHRASE_MATCH_FACTOR of that.
  * Fan effect: spreading from a note with more than FAN_FREE_LINKS links
    is damped by sqrt(FAN_FREE_LINKS / links), so hubs don't flood recall.
  * A multi-word tag that finds nothing falls back to its words
    (WORD_MATCH_FACTOR), so a near-miss phrase still finds something.
  * From each hit, recall travels along links. Reaching a neighbour gives
        reach = link strength x neighbour value x hop_decay^(hop - 1)
    so a decayed link stops stale baggage from coming along.
  * The fog rule (decision 19): a very important note (importance >=
    fog_importance_min) keeps at least `fog_level` as its value even when
    buried, so a live thread can still reach it "through the fog".
    Ordinary buried notes cannot be pulled up that way.
All strengths come from SQLite's temporary views (decay.temp_view_sql).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence

from . import fabric
from .cartridge import compact
from .tokens import estimate_tokens
from .history import atomic


@dataclass(frozen=True)
class Header:
    note_id: str
    title: str
    category: str
    profile: str
    strength: float
    depth: float
    reach: float
    score: float
    one_line: str
    via: str                 # "tag:<tag>" or "link:<note_id>/<relation>"
    hops: int
    through_fog: bool
    proprietary: bool
    recurring: bool          # an alert pulled up often: consider a permanent fix
    matched_tags: tuple

    def render(self) -> str:
        flags = []
        if self.through_fog:
            flags.append("through the fog")
        if self.recurring:
            flags.append("RECURRING: consider a permanent fix")
        if self.proprietary:
            flags.append("proprietary")
        extra = f" ({'; '.join(flags)})" if flags else ""
        return (f"[{self.category}] {self.title} | strength {self.strength:.2f}{extra}"
                f" | {self.one_line} [id {self.note_id}]")

    def tokens(self) -> int:
        return estimate_tokens(self.render())


@dataclass
class RecallResult:
    tags: tuple
    now: str
    headers: List[Header] = field(default_factory=list)
    more_available: int = 0
    considered: int = 0
    seed_note_ids: tuple = ()

    def ids(self) -> List[str]:
        return [h.note_id for h in self.headers]

    def render(self) -> str:
        """The block a host or agent places on the working board."""
        lines = [f"MEMORY RECALL for tags: {', '.join(self.tags) or '(none)'}"]
        if self.seed_note_ids:
            lines.append('  Explicit navigation seeds: ' + ', '.join(self.seed_note_ids))
        if not self.headers:
            lines.append("  no memories surfaced")
        for i, header in enumerate(self.headers, 1):
            lines.append(f"  {i}. {header.render()}")
        if self.more_available:
            lines.append(f"  (+{self.more_available} more beyond the budget; recall with narrower tags)")
        lines.append("  Load only what you need: load(ids).")
        return "\n".join(lines)


# Phrase matching: queries shorter than this (spacing removed) match exact tags only.
MIN_PHRASE_CHARS = 4
PHRASE_MATCH_FACTOR = 0.90
# A multi-word tag that matched nothing at all falls back to its own words
# (4+ letters, not stopwords), worth less again: "context governor" can still
# find "C2 governor core".
WORD_MATCH_FACTOR = 0.80
_STOPWORDS = {"with", "from", "into", "that", "this", "than", "then", "when", "what", "where",
              "which", "about", "after", "before", "over", "under", "between", "their", "there",
              "have", "been", "were", "will", "would", "should", "could", "also", "only", "just",
              "more", "most", "some", "such", "very", "each", "every", "other", "make", "made"}
# Fan effect: a note with many links spreads less along each one, so a hub
# (an index, a long-running thread) cannot drag hundreds of memories into
# every recall. Up to FAN_FREE_LINKS links spread at full strength; beyond
# that, each link carries sqrt(FAN_FREE_LINKS / links) of it.
FAN_FREE_LINKS = 8


def fan_factor(links: int) -> float:
    return 1.0 if links <= FAN_FREE_LINKS else math.sqrt(FAN_FREE_LINKS / links)


_SKIP_LINE = re.compile(r"^\s*(?:#|\||>|[-*_]{3,}\s*$|\*\*[^*]{1,40}:\*\*|\*\*[^*]{1,40}\*\*\s*$)")


def _one_line(body: str, limit: int = 140) -> str:
    """The first line of real prose: markdown headings, metadata lines
    ("**Date:** ..."), rules and table rows are skipped. Falls back to the
    start of the body when nothing else is left."""
    prose = []
    for line in (body or "").splitlines():
        if not line.strip() or _SKIP_LINE.match(line):
            if prose:
                break
            continue
        prose.append(re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line.strip()))
    text = " ".join(" ".join(prose).split()) or " ".join((body or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def recall(cart, tags: Iterable[str], *, budget_tokens: Optional[int] = None,
           max_headers: Optional[int] = None, categories: Optional[Sequence[str]] = None,
           include_proprietary: bool = True, seed_note_ids: Sequence[str] = ()) -> RecallResult:
    now = cart.sync_clock()
    s = cart.settings()
    threshold, fog_min, fog_level = s["surface_threshold"], s["fog_importance_min"], s["fog_level"]
    hop_decay, max_hops = s["hop_decay"], int(s["max_hops"])
    norm_tags = tuple(dict.fromkeys(fabric.normalize_tag(t) for t in tags))
    seeds = tuple(dict.fromkeys(seed_note_ids))
    result = RecallResult(tags=norm_tags, now=now, seed_note_ids=seeds)
    if not norm_tags and not seeds:
        return result

    def value(row) -> float:
        floor_value = fog_level if row["importance"] >= fog_min else 0.0
        return max(row["strength"], floor_value)

    # Direct hits. Exact tag first; then the phrase match: the query's
    # spacing-insensitive form inside a tag ("lm studio" finds "LMStudio
    # loaded state"), worth slightly less than an exact tag.
    exact: dict = {}      # note_id -> set of query tags matched exactly
    partial: dict = {}    # note_id -> set of query tags matched as a phrase
    marks = ",".join("?" * len(norm_tags))
    for row in cart.conn.execute(f"SELECT note_id, tag FROM tags WHERE tag IN ({marks})",
                                 norm_tags):
        exact.setdefault(row["note_id"], set()).add(row["tag"])
    longest = cart.conn.execute("SELECT max(length(tag)) FROM tags").fetchone()[0] or 0
    def phrase_hits(key: str) -> List[str]:
        # compact() contains only lowercase ASCII letters/digits: literal substring
        # search preserves LIKE's meaning without its pattern-size limit. Share this
        # primitive with word fallback so neither path can overflow that limit.
        if len(key) > longest:
            return []
        return [r["note_id"] for r in cart.conn.execute(
            "SELECT DISTINCT note_id FROM tags WHERE instr(fabric_compact(tag), ?) > 0", (key,))]

    words: dict = {}      # note_id -> set of query tags matched only by one of their words
    for tag in norm_tags:
        hit = any(tag in matched for matched in exact.values())
        key = compact(tag)
        if MIN_PHRASE_CHARS <= len(key):
            for note_id in phrase_hits(key):
                if tag not in exact.get(note_id, ()):
                    partial.setdefault(note_id, set()).add(tag)
                    hit = True
        if hit or " " not in tag:
            continue
        # Last resort for a multi-word tag that found nothing: its words.
        for word in tag.split():
            if len(word) >= MIN_PHRASE_CHARS and word not in _STOPWORDS:
                for note_id in phrase_hits(compact(word)):
                    words.setdefault(note_id, set()).add(tag)
    hit_ids = list(set(exact) | set(partial) | set(words))
    direct = []
    if hit_ids:
        marks = ",".join("?" * len(hit_ids))
        direct = cart.conn.execute(
            f"SELECT * FROM temp.note_state WHERE note_id IN ({marks})", hit_ids).fetchall()

    found = {}   # note_id -> dict(row, reach, via, hops, matched)
    for row in direct:
        note_id = row["note_id"]
        exact_tags, partial_tags = exact.get(note_id, set()), partial.get(note_id, set())
        word_tags = words.get(note_id, set())
        if exact_tags:
            factor, via = 1.0, f"tag:{sorted(exact_tags)[0]}"
        elif partial_tags:
            factor, via = PHRASE_MATCH_FACTOR, f"phrase:{sorted(partial_tags)[0]}"
        else:
            factor, via = WORD_MATCH_FACTOR, f"word:{sorted(word_tags)[0]}"
        reach = value(row) * factor
        if reach >= threshold:
            found[note_id] = dict(row=row, reach=reach, via=via, hops=0,
                                  matched=tuple(sorted(exact_tags | partial_tags | word_tags)))
    result.considered = len(direct)

    for nid in seeds:
        row = cart.conn.execute('SELECT * FROM temp.note_state WHERE note_id=?', (nid,)).fetchone()
        if row is None:
            raise fabric.FabricError('unknown explicit recall seed: ' + str(nid))
        if row['proprietary'] and not include_proprietary:
            raise fabric.FabricError('explicit seed is excluded by the proprietary filter')
        found[nid] = dict(row=row, reach=value(row), via='seed:' + nid, hops=0, matched=())

    frontier = list(found)
    for hop in range(1, max_hops + 1):
        if not frontier:
            break
        marks = ",".join("?" * len(frontier))
        links = cart.conn.execute(
            f"SELECT * FROM temp.link_state WHERE a IN ({marks}) OR b IN ({marks})",
            frontier + frontier).fetchall()
        degree: dict = {}
        for l in links:
            degree[l["a"]] = degree.get(l["a"], 0) + 1
            degree[l["b"]] = degree.get(l["b"], 0) + 1
        neighbour_ids = {l["b"] if l["a"] in frontier else l["a"] for l in links}
        neighbour_ids |= {l["a"] for l in links if l["b"] in frontier}
        neighbour_ids -= set(found)
        if not neighbour_ids:
            break
        marks = ",".join("?" * len(neighbour_ids))
        rows = {r["note_id"]: r for r in cart.conn.execute(
            f"SELECT * FROM temp.note_state WHERE note_id IN ({marks})", list(neighbour_ids))}
        result.considered += len(rows)
        next_frontier = []
        for l in links:
            for source, target in ((l["a"], l["b"]), (l["b"], l["a"])):
                if source not in frontier or target not in rows:
                    continue
                reach = (l["strength"] * value(rows[target]) * hop_decay ** (hop - 1)
                         * fan_factor(degree.get(source, 1)))
                if reach < threshold:
                    continue
                best = found.get(target)
                if best is None or reach > best["reach"]:
                    found[target] = dict(row=rows[target], reach=reach,
                                         via=f"link:{source}/{l['relation']}", hops=hop,
                                         matched=())
                    if target not in next_frontier:
                        next_frontier.append(target)
        frontier = next_frontier

    headers = []
    for note_id, info in found.items():
        if note_id in seeds:
            continue  # Deliberate entry points are reported separately, not promoted into ranked results.
        row = info["row"]
        if categories and row["category"] not in categories:
            continue
        if row["proprietary"] and not include_proprietary:
            continue
        score = info["reach"]
        if row["category"] == "Alert":
            score += s["alert_boost"]
        score += s["tag_match_bonus"] * max(0, len(info["matched"]) - 1)
        headers.append(Header(
            note_id=note_id, title=row["title"], category=row["category"],
            profile=row["profile"], strength=round(row["strength"], 4),
            depth=round(1.0 - row["strength"], 4), reach=round(info["reach"], 4),
            score=round(score, 4), one_line=_one_line(row["body"]), via=info["via"],
            hops=info["hops"], through_fog=row["strength"] < threshold,
            proprietary=bool(row["proprietary"]),
            recurring=row["category"] == "Alert" and row["pull_count"] >= s["recurring_pulls"],
            matched_tags=info["matched"]))
    headers.sort(key=lambda h: (-h.score, h.title))

    used = 0
    for header in headers:
        if max_headers is not None and len(result.headers) >= max_headers:
            break
        if budget_tokens is not None and used + header.tokens() > budget_tokens:
            break
        result.headers.append(header)
        used += header.tokens()
    result.more_available = len(headers) - len(result.headers)
    return result


@atomic
def load(cart, note_ids: Sequence[str], *, reinforce_links: bool = True) -> List[dict]:
    """Pull chosen notes onto the working board: full content, then refresh.

    Returns each note (body, tags, current strength). Loading refreshes the
    notes' decay clocks and, when several are loaded together, strengthens
    the links between them.
    """
    ids = list(dict.fromkeys(note_ids))
    notes = [fabric.get_note(cart, note_id) for note_id in ids]
    fabric.refresh(cart, ids)
    if reinforce_links:
        fabric.reinforce(cart, ids)
    return notes
