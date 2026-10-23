"""Aether v0.1 Acceptance Runner and Matrix Tool.

Provides clean workspace preparation, evaluator execution, and structured
acceptance matrix recording for the Aether Atelier v0.1 release.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
from typing import Any, Dict

ACCEPTANCE_DIR = Path(__file__).resolve().parent
FIXTURE_DIR = ACCEPTANCE_DIR / "fixture"
AGENT_INPUTS_DIR = FIXTURE_DIR / "agent_inputs"
EVALUATOR_DIR = FIXTURE_DIR / "evaluator"


def compute_sha256(path: Path) -> str:
    """Compute hex SHA-256 digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def generate_manifest() -> Dict[str, str]:
    """Generate manifest of all fixture files."""
    files: Dict[str, str] = {}
    for p in FIXTURE_DIR.rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts:
            rel = p.relative_to(ACCEPTANCE_DIR).as_posix()
            files[rel] = compute_sha256(p)
    return files


def prepare_workspace(project: str, destination: Path) -> Dict[str, Any]:
    """Copy only agent inputs into a fresh destination workspace."""
    project = project.upper()
    if project not in ("A", "B"):
        raise ValueError(f"Invalid project '{project}'. Must be 'A' or 'B'.")

    src = AGENT_INPUTS_DIR / f"project_{project.lower()}"
    if not src.exists():
        raise FileNotFoundError(f"Agent inputs for Project {project} not found at {src}")

    dest = Path(destination).resolve()
    if dest.exists() and any(dest.iterdir()):
        raise ValueError(f"Destination {dest} already exists and is not empty.")

    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, dirs_exist_ok=True)

    repo_dir = dest / "repository"
    return {
        "project": project,
        "fixture": "harbor-ledger-v01",
        "workspace": str(dest),
        "repository": str(repo_dir),
    }


def prepare_minds(destination: Path, share: Path, deployment: str, roles=None) -> Dict[str, Any]:
    """Author fresh public demonstration homes; never copy a resident cartridge."""
    from aether.atelier.participants import Participant
    roles = roles or ['lead', 'author', 'reviewer']
    if not deployment.strip() or any(r not in ('lead', 'author', 'reviewer', 'lone_master') for r in roles):
        raise ValueError('Choose a deployment name and the supplied public roles')
    destination, share = Path(destination).resolve(), Path(share).resolve()
    if destination.exists():
        raise FileExistsError('Choose a new directory for the demonstration minds')
    result = []
    for role in roles:
        template = ACCEPTANCE_DIR/'minds'/role
        spec = json.loads((template/'participant.json').read_bytes())
        home = destination/role
        mind = Participant.author(home, id=spec['id'], kind=spec['kind'], name=spec['name'],
            system=(template/'home/identity/System_0.txt').read_text(encoding='utf-8'),
            deployment=deployment, roots=spec['roots'], mailboxes=share)
        mind.tags = spec['tags']
        mind.save()
        (home/'workspace/SESSION_CURRENT.txt').write_text(
            (template/'home/workspace/SESSION_CURRENT.txt').read_text(encoding='utf-8'), encoding='utf-8')
        result.append(dict(role=role, id=mind.id, home=str(home)))
    return dict(homes=result, share=str(share), deployment=deployment, model_calls=0)


def grade_repository(project: str, repository: Path) -> Dict[str, Any]:
    """Run deterministic evaluator checks against the candidate repository."""
    project = project.upper()
    if project not in ("A", "B"):
        raise ValueError(f"Invalid project '{project}'. Must be 'A' or 'B'.")

    repo_path = Path(repository).resolve()
    if not repo_path.exists():
        raise FileNotFoundError(f"Repository not found at {repo_path}")

    grade_script = EVALUATOR_DIR / "grade.py"
    if not grade_script.exists():
        raise FileNotFoundError(f"Grader script not found at {grade_script}")

    spec = importlib.util.spec_from_file_location("evaluator_grade", grade_script)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load grader from {grade_script}")

    grader_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(grader_mod)

    return grader_mod.grade(project, repo_path)


def init_matrix(
    candidate_version: str = "v0.1.0-alpha",
    execution_layer: str = "offline",
    machine_id: str = "local",
    out_path: Path | None = None,
) -> Dict[str, Any]:
    """Initialize a fresh acceptance matrix from template."""
    tmpl_path = ACCEPTANCE_DIR / "matrix_template.json"
    data = json.loads(tmpl_path.read_text(encoding="utf-8"))

    from datetime import datetime, timezone

    data["created_at"] = datetime.now(timezone.utc).isoformat()
    data["candidate"]["version"] = candidate_version
    data["environment"]["execution_layer"] = execution_layer
    data["environment"]["machine_id"] = machine_id
    data["environment"]["python_version"] = sys.version.split()[0]

    if out_path:
        out_p = Path(out_path).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")

    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Aether v0.1 Acceptance Runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # prepare
    p_prep = subparsers.add_parser("prepare-workspace", help="Prepare workspace for Project A or B")
    p_prep.add_argument("--project", required=True, choices=["A", "B"])
    p_prep.add_argument("--dest", required=True, type=Path)

    p_minds = subparsers.add_parser('prepare-minds', help='Author new public demonstration homes')
    p_minds.add_argument('--dest', required=True, type=Path)
    p_minds.add_argument('--share', required=True, type=Path)
    p_minds.add_argument('--deployment', required=True)
    p_minds.add_argument('--roles', nargs='+', choices=['lead', 'author', 'reviewer', 'lone_master'])

    # grade
    p_grade = subparsers.add_parser("grade", help="Run evaluator against repository")
    p_grade.add_argument("--project", required=True, choices=["A", "B"])
    p_grade.add_argument("--repo", required=True, type=Path)

    # matrix-init
    p_mat = subparsers.add_parser("matrix-init", help="Initialize acceptance matrix")
    p_mat.add_argument("--out", required=True, type=Path)
    p_mat.add_argument("--version", default="v0.1.0-alpha")
    p_mat.add_argument("--layer", default="offline")
    p_mat.add_argument("--machine", default="local")

    # manifest
    subparsers.add_parser("manifest", help="Print SHA-256 fixture manifest")

    args = parser.parse_args()

    if args.command == 'prepare-minds':
        print(json.dumps(prepare_minds(args.dest, args.share, args.deployment, args.roles), indent=2))
        return 0
    if args.command == "prepare-workspace":
        res = prepare_workspace(args.project, args.dest)
        print(json.dumps(res, indent=2))
        return 0
    elif args.command == "grade":
        res = grade_repository(args.project, args.repo)
        print(json.dumps(res, indent=2))
        return 0
    elif args.command == "matrix-init":
        init_matrix(
            candidate_version=args.version,
            execution_layer=args.layer,
            machine_id=args.machine,
            out_path=args.out,
        )
        print(f"Initialized matrix at {args.out}")
        return 0
    elif args.command == "manifest":
        manifest = generate_manifest()
        print(json.dumps(manifest, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
