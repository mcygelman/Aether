"""Command line: python -m memory_fabric <command>

  simulate [scenarios_dir]     run the calibration bench (exit 1 if anything fails)
  demo [cartridge_path]        build the upstairs-door example and show recall + card
  inspect <cartridge_path>     open report, counts, strongest memories
  card <cartridge_path>        trading card as JSON
  resume <cartridge_path>      live résumé as Markdown
  view <cartridge_path> [out.html] [--no-proprietary] [--no-bodies]
                               write the read-only memory viewer page
  walkthrough <memory_dir> <agent_id> <out_dir> [--sheet MOVE_IN.json] [--name NAME]
                               floor plan + preview of the home + a blank move-in sheet
  import-profile <memory_dir> <agent_id> <out.sqlite> [--dry-run] [--grace-days N] [--name NAME]
                 [--move-in MOVE_IN.json]
                               migrate an agent's markdown memory into a new cartridge,
                               with the agent's own move-in sheet applied if given
                               (reads the profile only; never writes into it)
  archive <legacy_folder> <agent_id> <out.sqlite> --frozen-at YYYY-MM-DD [--name N] [--dry-run]
                               frozen, read-only reference archive of a mind in an older
                               structure (reads the folder only; never writes into it)
  migrate <cartridge_path>     bring a cartridge to this engine's schema (backup first)
  backup <cartridge_path> <destination>
                               consistent full copy of a mind (read-only on the source)
  dashboard <cartridge_path> [--port N] [--session-id ID]
                               local read-only Welcome Dashboard with refresh
"""
from __future__ import annotations

import json
import os
import sys

from . import simulator
from .clock import FixedClock
from .engine import MemoryEngine
from .errors import MemoryFabricError

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SCENARIOS = os.path.join(os.path.dirname(HERE), "scenarios")


def _simulate(args) -> int:
    directory = args[0] if args else DEFAULT_SCENARIOS
    reports = simulator.run_library(directory)
    print(simulator.render_library(reports))
    return 0 if all(r.passed for r in reports) else 1


def _demo(args) -> int:
    path = args[0] if args else ":memory:"
    clock = FixedClock("2026-10-06T09:00:00Z")
    engine = MemoryEngine.create(path, "demo-agent", name="Demo Agent", clock=clock)
    project = engine.remember("UI Brand Revamp", "One-week project with the team upstairs.",
                              "project fact", ["upstairs"], switch_in_days=7)
    code = engine.remember("Door code", "Code 4471 opens the upstairs door.", "project fact",
                           ["door code", "upstairs"], switch_in_days=7,
                           links=[(project, "context", 0.9)])
    engine.remember("Door alarm warning", "Every door alarms after two wrong codes in a row.",
                    "safety warning", ["door code", "alarm"], category="Alert",
                    switch_in_days=7, links=[(code, "context", 0.9)])
    for day in (2, 10):
        clock.set(f"2026-10-{6 + day:02d}T09:00:00Z")
        print(f"--- day {day} ---")
        print(engine.recall(["alarm"]).render())
    engine.set_scan_line("Demo agent: shows how the memory fabric behaves.")
    engine.record_outcome("demo", "D-1", "success", 2, verified_by="operator")
    print(json.dumps({k: v for k, v in engine.card().items() if k != "avatar_svg"}, indent=2))
    engine.close()
    return 0


def _open(path: str) -> MemoryEngine:
    if not os.path.exists(path):
        raise SystemExit(f"no such cartridge: {path}")
    return MemoryEngine.open(path, read_only=True)


def _inspect(args) -> int:
    engine = _open(args[0])
    report = engine.open_report
    print(f"agent {report.agent_id}  schema {report.schema_version}  seal {report.status}"
          + (f" ({report.warning})" if report.warning else ""))
    conn = engine.cart.conn
    for table in ("notes", "links", "tags", "reviews", "outcomes"):
        print(f"  {table}: {conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]}")
    print("  strongest memories:")
    for row in conn.execute("SELECT note_id, title, category, strength FROM temp.note_state "
                            "ORDER BY strength DESC LIMIT 10"):
        print(f"    {row['strength']:.2f}  [{row['category']}] {row['title']}  ({row['note_id']})")
    engine.close()
    return 1 if report.status in ("tampered", "unsealed") else 0


def _card(args) -> int:
    engine = _open(args[0])
    print(json.dumps(engine.card(), indent=2))
    engine.close()
    return 0


def _resume(args) -> int:
    engine = _open(args[0])
    print(engine.resume())
    engine.close()
    return 0


def _view(args) -> int:
    from . import viewer
    flags = {a for a in args if a.startswith("--")}
    unknown = flags - {"--no-proprietary", "--no-bodies"}
    if unknown:
        print(f"unknown option(s): {sorted(unknown)}")
        return 2
    paths = [a for a in args if not a.startswith("--")]
    out = paths[1] if len(paths) > 1 else os.path.splitext(paths[0])[0] + ".memory.html"
    if os.path.abspath(out) == os.path.abspath(paths[0]):
        print("refusing to overwrite the cartridge")
        return 2
    engine = _open(paths[0])
    counts = viewer.write_html(engine, out,
                               include_proprietary="--no-proprietary" not in flags,
                               include_bodies="--no-bodies" not in flags)
    engine.close()
    print(f"wrote {out}: {counts['shown_notes']} of {counts['total_notes']} memories")
    return 0


def _inside_git_repo(path: str) -> bool:
    folder = os.path.dirname(os.path.abspath(path))
    while True:
        if os.path.exists(os.path.join(folder, ".git")):
            return True
        parent = os.path.dirname(folder)
        if parent == folder:
            return False
        folder = parent


def _import_profile(args) -> int:
    from . import importer
    options, positional, i = {}, [], 0
    while i < len(args):
        arg = args[i]
        if arg == "--dry-run":
            options["dry_run"] = True
        elif arg in ("--grace-days", "--name", "--move-in") and i + 1 < len(args):
            options[arg[2:].replace("-", "_")] = args[i + 1]
            i += 1
        elif arg.startswith("--"):
            print(f"unknown option: {arg}")
            return 2
        else:
            positional.append(arg)
        i += 1
    if len(positional) != 3:
        print("usage: python -m memory_fabric import-profile <memory_dir> <agent_id> <out.sqlite>"
              " [--dry-run] [--grace-days N] [--name NAME]")
        return 2
    memory_dir, agent_id, out = positional
    plan = importer.plan_import(memory_dir)
    print(plan.render())
    if options.get("dry_run"):
        return 0
    try:
        importer.check_destination(memory_dir, out)
    except importer.ImporterError as exc:
        print(f"refusing: {exc}")
        return 2
    if os.path.exists(out):
        print(f"refusing to overwrite an existing file: {out}")
        return 2
    if _inside_git_repo(out):
        print("WARNING: this cartridge is inside a git repository. Cartridges are minds, not"
              " source: keep them outside the repo (see AetherMemory/README.md).")
    # Built under a temporary name and renamed at the end: a half-finished move-in
    # never looks like a mind.
    building = out + ".moving-in"
    if os.path.exists(building):
        os.remove(building)
    engine = MemoryEngine.create(building, agent_id, name=options.get("name"))
    try:
        if options.get("move_in"):
            from . import movein
            report = movein.move_in(engine, memory_dir, movein.load_sheet(options["move_in"]),
                                    grace_days=float(options.get("grace_days", 30)))
            counts = {**report.imported, "arranged": report.changed_records,
                      "buried": report.buried, "links_added": report.links_added,
                      "remembered": report.remembered}
        else:
            counts = importer.apply_import(engine, plan,
                                           grace_days=float(options.get("grace_days", 30)))
    except BaseException:
        engine.close()
        os.remove(building)
        raise
    engine.close()
    os.replace(building, out)
    print(f"wrote {out}: {counts}")
    return 0


def _migrate(args) -> int:
    from . import migrations
    report = migrations.migrate(args[0])
    if not report.changed:
        print(f"{args[0]}: already schema {report.to_version}; nothing to do")
    else:
        print(f"{args[0]}: schema {report.from_version} -> {report.to_version}; "
              f"backup at {report.backup_path}")
    return 0


def _backup(args) -> int:
    if len(args) != 2:
        print("usage: python -m memory_fabric backup <cartridge_path> <destination>")
        return 2
    engine = _open(args[0])
    try:
        engine.backup(args[1])
    finally:
        engine.close()
    print(f"backed up {args[0]} -> {args[1]}")
    return 0


def _walkthrough(args) -> int:
    from . import movein
    options, positional, i = {}, [], 0
    while i < len(args):
        if args[i] in ("--sheet", "--name") and i + 1 < len(args):
            options[args[i][2:]] = args[i + 1]
            i += 2
            continue
        if args[i].startswith("--"):
            print(f"unknown option: {args[i]}")
            return 2
        positional.append(args[i])
        i += 1
    if len(positional) != 3:
        print("usage: python -m memory_fabric walkthrough <memory_dir> <agent_id> <out_dir>"
              " [--sheet MOVE_IN.json] [--name NAME]")
        return 2
    memory_dir, agent_id, out_dir = positional
    sheet = movein.load_sheet(options["sheet"]) if options.get("sheet") else None
    result = movein.walkthrough(memory_dir, agent_id, out_dir, sheet, name=options.get("name"))
    print(f"floor plan: {result['floor_plan']}")
    print(f"preview:    {result['preview']}" + ("  (with your sheet applied)"
                                                 if sheet and not result["problems"] else ""))
    print(f"your sheet: {result['sheet']}")
    if result["problems"]:
        print("The sheet needs changes before it can be applied (preview shown without it):")
        for problem in result["problems"]:
            print(f"  - {problem}")
        return 1
    return 0


def _archive(args) -> int:
    from . import archive
    options, positional, i = {}, [], 0
    while i < len(args):
        if args[i] in ("--frozen-at", "--name", "--note") and i + 1 < len(args):
            options[args[i][2:].replace("-", "_")] = args[i + 1]
            i += 2
            continue
        if args[i] == "--dry-run":
            options["dry_run"] = True
            i += 1
            continue
        if args[i].startswith("--"):
            print(f"unknown option: {args[i]}")
            return 2
        positional.append(args[i])
        i += 1
    if len(positional) != 3 or not options.get("frozen_at"):
        print("usage: python -m memory_fabric archive <legacy_folder> <agent_id> <out.sqlite>"
              " --frozen-at YYYY-MM-DD [--name NAME] [--note TEXT] [--dry-run]")
        return 2
    folder, agent_id, out = positional
    plan = archive.plan_archive(folder, agent_id, options["frozen_at"])
    print(plan.render())
    if options.get("dry_run"):
        return 0
    if _inside_git_repo(out):
        print("WARNING: this archive is inside a git repository. Cartridges are minds, not"
              " source: keep them outside the repo.")
    result = archive.create_archive(folder, out, agent_id, options["frozen_at"],
                                    name=options.get("name"), note=options.get("note"))
    print(f"wrote {out}: {result}")
    return 0


def _dashboard(args):
    from .dashboard_server import main as serve
    return serve(args)


COMMANDS = {"simulate": _simulate, "demo": _demo, "inspect": _inspect, "card": _card,
            "resume": _resume, "view": _view,
            "import-profile": _import_profile, "migrate": _migrate,
            "backup": _backup, "walkthrough": _walkthrough,
            "archive": _archive, "dashboard": _dashboard}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in COMMANDS:
        print(__doc__)
        return 2
    command, rest = argv[0], argv[1:]
    if command in ("inspect", "card", "resume", "view", "migrate", "backup") and not rest:
        print(f"usage: python -m memory_fabric {command} <cartridge_path>")
        return 2
    try:
        return COMMANDS[command](rest)
    except MemoryFabricError as exc:
        print(f"error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
