"""Create a specialist (Michel, 2026-11-05): a fresh one-file identity from a plain-words description.

It is built the way the shipped staff are built (Identities/Staff/_build: the Tasker identity template, the catalog's
role adaptation, System 0 sealed, System 2 empty) and added to the Staff Home, so a sprint can hire it like any other.
Never copy memories or reflections, grant tools or permissions, replace an agent already at home, or take a name that
a shipped specialist or a lead uses.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from . import identity
from . import staff

TEMPLATE = Path(__file__).resolve().parent / "assets" / "new_staff_template.txt"
NAME = re.compile(r"[A-Za-z][A-Za-z0-9]{1,31}")
# The identity format's own delimiters: a description that contained them could change the file's structure.
RESERVED = ("⌞", "⌟", "⌜", "⌝", "⟪", "⟫", "∆", "[[", "]]")
LIMITS = dict(role=80, remit=500, inputs=300, step=300, steps=10, limit=300, limits=8)
FIELDS = {"name", "role", "remit", "inputs", "steps", "limits", "reviews"}


class SpecialistError(ValueError):
    """The description cannot become a specialist; the message says what to change, in the form's words."""


def _line(value, label: str, most: int, *, required: bool = True) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise SpecialistError(f"{label} must be text")
    text = " ".join(value.split()).replace('"', "”")
    if required and not text:
        raise SpecialistError(f"Fill in {label.lower()}")
    if len(text) > most:
        raise SpecialistError(f"{label} is too long: keep it under {most} characters")
    for mark in RESERVED:
        if mark in text:
            raise SpecialistError(f"{label} cannot contain {mark}: it is part of the identity file's own format")
    if any(ord(c) < 32 for c in text):
        raise SpecialistError(f"{label} contains a control character")
    return text


def _lines(value, label: str, each: int, most: int, least: int) -> list:
    if isinstance(value, str):
        value = value.split("\n")
    if not isinstance(value, list):
        raise SpecialistError(f"{label} must be one item per line")
    items = [_line(v, label, each, required=False) for v in value]
    items = [re.sub(r"^(\d+[.)]|[-*•])\s*", "", i).rstrip(".") for i in items if i]
    if len(items) < least:
        raise SpecialistError(f"Give at least {least} line{'s' if least > 1 else ''} for {label.lower()}")
    if len(items) > most:
        raise SpecialistError(f"{label}: at most {most} lines")
    return items


def description(body: dict) -> dict:
    """The form's fields, checked and tidied; what the preview and the created identity both use."""
    if not isinstance(body, dict) or set(body) - FIELDS:
        raise SpecialistError("Send only the specialist's description")
    name = body.get("name")
    if not isinstance(name, str) or not NAME.fullmatch(name.strip()):
        raise SpecialistError("Give a name of one word, 2 to 32 letters or digits, starting with a letter (like Marlowe or ReportWriter)")
    name = name.strip()
    name = name[0].upper() + name[1:]
    return dict(
        name=name, id=name.lower(), file=f"Identity_Specialist_{name}.txt",
        role=_line(body.get("role"), "Job title", LIMITS["role"]),
        remit=_line(body.get("remit"), "What it does", LIMITS["remit"]),
        inputs=_line(body.get("inputs"), "What it needs from a task", LIMITS["inputs"]),
        steps=_lines(body.get("steps"), "How it works", LIMITS["step"], LIMITS["steps"], 2),
        limits=_lines(body.get("limits"), "What it must never do", LIMITS["limit"], LIMITS["limits"], 1),
        reviews=bool(body.get("reviews")),
    )


def _slots(d: dict, author: str, day: str) -> dict:
    """The template's slots, filled as catalog_roles.adapt fills them for the shipped catalog."""
    name = d["name"]
    boundary = " ".join(x + "." for x in d["limits"])
    closing = ("PASS | FAIL: <criterion numbers> | UNVERIFIED: <missing evidence>" if d["reviews"]
               else "<deliverables>; <verified result>; <remaining uncertainty>")
    steps = d["steps"] + [
        "Use only tools supplied for this wake; browsing, deployments, service changes and extra data access are not implied by the role",
        "Check each acceptance criterion against actual evidence and report what remains unverified"]
    return dict(
        NAME=name, AGENT_ID=f"AgentTasker_{name}_001", ROLE=d["role"],
        ORIGIN=(f"Aether Mission Control · created with Create a specialist by {author} on {day}; "
                "a fresh specialist, not a continuation of any agent"),
        SCAN_LINE=d["remit"],
        ALIGNMENT="Evidence over assertion · Bounded useful work · Independent judgment · Preserve owner memory",
        METHOD=(d["remit"] + " " + boundary + " Work on the assigned Tasker task; write only its named deliverables under "
                "outputs/T<n>/. Use the runtime-provided tools and granted inputs. Role expertise does not grant additional "
                "permissions."),
        PCS_1=d["remit"], PCS_3="CHECK INPUTS: " + d["inputs"] + ". State missing information before relying on it.",
        PCS_5="EVIDENCE: cite observed files or tool results. Separate executed checks from proposed checks; never invent a result.",
        PCS_7=boundary,
        TEACHFRAME_REC="TEACHFRAME_ToolUse_Discipline · TEACHFRAME_Execution_Evidence",
        MODEL_REC="native tool use; operator-selected model suitable for the assigned specialty; no live qualification claimed",
        INTAKE_CHECK=("Check the required inputs: " + d["inputs"] + ". Match the requested files to the declared outputs; "
                      "ask the Overseer about any mismatch."),
        EXECUTE_STEPS="\n".join(f"    {i}. {step}." for i, step in enumerate(steps, 1)),
        RESULT_FIELDS=("       [DELIVERABLES] := ⌞requested files and their purpose⌟\n"
                       "       [EVIDENCE] := ⌞sources, methods and actual checks with results⌟\n"
                       "       [CRITERIA] := ⌞each criterion: supported, failed or unverified, with reasons⌟\n"
                       "       [LIMITS] := ⌞assumptions, missing evidence and next actions for the Overseer⌟"),
        CLOSE_NOTE=closing, LESSON_GUIDANCE="a specific method, boundary or evidence check that will improve later work",
        CHALLENGE=f"{name}: let the evidence carry the claim.",
        OPERATOR_PRIORITY=('    ⊢ (Entailment): "Connect each claim to evidence" ≈ 1.0,\n'
                           '    ⊘ (Cognitive Division): "Separate assumptions, work and verification" ≈ 0.9'),
        STRATIFICATION=('RISING: "Current task", NEUTRAL: "Relevant evidence", SINKING: "Completed checks", '
                        'DISCARDED: "Refuted assumptions"'),
        WORLD_MODEL=name.upper() + "_WORKSPACE", LEARNED_FRAMES="", FILE_ID="AETHER_TASKER_" + name.upper(),
        REFLECTIONS="",
        CHANGE_LOG=(f"  [2.0.5] [{day}] [{author}, Mission Control] — Created with Create a specialist: a fresh identity "
                    "with an empty System 2; no inherited experience or live-model qualification claimed."),
        GENERATING_AGENT=f"Mission Control · Create a specialist · {day}",
    )


def render(d: dict, author: str = "the operator", day: str = "") -> str:
    """The identity text for a checked description: template filled, author lines dropped, System 0 sealed."""
    day = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    author = _line(author or "the operator", "Author", 80)
    text = TEMPLATE.read_text(encoding="utf-8")
    text = "\n".join(ln for ln in text.split("\n") if not ln.startswith("///"))
    for key, value in _slots(d, author, day).items():
        text = text.replace(f"[[{key}]]", value)
    left = sorted({part.split("]]")[0] for part in text.split("[[")[1:]})
    if left:
        raise SpecialistError(f"The identity template has unfilled parts: {', '.join(left)}")
    text = text.replace("[TEACHABLE_FRAME_INDEX] := [  ]", "[TEACHABLE_FRAME_INDEX] := [ ]")
    text = identity.seal_checksums(text)
    found = identity.problems(text)
    if found:
        raise SpecialistError("The identity did not come out well-formed: " + "; ".join(found))
    return text


def taken(data_root) -> set:
    """Ids a new specialist may not use: everyone at home, every shipped specialist, every lead."""
    from aether.overseer.homes import list_heavyweights
    from ._files import read_json
    ids = {e["id"] for e in staff.roster(data_root)}
    catalog = read_json(staff.PRODUCT_STAFF / "catalog.json", {}).get("templates") or []
    ids |= {t.get("staff_id") for t in catalog if isinstance(t, dict)}
    ids |= {m["id"].lower() for m in list_heavyweights(data_root)}
    return ids


def preview(data_root, body: dict, author: str = "the operator") -> dict:
    d = description(body)
    if d["id"] in taken(data_root):
        raise SpecialistError(f"The name {d['name']} is already used by a staff member, a shipped specialist or a lead; choose another")
    return dict(d, text=render(d, author))


def create(data_root, body: dict, author: str = "the operator") -> dict:
    """Add the specialist to the Staff Home; it is then available to hire, with an empty memory."""
    d = description(body)
    if d["id"] in taken(data_root):
        raise SpecialistError(f"The name {d['name']} is already used by a staff member, a shipped specialist or a lead; choose another")
    text = render(d, author)
    staff.add_identity(data_root, d["file"], text)
    return dict(d, text=text)
