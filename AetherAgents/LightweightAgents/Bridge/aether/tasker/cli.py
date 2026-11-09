"""The headless command line of the Tasker Process (contract §7): everything an operator, human or AI, needs.

    python -m aether.tasker staff seed   --data-root D [--source DIR]
    python -m aether.tasker staff list   --data-root D
    python -m aether.tasker mind create  --data-root D --id ID --name NAME
    python -m aether.tasker mind list    --data-root D
    python -m aether.tasker sprint new   --data-root D --goals FILE --overseer ID [--project P] [--deployment NAME]
                                          --contracts FILE [--profiles PATH] [--cap name=value ...] [--price in,out] [--no-timer]
    python -m aether.tasker sprint status PATH [--lines N]
    python -m aether.tasker sprint stop   PATH
    python -m aether.tasker sprint resume PATH
    python -m aether.tasker sprint caps   PATH name=value ...
    python -m aether.tasker sprint grade  PATH            (process faults counted from the records; see grade.py)
    python -m aether.tasker sprint verify PATH            (current checks and receipt identifiers)
    python -m aether.tasker sprint accept PATH [--reviews FILE] (bound operator decisions by task number)
    python -m aether.tasker timer run     PATH [--interval S]

Every command takes --json for a machine-readable answer. `sprint new` starts the Timer in the background (its
console output goes to timer/timer.out), and the Timer delivers HIRED to the Overseer.
Never: decide anything for a mind, change a task, or print a credential.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from . import staff as staff_home
from .sprint import DEFAULT_CAPS, Sprint, SprintError, load_sprint, new_sprint
from .timer import Timer, resume

BRIDGE = Path(__file__).resolve().parents[2]


def default_staff_source() -> Path:
    """The development identities in a checkout; the shipped standard staff in an installed app."""
    from aether.paths import LIGHTWEIGHT_ROOT
    development = Path(LIGHTWEIGHT_ROOT) / "Identities" / "Staff"
    return development if development.is_dir() else staff_home.PRODUCT_STAFF


def _cap(text: str) -> tuple:
    if "=" not in text:
        raise argparse.ArgumentTypeError(f"a cap is name=value, for example max_turns_per_sprint=200; got {text!r}")
    name, value = text.split("=", 1)
    if name not in DEFAULT_CAPS:
        raise argparse.ArgumentTypeError(f"unknown cap {name}; the caps are {', '.join(DEFAULT_CAPS)}")
    if value.lower() in ("none", "off", ""):
        return name, None
    return name, (float(value) if name == "max_cost_per_sprint" else int(value))


def overseer_runner():
    """Praxis's run_overseer_turn when it is installed; otherwise a runner that says so (a setup failure)."""
    try:
        from aether.overseer.turn import run_overseer_turn
        return run_overseer_turn
    except ImportError:
        from .sprint import TurnResult

        def missing(sprint, wake, *, client=None, stopped=None):
            return TurnResult(False, "", {}, "SetupError: the Overseer runner (aether.overseer.turn) is not installed yet")
        return missing


def deployment_limits(sprint: Sprint) -> dict:
    """Each named deployment's output ceiling and context window with their sources, and what to review: shown at
    setup, so a fallback or a low hand-written limit is seen before a sprint runs (M7's lesson)."""
    from .model import SetupError, describe_deployment
    record = sprint.record()
    path = Path(record.get("profiles_path") or (sprint.data_root / "runtime_profiles.json"))
    out = {}
    for name in sorted(set((record.get("deployments") or {}).values())):
        try:
            out[name] = describe_deployment(path, name)
        except (SetupError, OSError, ValueError) as exc:
            out[name] = {"error": str(exc)}
    return out


def limits_text(limits: dict) -> str:
    lines = []
    for name, d in limits.items():
        if "error" in d:
            lines.append(f"  {name}: {d['error']}")
            continue
        can = (f"; the model can give {d['registry_max_tokens']}" if d['max_tokens_source'] == 'budget' else "")
        lines.append(f"  {name} ({d['model']}): replies up to {d['max_tokens']} tokens ({d['max_tokens_source']}{can}), "
                     f"context {d['context_window'] or 'unknown'} ({d['window_source']})")
        lines += [f"    REVIEW: {w}" for w in d["warnings"]]
    return ("limits:\n" + "\n".join(lines) + "\n") if lines else ""


def start_timer_process(sprint: Sprint, interval: float = 3.0) -> int:
    out = (sprint.root / "timer" / "timer.out").open("ab")
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "aether.tasker", "timer", "run", str(sprint.root),
                             "--interval", str(interval)], cwd=str(BRIDGE), stdout=out, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    return proc.pid


def status(sprint: Sprint, lines: int = 12) -> dict:
    from .terminal import failure
    from .verification import evaluate
    record = sprint.record()
    tk = sprint.tasker("operator")
    from ._files import read_json
    timer = read_json(sprint.root / "timer" / "timer.json", {})
    log = sprint.root / "timer" / "timer.log"
    tail = log.read_text(encoding="utf-8").splitlines()[-lines:] if log.exists() else []
    return {
        "id": sprint.id, "path": str(sprint.root), "state": record.get("state"), "result": record.get("result"),
        "dispatch": bool(record.get("dispatch")), "paused_for": record.get("paused_for"),
        "cap_reached": record.get("cap_reached"), "overseer": sprint.overseer, "staff": record.get("staff", []),
        "counts": tk.counts(), "terminal_failure": failure(sprint),
        "verification": evaluate(sprint),
        "tasks": [dict(number=t.number, status=t.status, assigned_to=t.assigned_to, title=t.title, note=t.note,
                       depends_on=list(t.depends_on)) for t in tk.list()],
        "caps": record.get("caps", {}),
        "used": dict(turns=timer.get("turns_started", 0), tokens=timer.get("tokens_used", 0),
                     cost=round(float(timer.get("cost_used", 0.0)), 6)),
        "timer": dict(pid=timer.get("pid"), heartbeat=timer.get("heartbeat"), running=timer.get("running", [])),
        "operator_lines": record.get("operator_lines", [])[-5:],
        "log": tail,
    }


def _print_status(s: dict) -> None:
    print('  output acceptance: ' + s['verification']['status'])
    if s.get('terminal_failure'):
        print(s['terminal_failure'])
    print(f"{s['id']}  state {s['state']}  dispatch {'on' if s['dispatch'] else 'off'}"
          + (f"  PAUSED for {s['paused_for']}" if s['paused_for'] else "")
          + (f"  CAP {s['cap_reached']}" if s['cap_reached'] else ""))
    print(f"  overseer {s['overseer']} · staff {', '.join(s['staff']) or '-'} · "
          + " ".join(f"{k} {v}" for k, v in s["counts"].items()))
    for t in s["tasks"]:
        deps = ",".join(str(d) for d in t["depends_on"]) or "-"
        print(f"  T{t['number']:<3} {t['status']:<8} {t['assigned_to'] or '-':<12} deps {deps:<8} {t['title']}"
              + (f"  // {t['note']}" if t["note"] else ""))
    u, c = s["used"], s["caps"]
    print(f"  used: {u['turns']} turns (cap {c.get('max_turns_per_sprint')}), {u['tokens']} tokens, "
          f"cost {u['cost']} (cap {c.get('max_cost_per_sprint')}); timer pid {s['timer']['pid']}, "
          f"heartbeat {s['timer']['heartbeat']}, running {', '.join(s['timer']['running']) or '-'}")
    for line in s["operator_lines"]:
        print(f"  OPERATOR {line['at']} {line['text']}" + (f" [stop: {line['stop']}]" if line.get("stop") else ""))
    for line in s["log"]:
        print("  | " + line)
    if s["result"]:
        print(f"  RESULT {s['result']['result']}: {s['result']['note']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aether.tasker", description="The Aether Tasker Process, headless.")
    sub = parser.add_subparsers(dest="area", required=True)

    staff = sub.add_parser("staff").add_subparsers(dest="command", required=True)
    seed = staff.add_parser("seed")
    seed.add_argument("--data-root", required=True)
    seed.add_argument("--source")
    seed.add_argument("--json", action="store_true")
    lst = staff.add_parser("list")
    lst.add_argument("--data-root", required=True)
    lst.add_argument("--json", action="store_true")

    mind = sub.add_parser("mind").add_subparsers(dest="command", required=True)
    create = mind.add_parser("create", help="create a fresh Overseer home; existing homes are preserved")
    create.add_argument("--data-root", required=True)
    create.add_argument("--id", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--deployment")
    create.add_argument("--json", action="store_true")
    minds = mind.add_parser("list")
    minds.add_argument("--data-root", required=True)
    minds.add_argument("--json", action="store_true")

    sprint = sub.add_parser("sprint").add_subparsers(dest="command", required=True)
    new = sprint.add_parser("new")
    new.add_argument("--data-root", required=True)
    new.add_argument("--goals", required=True, help="a file holding the operator's goals")
    new.add_argument("--contracts", help="required operator-owned output checks JSON, keyed by task number (or solo)")
    new.add_argument("--overseer", required=True)
    new.add_argument("--project")
    new.add_argument("--name", help="words for the sprint id's slug (default: the goals' first line)")
    new.add_argument("--deployment", help="runtime profile for every mind (sprint.json deployments.*)")
    new.add_argument("--profiles", help="runtime_profiles.json (default: <data root>/runtime_profiles.json)")
    new.add_argument("--read-root", action="append", default=[],
                     help="existing input file or directory the Overseer may read; repeat for multiple paths")
    new.add_argument("--cap", action="append", type=_cap, default=[])
    new.add_argument("--price", help="input,output price per million tokens, for max_cost_per_sprint")
    new.add_argument("--interval", type=float, default=3.0)
    new.add_argument("--no-timer", action="store_true", help="create only; start it later with: timer run PATH")
    new.add_argument("--json", action="store_true")
    for name in ("status", "stop", "resume", "caps", "grade", "verify", "accept"):
        p = sprint.add_parser(name)
        p.add_argument("path")
        p.add_argument("--json", action="store_true")
        if name == 'accept':
            p.add_argument('--reviews', help='operator review map with verdict, reviewer and receipt_sha256')
        if name == "status":
            p.add_argument("--lines", type=int, default=12)
        if name == "caps":
            p.add_argument("caps", nargs="+", type=_cap)

    timer = sub.add_parser("timer").add_subparsers(dest="command", required=True)
    run = timer.add_parser("run")
    run.add_argument("path")
    run.add_argument("--interval", type=float, default=3.0)
    run.add_argument("--json", action="store_true")

    a = parser.parse_args(argv)
    out = {}
    try:
        if a.area == "staff" and a.command == "seed":
            added = staff_home.seed_staff(a.data_root, a.source or default_staff_source())
            out = {"added": added, "roster": [e["id"] for e in staff_home.roster(a.data_root)]}
            text = f"seeded {', '.join(added) or 'nobody new'}; the Staff Home holds {', '.join(out['roster'])}"
        elif a.area == "staff" and a.command == "list":
            out = {"staff": staff_home.roster(a.data_root)}
            text = "\n".join(f"{e['id']:<12} {e['state']:<28} {e['role']}" for e in out["staff"]) or "(empty)"
        elif a.area == "mind":
            from aether.overseer.homes import create_heavyweight_home, list_heavyweights
            if a.command == "create":
                home = create_heavyweight_home(a.data_root, a.id, a.name, deployment=a.deployment)
                out, text = {"id": a.id, "home": str(home)}, f"created {a.id} at {home}"
            else:
                out = {"minds": list_heavyweights(a.data_root)}
                text = "\n".join(f"{e['id']}: {'available' if e['available'] else 'busy'}" for e in out["minds"]) or "(empty)"
        elif a.area == "sprint" and a.command == "new":
            from .verification import validate
            from .output_checks import load
            if not a.contracts:
                raise SprintError('Declare output checks with --contracts FILE before starting a new sprint')
            contracts = validate(load(a.contracts))
            goals = Path(a.goals).read_text(encoding="utf-8")
            read_roots = [str(Path(path).resolve(strict=True)) for path in a.read_root]
            private = [Path(a.contracts).resolve(), Path(a.data_root).resolve()/'verification']
            private += [Path(item['path']).resolve() for c in contracts.values() for item in c['evidence']]
            if any(path.is_relative_to(Path(root)) for path in private for root in read_roots):
                raise SprintError('Keep operator contracts, evidence and verification storage outside agent read roots')
            from aether.overseer.homes import home_path
            if not (home_path(a.data_root, a.overseer) / 'participant.json').is_file():
                raise SprintError('Overseer home not found; use mind create or select an existing mind with mind list')
            s = new_sprint(a.data_root, goals, a.overseer, a.project, dict(a.cap), name=a.name, contracts=contracts)
            def configure(d):
                if a.deployment:
                    d["deployments"] = {"*": a.deployment}
                if a.profiles:
                    d["profiles_path"] = str(Path(a.profiles).resolve())
                if read_roots:
                    d["overseer_read_roots"] = list(dict.fromkeys(read_roots))
                if a.price:
                    pin, pout = (float(x) for x in a.price.split(","))
                    d["prices"] = {"*": {"input_per_million": pin, "output_per_million": pout}}
            s.update(configure)
            limits = deployment_limits(s)
            pid = None if a.no_timer else start_timer_process(s, a.interval)
            out = {"id": s.id, "path": str(s.root), "timer_pid": pid, "limits": limits}
            text = (f"sprint {s.id} created at {s.root}\n" + limits_text(limits)
                    + (f"Timer started (pid {pid}); it delivers HIRED to {a.overseer}. Watch: python -m aether.tasker "
                       f"sprint status \"{s.root}\"" if pid else f"Timer not started: python -m aether.tasker timer run "
                                                             f"\"{s.root}\""))
        elif a.area == "sprint":
            s = load_sprint(a.path)
            if a.command == "status":
                out = status(s, a.lines)
                if not a.json:
                    _print_status(out)
                    return 0
                text = ""
            elif a.command in ('verify', 'accept'):
                from .verification import evaluate, acceptance
                from .output_checks import load
                if a.command == 'accept' and a.reviews and Path(a.reviews).resolve().is_relative_to(s.root/'outputs'):
                    raise SprintError('Operator reviews must be outside worker outputs')
                out = (acceptance(s, load(a.reviews) if a.reviews else None) if a.command == 'accept' else evaluate(s))
                print(json.dumps(out, ensure_ascii=False, indent=2) if a.json else str(out))
                return 0 if out['status'] == 'accepted' else 2
            elif a.command == "grade":
                from .grade import grade, render
                out = grade(s.root)
                text = render(out)
            elif a.command == "stop":
                (s.root / "timer" / "STOP").write_bytes(b"")
                out, text = {"stop": True}, "STOP written; the Timer ends after the turns in flight"
            elif a.command == "resume":
                resume(s)
                out, text = {"resumed": True}, "resumed: the pause is lifted and the Timer clears its holds at its next tick"
            else:
                s.update(lambda d: d.setdefault("caps", {}).update(dict(a.caps)))
                out = {"caps": s.record()["caps"]}
                text = "caps: " + ", ".join(f"{k}={v}" for k, v in out["caps"].items())
        else:
            s = load_sprint(a.path)
            from .staff_turn import run_staff_turn
            t = Timer(s, overseer_runner=overseer_runner(), staff_runner=run_staff_turn, interval=a.interval,
                      log=None if a.json else print).start()
            why = t.run()
            out, text = {"ended": why}, f"Timer ended: {why}"
    except (SprintError, staff_home.StaffError, FileNotFoundError, FileExistsError, RuntimeError, ValueError) as exc:
        if getattr(a, "json", False):
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=2) if getattr(a, "json", False) else text)
    ended=out.get('ended') if isinstance(out,dict) else None
    return getattr(ended,'exit_code',2 if str(ended or '').startswith(('incomplete:','the Timer failed')) else 0)
