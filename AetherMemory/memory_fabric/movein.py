"""Move-in box: the agent arranges its new home before it moves in.

"We have to respect their domains." (Michel, 2026-10-06)

Inputs:  an agent's markdown memory folder, and the agent's own MOVE-IN SHEET
         (JSON, written by the agent itself during its walkthrough).
Outputs: walkthrough(): a floor plan (Markdown), a preview of the home as it
             would be (viewer HTML) and a blank sheet to fill in. Nothing in
             the profile is touched; no cartridge is kept.
         check_sheet(): every problem in a sheet at once, or a clean bill.
         move_in(): imports the agent's memory WITH its choices applied, in one
             pass, into a new cartridge, and remembers the move itself.

The sheet is the agent's, in the agent's words:
  {
    "format": "aether-move-in/1",
    "agent": "solace",                       # whose home this is
    "written_by": "solace",                  # must be the agent itself
    "note": "Why I arranged things this way.",
    "card":   {"name": "Solace", "scan_line": "One line about me."},
    "avatar": {"svg_file": "avatar.svg", "description": "In words.", "original_design": true},
    "records": {
      "INS_20260805_Configured_Is_Not_Capability": {"importance": "vital"},
      "EXP_20260725_S00076_Scoped_Edits": {"kind": "project fact", "tags_add": ["edit_file"]},
      "INT_20260720_Coming_Into_Being": {"proprietary": false, "current_until": "2027-07-20"},
      "EXP_20260813_ARA05_Leg1_Flash_Thinking_Disabled": {"bury": true}
    },
    "links":    [{"from": "INS_...", "to": "EXP_...", "relation": "causal", "strength": "strong"}],
    "remember": [{"title": "...", "body": "...", "kind": "lesson", "tags": ["..."]}]
  }
Per record: kind, category, importance (low/normal/high/vital), title,
tags_add, tags_remove, proprietary, bury, current_until | current_for_days,
note. Keys starting with "_" are comments and are ignored.

Rules that protect the agent:
  * Only the agent writes its sheet: written_by must be the agent. A sheet
    written by anyone else is refused, whoever they are.
  * Nothing is left behind or deleted (decision 13). A memory the agent
    doesn't want in view is BURIED: packed, in the basement, able to rise.
  * The whole sheet is checked before anything is written, and every problem
    is listed at once, so the agent can fix them in one pass.
  * A sheet applies once, on move-in day, to a new cartridge. Afterwards the
    agent rearranges with its memory tools, like anyone living there.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import importer, kinds, schema, staff
from .errors import MemoryFabricError
from .fabric import normalize_tag
from .tools import IMPORTANCE_WORDS, STRENGTH_WORDS, importance_value

FORMAT = "aether-move-in/1"
SHEET_NAME = "MOVE_IN.json"
_TOP = {"format", "agent", "written_by", "written_at", "note", "card", "avatar", "records",
        "links", "remember"}
_RECORD = {"kind", "category", "importance", "title", "tags_add", "tags_remove", "proprietary",
           "bury", "current_until", "current_for_days", "note"}
_REMEMBER = {"title", "body", "kind", "tags", "category", "importance", "current_until",
             "current_for_days", "proprietary", "links"}
_LINK = {"from", "to", "relation", "strength"}


class MoveInError(MemoryFabricError, ValueError):
    """The sheet can't be applied. `problems` lists every reason, for one round of fixes."""

    def __init__(self, problems: List[str]):
        self.problems = list(problems)
        super().__init__("the move-in sheet needs changes:\n- " + "\n- ".join(self.problems))


@dataclass
class MoveInReport:
    agent: str
    imported: Dict[str, int] = field(default_factory=dict)
    changed_records: int = 0
    buried: int = 0
    links_added: int = 0
    remembered: int = 0
    card_changed: bool = False
    avatar_set: bool = False
    sheet_digest: str = ""
    move_in_note_id: Optional[str] = None


# -- reading and checking a sheet ------------------------------------------------------

def load_sheet(path: str) -> dict:
    try:
        with open(path, encoding="utf-8-sig") as handle:
            sheet = json.load(handle)
    except (OSError, UnicodeDecodeError) as exc:
        raise MoveInError([f"cannot read the sheet {path}: {exc}"]) from exc
    except json.JSONDecodeError as exc:
        raise MoveInError([f"the sheet is not valid JSON (line {exc.lineno}, column {exc.colno}): "
                           f"{exc.msg}"]) from exc
    if isinstance(sheet, dict) and isinstance(sheet.get("avatar"), dict) \
            and sheet["avatar"].get("svg_file"):
        svg_path = os.path.join(os.path.dirname(os.path.abspath(path)), sheet["avatar"]["svg_file"])
        try:
            with open(svg_path, encoding="utf-8") as handle:
                sheet["avatar"] = {**sheet["avatar"], "svg": handle.read()}
        except OSError as exc:
            raise MoveInError([f"avatar.svg_file: cannot read {svg_path}: {exc}"]) from exc
    return sheet


def _visible(d: dict) -> dict:
    return {k: v for k, v in d.items() if not str(k).startswith("_")}


def _same(a: str, b: str) -> bool:
    return isinstance(a, str) and isinstance(b, str) and a.strip().casefold() == b.strip().casefold()


def _date(value, where: str, problems: List[str]) -> Optional[str]:
    try:
        return _dt.date.fromisoformat(str(value)[:10]).isoformat() + "T00:00:00Z"
    except ValueError:
        problems.append(f"{where}: a date like 2027-01-31, not {value!r}")
        return None


def _days(value, where: str, problems: List[str]) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or not 0 <= value <= 36500:
        problems.append(f"{where}: a number of days between 0 and 36500")


def _words(value, where: str, problems: List[str]) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        problems.append(f"{where}: a list of words")
        return []
    return [normalize_tag(v) for v in value]


def _check_memory_fields(entry: dict, where: str, problems: List[str], profile_names) -> None:
    if "kind" in entry:
        try:
            kinds.resolve(str(entry["kind"]), profile_names)
        except MemoryFabricError as exc:
            problems.append(f"{where}.kind: {exc}")
    if "category" in entry and entry["category"] not in schema.CATEGORIES:
        problems.append(f"{where}.category: one of {list(schema.CATEGORIES)}")
    if "importance" in entry and entry["importance"] not in IMPORTANCE_WORDS:
        problems.append(f"{where}.importance: one of {list(IMPORTANCE_WORDS)}")
    if "proprietary" in entry and not isinstance(entry["proprietary"], bool):
        problems.append(f"{where}.proprietary: true or false")
    if "current_until" in entry and "current_for_days" in entry:
        problems.append(f"{where}: give current_until or current_for_days, not both")
    if "current_until" in entry:
        _date(entry["current_until"], f"{where}.current_until", problems)
    if "current_for_days" in entry:
        _days(entry["current_for_days"], f"{where}.current_for_days", problems)


def _check_link(link, where: str, problems: List[str], known) -> None:
    if not isinstance(link, dict):
        problems.append(f"{where}: an object like {{'from': id, 'to': id}}")
        return
    link = _visible(link)
    for key in sorted(set(link) - _LINK):
        problems.append(f"{where}: unknown field '{key}' (allowed: {sorted(_LINK)})")
    for end in ("from", "to"):
        if end in link and link[end] not in known:
            problems.append(f"{where}.{end}: no record '{link[end]}' in this memory")
    if link.get("relation", "context") not in schema.RELATIONS:
        problems.append(f"{where}.relation: one of {list(schema.RELATIONS)}")
    if link.get("strength") not in (None, *STRENGTH_WORDS):
        problems.append(f"{where}.strength: one of {list(STRENGTH_WORDS)}")


def check_sheet(sheet, plan: "importer.ImportPlan", agent_id: str) -> List[str]:
    """Every problem with the sheet, or [] when it can be applied as written."""
    if not isinstance(sheet, dict):
        return ["the sheet must be a JSON object"]
    problems: List[str] = []
    sheet = _visible(sheet)
    for key in sorted(set(sheet) - _TOP):
        problems.append(f"unknown field '{key}' (allowed: {sorted(_TOP)})")
    if sheet.get("format") != FORMAT:
        problems.append(f'format: "{FORMAT}"')
    if not _same(sheet.get("agent", ""), agent_id):
        problems.append(f"agent: this sheet is for '{sheet.get('agent')}', not '{agent_id}'")
    if not _same(sheet.get("written_by", ""), agent_id):
        problems.append(f"written_by: only {agent_id} can arrange {agent_id}'s home "
                        f"(the sheet says '{sheet.get('written_by')}')")
    if "note" in sheet and not isinstance(sheet["note"], str):
        problems.append("note: text")

    profile_names = [p[0] for p in schema.DEFAULT_PROFILES]
    known = {r.record_id for r in plan.records}
    records = sheet.get("records", {})
    if not isinstance(records, dict):
        problems.append("records: an object keyed by record id")
        records = {}
    for record_id, change in records.items():
        if str(record_id).startswith("_"):
            continue
        where = f"records.{record_id}"
        if record_id not in known:
            problems.append(f"{where}: no such record in this memory")
            continue
        if not isinstance(change, dict):
            problems.append(f"{where}: an object of changes")
            continue
        change = _visible(change)
        for key in sorted(set(change) - _RECORD):
            problems.append(f"{where}: unknown field '{key}' (allowed: {sorted(_RECORD)})")
        _check_memory_fields(change, where, problems, profile_names)
        if "title" in change and (not isinstance(change["title"], str)
                                  or not change["title"].strip() or len(change["title"]) > 200):
            problems.append(f"{where}.title: 1 to 200 characters")
        for key in ("tags_add", "tags_remove"):
            if key in change:
                _words(change[key], f"{where}.{key}", problems)
        if "bury" in change and not isinstance(change["bury"], bool):
            problems.append(f"{where}.bury: true or false")
        if change.get("bury") and change.get("importance") in ("high", "vital"):
            problems.append(f"{where}: buried and {change['importance']} at once; choose one")
        if "note" in change and not isinstance(change["note"], str):
            problems.append(f"{where}.note: text")

    links = sheet.get("links", [])
    if not isinstance(links, list):
        problems.append("links: a list")
        links = []
    for i, link in enumerate(links):
        _check_link(link, f"links[{i}]", problems, known)

    remember = sheet.get("remember", [])
    if not isinstance(remember, list):
        problems.append("remember: a list")
        remember = []
    for i, entry in enumerate(remember):
        where = f"remember[{i}]"
        if not isinstance(entry, dict):
            problems.append(f"{where}: an object")
            continue
        entry = _visible(entry)
        for key in sorted(set(entry) - _REMEMBER):
            problems.append(f"{where}: unknown field '{key}' (allowed: {sorted(_REMEMBER)})")
        for key in ("title", "body", "kind"):
            if not isinstance(entry.get(key), str) or not entry.get(key, "").strip():
                problems.append(f"{where}.{key}: required text")
        _check_memory_fields(entry, where, problems, profile_names)
        if "tags" in entry:
            _words(entry["tags"], f"{where}.tags", problems)
        entry_links = entry.get("links", [])
        if not isinstance(entry_links, list):
            problems.append(f"{where}.links: a list")
            entry_links = []
        for j, link in enumerate(entry_links):
            spot = f"{where}.links[{j}]"
            if not isinstance(link, dict) or "to" not in link \
                    or set(_visible(link)) - {"to", "relation", "strength"}:
                problems.append(f"{spot}: an object with to (and optionally relation, strength)")
                continue
            if link["to"] not in known:
                problems.append(f"{spot}.to: no record '{link['to']}' in this memory")
            if link.get("relation", "context") not in schema.RELATIONS:
                problems.append(f"{spot}.relation: one of {list(schema.RELATIONS)}")
            if link.get("strength") not in (None, *STRENGTH_WORDS):
                problems.append(f"{spot}.strength: one of {list(STRENGTH_WORDS)}")

    card = sheet.get("card")
    if card is not None:
        if not isinstance(card, dict) or set(_visible(card)) - {"name", "scan_line"}:
            problems.append("card: an object with name and/or scan_line")
        else:
            for key, check in (("name", staff.check_name), ("scan_line", staff.check_scan_line)):
                if key in card:
                    try:
                        check(card[key])
                    except MemoryFabricError as exc:
                        problems.append(f"card.{key}: {exc}")
    avatar = sheet.get("avatar")
    if avatar is not None:
        if not isinstance(avatar, dict):
            problems.append("avatar: an object with svg_file (or svg), description, original_design")
        else:
            try:
                staff.check_avatar(avatar.get("svg"), avatar.get("description"),
                                   avatar.get("original_design") is True)
            except MemoryFabricError as exc:
                problems.append(f"avatar: {exc}")
    return problems


def sheet_digest(sheet: dict) -> str:
    return hashlib.sha256(json.dumps(sheet, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


# -- applying a sheet --------------------------------------------------------------------

def _apply_to_plan(plan, sheet: dict, fog_min: float) -> int:
    by_id = {r.record_id: r for r in plan.records}
    changed = 0
    for record_id, change in _visible(sheet.get("records", {})).items():
        record, change = by_id[record_id], _visible(change)
        if "kind" in change:
            record.kind = change["kind"]
        if "category" in change:
            record.category = change["category"]
        if "importance" in change:
            record.importance = importance_value(change["importance"], fog_min)
        if "title" in change:
            record.title = change["title"].strip()
        if "tags_add" in change or "tags_remove" in change:
            removed = {normalize_tag(t) for t in change.get("tags_remove", [])}
            tags = [t for t in (normalize_tag(t) for t in record.tags) if t not in removed]
            tags += [t for t in (normalize_tag(t) for t in change.get("tags_add", []))
                     if t not in tags]
            record.tags = tags
        if "proprietary" in change:
            record.proprietary = change["proprietary"]
        if "current_until" in change:
            record.switch_at = _dt.date.fromisoformat(str(change["current_until"])[:10]) \
                .isoformat() + "T00:00:00Z"
        changed += 1
    return changed


def _switch_kwargs(entry: dict) -> dict:
    if "current_until" in entry:
        return {"switch_at": str(entry["current_until"])[:10]}
    if "current_for_days" in entry:
        return {"switch_in_days": float(entry["current_for_days"])}
    return {}


def move_in(engine, memory_dir: str, sheet: dict, *,
            grace_days: float = importer.DEFAULT_GRACE_DAYS) -> MoveInReport:
    """Import the agent's memory into this (new) cartridge with its own choices applied."""
    plan = importer.plan_import(memory_dir)
    problems = check_sheet(sheet, plan, engine.agent_id)
    cart = engine.cart
    digest = sheet_digest(sheet)
    done = cart.conn.execute("SELECT value FROM meta WHERE key = 'move_in_sheet'").fetchone()
    if done:
        problems.append("this agent has already moved in; rearrange with the memory tools now"
                        if done["value"] != digest else
                        "this sheet was already applied; nothing to do")
    # Supplied orientation resources do not mean the owner has already moved in.
    if cart.conn.execute("SELECT count(*) FROM notes WHERE note_id NOT IN "
                         "(SELECT guide_id FROM guides WHERE builtin_key IS NOT NULL UNION "
                         "SELECT teachframe_id FROM teachframes WHERE builtin_key IS NOT NULL)").fetchone()[0]:
        problems.append("move-in sheets apply on move-in day, to a new, empty cartridge")
    if problems:
        raise MoveInError(problems)

    fog_min = float(engine.settings()["fog_importance_min"])
    report = MoveInReport(agent=engine.agent_id, sheet_digest=digest)
    report.changed_records = _apply_to_plan(plan, sheet, fog_min)
    report.imported = importer.apply_import(engine, plan, grace_days=grace_days)

    records = _visible(sheet.get("records", {}))
    for record_id, change in records.items():
        change = _visible(change)
        if "current_for_days" in change:
            engine.set_switch(record_id, in_days=float(change["current_for_days"]))
        if change.get("bury"):
            engine.bury(record_id)
            report.buried += 1
    for link in sheet.get("links", []):
        link = _visible(link)
        engine.link(link["from"], link["to"], link.get("relation", "context"),
                    STRENGTH_WORDS.get(link.get("strength") or "normal"))
        report.links_added += 1
    for entry in sheet.get("remember", []):
        entry = _visible(entry)
        links = [(l["to"], l.get("relation", "context"),
                  STRENGTH_WORDS.get(l.get("strength") or "normal") or
                  engine.settings()["default_link_strength"])
                 for l in (_visible(x) for x in entry.get("links", []))]
        engine.remember(entry["title"], entry["body"], entry["kind"], entry.get("tags", []),
                        category=entry.get("category", "Information"),
                        importance=importance_value(entry.get("importance"), fog_min),
                        proprietary=entry.get("proprietary", False), links=links,
                        **_switch_kwargs(entry))
        report.remembered += 1
    card = _visible(sheet.get("card") or {})
    if "name" in card:
        cart.set_card_value("name", staff.check_name(card["name"]))
        report.card_changed = True
    if "scan_line" in card:
        engine.set_scan_line(card["scan_line"])
        report.card_changed = True
    avatar = sheet.get("avatar")
    if avatar:
        engine.set_avatar(avatar["svg"], avatar["description"], avatar["original_design"] is True)
        report.avatar_set = True

    # The agent remembers moving in, and what it chose.
    note = (sheet.get("note") or "").strip()
    body = (f"I moved into this cartridge on {engine.cart.clock.now()[:10]}. "
            f"{report.imported.get('written', 0)} memories came with me; I arranged "
            f"{report.changed_records} of them, buried {report.buried}, added "
            f"{report.links_added} links and {report.remembered} new memories."
            + (f"\n\nIn my words: {note}" if note else ""))
    report.move_in_note_id = engine.remember(
        "Moving in", body, "standing lesson", ["move-in", "home", "my memory"],
        category="Reflection", stratum="Experience", source_ref="move-in sheet")
    with cart.transaction() as conn:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('move_in_sheet', ?)",
                     (digest,))
    return report


# -- the walkthrough -----------------------------------------------------------------------

def blank_sheet(agent_id: str) -> dict:
    return {
        "format": FORMAT,
        "agent": agent_id,
        "written_by": agent_id,
        "note": "",
        "_how": ("Fill in only what you want to change; everything else moves in as the floor "
                 "plan shows. Keys starting with _ are comments. Nothing is ever left behind: "
                 "bury what you don't want in view."),
        "_record_fields": sorted(_RECORD),
        "_importance_words": list(IMPORTANCE_WORDS),
        "_kinds": [aliases[0] for aliases in kinds.ALIASES.values()],
        "records": {},
        "links": [],
        "remember": [],
    }


def _importance_word(value: Optional[float]) -> str:
    if value is None:
        return "normal (kind default)"
    for word in ("low", "high"):
        if abs(value - importance_value(word, 0.90)) < 1e-9:
            return word
    if value >= 0.90:
        return "vital"
    return f"{value:.2f}"


def floor_plan(plan: "importer.ImportPlan", agent_id: str, name: Optional[str] = None) -> str:
    lines = [f"# Floor plan: {name or agent_id}'s memory as it would move in", "",
             f"Source (read-only): `{plan.source}`", "",
             "Everything below moves in unless your sheet says otherwise. Nothing is left "
             "behind; you can bury what you don't want in view.", "",
             "| Record | Title | Kind | Category | Importance | Links | Tags |",
             "|---|---|---|---|---|---|---|"]
    for r in plan.records:
        importance = _importance_word(r.importance)
        tags = ", ".join(r.tags[:6]) + (" …" if len(r.tags) > 6 else "")
        cell = lambda t: str(t).replace("|", "/")
        lines.append(f"| `{r.record_id}` | {cell(r.title)} | {r.kind} | {r.category} | "
                     f"{importance} | {len(r.links)} | {cell(tags)} |")
    if plan.skipped:
        lines += ["", "Not moved (and why):", ""]
        lines += [f"- `{path}`: {reason}" for path, reason in plan.skipped]
    return "\n".join(lines) + "\n"


def walkthrough(memory_dir: str, agent_id: str, out_dir: str, sheet: Optional[dict] = None,
                *, name: Optional[str] = None, clock=None) -> dict:
    """Write floor_plan.md, home_preview.html and (if absent) MOVE_IN.json to out_dir.

    With a sheet, the preview shows the home WITH the agent's choices applied, so the agent
    can edit, look again, and repeat until it's right. Nothing is kept but these three files.
    """
    from . import viewer
    from .engine import MemoryEngine
    importer.check_destination(memory_dir, os.path.join(out_dir, "x"))
    plan = importer.plan_import(memory_dir)
    os.makedirs(out_dir, exist_ok=True)
    result = {"floor_plan": os.path.join(out_dir, "floor_plan.md"),
              "preview": os.path.join(out_dir, "home_preview.html"),
              "sheet": os.path.join(out_dir, SHEET_NAME), "problems": []}
    with open(result["floor_plan"], "w", encoding="utf-8", newline="\n") as handle:
        handle.write(floor_plan(plan, agent_id, name))
    if not os.path.exists(result["sheet"]):
        with open(result["sheet"], "w", encoding="utf-8", newline="\n") as handle:
            json.dump(blank_sheet(agent_id), handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    with tempfile.TemporaryDirectory() as scratch:
        mind = MemoryEngine.create(os.path.join(scratch, "preview.sqlite"), agent_id,
                                   name=name or agent_id, clock=clock)
        try:
            if sheet is not None:
                try:
                    move_in(mind, memory_dir, sheet)
                except MoveInError as exc:
                    result["problems"] = exc.problems
                    importer.apply_import(mind, plan)
            else:
                importer.apply_import(mind, plan)
            viewer.write_html(mind, result["preview"])
        finally:
            mind.close()
    return result
