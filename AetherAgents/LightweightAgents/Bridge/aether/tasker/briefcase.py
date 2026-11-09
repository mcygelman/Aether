"""The creation routine (contract §4, step 5 of Michel's flow): one briefcase folder per hired staff agent.

    briefcases/<agent id>/
      README.txt                  the staff directive (Lexicon)
      Aether_Crib_Sheet.txt       the canonical Lexicon reference
      teachframes/                TeachFrames the Overseer chose, copied from the catalog by name
      directives/NN.txt           the Overseer's extra directives, verbatim
      manifest.json               sha256 per file: a record, not a gate

Never copy an identity into this package; the staff turn reads it from Staff Home.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Mapping

from . import staff as staff_home
from ._files import now, write_bytes, write_json
from .sprint import Sprint
from .tasker import Tasker
from .grants import read_directive, validate_directive

PACKAGE = Path(__file__).resolve().parent
CRIB_SHEET = PACKAGE / "assets" / "Aether_Crib_Sheet.txt"
STAFF_README = PACKAGE / "directives" / "STAFF_README.txt"


class BriefcaseError(ValueError):
    """The briefcases cannot be made as asked; the message says why. Nothing was written."""


def _catalog(catalog_path) -> dict:
    from aether.knowledge.school import DEFAULT_CATALOG_PATH, load_catalog
    return load_catalog(catalog_path or DEFAULT_CATALOG_PATH)


def _frame_name(name: str) -> str:
    name = Path(str(name).strip()).name
    name = re.sub(r"\.txt$", "", name, flags=re.IGNORECASE)
    return name[len("TEACHFRAME_"):] if name.upper().startswith("TEACHFRAME_") else name


SLUG_MAX = 20
WINDOWS_PATH_MAX = 260


def _write(path: Path, data: bytes) -> None:
    """Write one briefcase file; a path the system refuses is answered with its real cause, not "No such file"."""
    try:
        write_bytes(path, data)
    except OSError as exc:
        n = len(str(path))
        if n + 16 >= WINDOWS_PATH_MAX:        # the temporary name used while writing adds about 16 characters
            raise BriefcaseError(f"cannot write {path.name}: its path is {n} characters and Windows refuses paths over "
                                 f"{WINDOWS_PATH_MAX}. The sprint folder is too deep; the operator should use a "
                                 f"shorter data root") from exc
        raise


def _slug(text: str) -> str:
    first = next((ln for ln in text.splitlines() if re.search(r"[A-Za-z0-9]", ln)), "directive")
    # short on purpose: in M7's rerun a 40-character slug plus the temporary name pushed a path past Windows' 260
    return ("_".join(re.findall(r"[A-Za-z0-9]+", first))[:SLUG_MAX] or "directive").strip("_")


def create_briefcases(sprint: Sprint, staff_spec: Mapping[str, Mapping], *, catalog_path=None) -> dict:
    """staff_spec: {agent_id: {"teachframes": [names], "directives": [texts]}}. Every agent must be hired first.
    Everything is checked before anything is written. Returns {agent_id: manifest}."""
    if not staff_spec:
        raise BriefcaseError("name the hired staff to make briefcases for")
    prepared = {}
    for agent, spec in staff_spec.items():
        spec = dict(spec or {})
        try:
            generated = read_directive(spec.get('read_roots', []))
            directives = spec.get('directives') or []
            if not isinstance(directives, list) or not all(isinstance(d, str) and d.strip() for d in directives):
                raise ValueError('directives must be a list of non-empty texts')
            for text in directives: validate_directive(text)
            spec['directives'] = directives + ([generated] if generated else [])
        except ValueError as exc:
            raise BriefcaseError(f'{agent}: {exc}') from exc
        prepared[agent] = spec
    staff_spec = prepared
    hired = set(sprint.record().get("staff", []))
    problems, frames_needed = [], {}
    for agent_id, spec in staff_spec.items():
        spec = spec or {}
        if agent_id not in hired:
            problems.append(f"{agent_id} is not hired on this sprint; call staff_hire first")
        frames = [_frame_name(n) for n in spec.get("teachframes", []) or []]
        frames_needed[agent_id] = frames
        directives = spec.get("directives", []) or []
        if isinstance(directives, str) or not all(isinstance(d, str) and d.strip() for d in directives):
            problems.append(f"{agent_id}: directives must be a list of non-empty texts")
    catalog = {}
    if any(frames_needed.values()):
        catalog = _catalog(catalog_path)
        for agent_id, frames in frames_needed.items():
            missing = [f for f in frames if f not in catalog]
            if missing:
                problems.append(f"{agent_id}: no TeachFrame named {', '.join(missing)} in the catalog")
    if problems:
        raise BriefcaseError("; ".join(problems))

    tasker_path = sprint.root / "tasker.sqlite"
    if not tasker_path.exists():
        Tasker.create(tasker_path, sprint.id, sprint.overseer)
    readme = STAFF_README.read_bytes()
    crib = CRIB_SHEET.read_bytes()
    manifests = {}
    for agent_id, spec in staff_spec.items():
        spec = spec or {}
        folder = sprint.briefcase(agent_id)
        _write(folder / "README.txt", readme)
        _write(folder / "Aether_Crib_Sheet.txt", crib)
        for name in frames_needed[agent_id]:
            source = catalog[name].path
            _write(folder / "teachframes" / source.name, source.read_bytes())
        existing = sorted((folder / "directives").glob("*.txt")) if (folder / "directives").is_dir() else []
        bodies = {path.read_bytes() for path in existing}
        number = len(existing)
        for text in spec.get("directives", []) or []:
            body = text if text.endswith("\n") else text + "\n"
            encoded = body.encode("utf-8")
            if encoded in bodies:
                continue                       # a retried request must not duplicate its directives
            number += 1
            # numbered only (S4): a name made from the text pushed paths past Windows' 260 in two live runs
            _write(folder / "directives" / f"{number:02d}.txt", encoded)
            bodies.add(encoded)
        manifests[agent_id] = write_manifest(sprint, agent_id)
    return manifests


def write_manifest(sprint: Sprint, agent_id: str) -> dict:
    folder = sprint.briefcase(agent_id)
    files = {}
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        rel = path.relative_to(folder).as_posix()
        if rel in ("manifest.json", staff_home.CLOSED_RECORD, staff_home.BEFORE_CLOSE):
            continue
        files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {"schema": "AETHER-BRIEFCASE-2", "sprint": sprint.id, "agent": agent_id, "written": now(),
                "files": files}
    write_json(folder / "manifest.json", manifest)
    return manifest


def briefcase_files(sprint: Sprint, agent_id: str) -> dict:
    """What a staff turn loads, by part: identity, readme, crib, directives (in order), teachframes (paths)."""
    folder = sprint.briefcase(agent_id)
    ident = staff_home.home_file(sprint.data_root, agent_id)
    return {
        "identity": ident,
        "readme": folder / "README.txt",
        "crib": folder / "Aether_Crib_Sheet.txt",
        "directives": sorted((folder / "directives").glob("*.txt")) if (folder / "directives").is_dir() else [],
        # catalog frames are .txt (curriculum) or .md (constitution laws such as LAW_Execution_Evidence.md)
        "teachframes": sorted(p for p in (folder / "teachframes").iterdir() if p.is_file())
        if (folder / "teachframes").is_dir() else [],
    }
