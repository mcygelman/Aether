"""Bridge CLI — headless entry points for S00064.

  python -m aether_bridge validate  <briefcase.txt>
  python -m aether_bridge assemble  <agent_spec.json> --out <briefcase.txt>
  python -m aether_bridge ack-test  <briefcase.txt> --model <name> [--endpoint URL] [--api-key KEY]
  python -m aether_bridge run-packet <briefcase.txt> <packet.md> --model <name>
        --root name=path [--root name=path ...] [--journal DIR] [--endpoint URL] [--api-key KEY]
  python -m aether_bridge run-micro-packet <microcase.txt> <packet.md> --model <name>
        --root name=path [--root name=path ...] [--journal DIR] [--endpoint URL] [--api-key KEY]
        (Roadmap U-04 -- the MICROCASE-1/Ultralight equivalent of run-packet: micro-gate not
        seal-gate, tuned tool-call cap/result size/floor, tag-lint logged on the reply.)
  python -m aether_bridge mcp-serve --root name=path[:deny] ...
  python -m aether_bridge serve --root name=path[:mode] --model <name> --model-window <n>
        (local model is the required fallback client; hosted API backends like DeepSeek route
        per-packet via PacketPlan.backend, wired from secrets_local/, independent of --api-key)
  python -m aether_bridge plan-mission <brief> --model <name> [--submit <mission-id>] [--api-key KEY]

  --api-key defaults to the AETHER_API_KEY environment variable so a hosted-vendor key never
  needs to appear in shell history (2026-07-20, API-only mode). Leave unset for local LM Studio.
  python -m aether_bridge hash <file> [<file> ...]
  python -m aether_bridge undo --journal DIR ENTRY_ID
  python -m aether_bridge sanctum verify|reseal|hire ...
  python -m aether_bridge fabrication-check <mission.json> [--deliverable PID=path ...]
        [--root path ...]
  python -m aether_bridge check <mission.json | --corpus DIR>
"""
from __future__ import annotations
from aether.paths import BRIDGE_ROOT

import argparse
import json
import os
import sys

from aether.integrity import canon
from aether.agent import microcase as microcase_mod
from aether.agent.briefcase import BriefcaseError, parse_file, verify
from aether.agent.assembler import main as assemble_main
from aether.integrity.fabrication_check import overall_verdict, run_tripwires
from aether.backends.model import LMStudio, select_dialect
from aether.backends.legacy_endpoints import LEGACY_LMSTUDIO_ENDPOINT
from aether.toolbelt.mcp_server import main as mcp_main
from aether.credentials import key_lifecycle
from aether.integrity import sanctum, sealbox

ATTRIBUTION_HELP = (
    "plaintext attribution; not identity authentication or a digital signature"
)
from aether.toolbelt.toolbelt import ToolBelt, ToolError, parse_root_spec, undo  # noqa: F401 (re-exported)


def resolve_windows(context_window: int | None, model_window: int | None) -> tuple[int, int, str | None]:
    if model_window is None:
        raise ValueError("--model-window is required for live model runs")
    effective_window = model_window

    warning = None
    if context_window is not None and context_window != model_window:
        if context_window > model_window:
            warning = (
                "WARNING: --context-window exceeds --model-window; ignoring the inflated "
                "override so the budget guard stays tied to the real model window "
                f"(context-window={context_window}, model-window={model_window})."
            )
        else:
            warning = (
                "WARNING: --context-window differs from --model-window; using the real "
                f"model window for the budget guard (context-window={context_window}, "
                f"model-window={model_window})."
            )
    return int(effective_window), int(model_window), warning


def emit_window_warning(warning: str | None) -> None:
    if warning:
        print(warning, file=sys.stderr)


def serve_main(argv: list[str]) -> int:
    """The private full edition's server, imported only when it starts (it reaches the Runtime)."""
    from aether.mission_control.server import main
    return main(argv)


def _edition(argv: list[str]) -> str:
    for i, item in enumerate(argv):
        if item == "--edition" and i + 1 < len(argv):
            return argv[i + 1]
        if item.startswith("--edition="):
            return item.split("=", 1)[1]
    return "full"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["serve"] and _edition(argv[1:]) == "atelier":
        # The Atelier edition (v0.1): no Runtime module is imported on this path.
        from aether.mission_control.atelier_server import main as atelier_main
        rest = argv[1:]
        drop = {i for i, item in enumerate(rest) if item.startswith("--edition")}
        drop |= {i + 1 for i in drop if rest[i] == "--edition"}
        return atelier_main([item for i, item in enumerate(rest) if i not in drop])
    # The full edition's commands use the Runtime; import it only past the Atelier path above.
    from aether.runtime.orchestration.check import WIRED_TRIPWIRES, check_corpus, check_mission_file, verdict_line
    from aether.runtime.orchestration.planner import plan_with_client, submit_plan, template_path
    from aether.runtime.sessions.toolloop import DYNAMIC_FLOOR, OUTPUT_RESERVE, deliver_and_gate, run_packet, run_micro_packet
    from aether.runtime.sessions.transcript import write_markdown
    ap = argparse.ArgumentParser(prog="aether_bridge")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="parse + checksum-verify a briefcase")
    v.add_argument("briefcase")

    h = sub.add_parser("hash", help="print AETHER-CANON-1 sha256 for file freshness checks")
    h.add_argument("files", nargs="+")

    asm = sub.add_parser("assemble", help="assemble and seal a BRIEFCASE-1 file from a JSON spec")
    asm.add_argument("agent_spec")
    asm.add_argument("--out", required=True)

    def model_args(p):
        p.add_argument("--endpoint", default=LEGACY_LMSTUDIO_ENDPOINT)
        p.add_argument("--model", required=True)
        p.add_argument(
            "--dialect", choices=("auto", "openai", "deepseek"), default="auto",
            help="Response dialect. auto selects DeepSeek only for its published API endpoint.",
        )
        # 2026-07-20 (API-only mode guide): every LMStudio-shaped client already accepted
        # api_key (S00073.7 B2, see model.py's deepseek_client()), but no CLI subcommand had a
        # way to actually pass one -- --endpoint alone against a hosted vendor always sent no
        # Authorization header and would 401. Reads AETHER_API_KEY from the environment by
        # default so the key never has to appear in shell history / process listings; --api-key
        # is still accepted explicitly for one-off overrides.
        p.add_argument("--api-key", default=os.environ.get("AETHER_API_KEY", ""),
                       help="Bearer token for a hosted OpenAI-compatible API backend (e.g. "
                            f"DeepSeek). Empty (default for --endpoint {LEGACY_LMSTUDIO_ENDPOINT}) "
                            "sends no Authorization header, which is what local LM Studio "
                            "expects. Falls back to the AETHER_API_KEY environment variable.")
        p.add_argument("--context-window", type=int, default=None,
                       help="Compatibility assertion. If set, it must match --model-window or a warning is emitted.")
        p.add_argument("--model-window", type=int, default=None,
                       help="Real configured model context window; required for live runs.")

    a = sub.add_parser("ack-test", help="deliver briefcase, run the ACK seal gate")
    a.add_argument("briefcase")
    model_args(a)

    r = sub.add_parser("run-packet", help="deliver, gate, then execute a packet with tools")
    r.add_argument("briefcase")
    r.add_argument("packet")
    model_args(r)
    r.add_argument("--root", action="append", default=[], metavar="NAME=PATH[:deny]")
    r.add_argument("--journal", default="./bridge_journal")
    r.add_argument("--transcript-out", default=None)
    r.add_argument("--transcript-md", default=None)
    r.add_argument("--dynamic-floor", type=int, default=DYNAMIC_FLOOR)
    r.add_argument("--output-reserve", type=int, default=OUTPUT_RESERVE)

    rm = sub.add_parser("run-micro-packet",
                        help="deliver a MICROCASE-1 jewel, micro-gate, then execute a packet "
                             "with tools (Roadmap U-04)")
    rm.add_argument("microcase")
    rm.add_argument("packet")
    model_args(rm)
    rm.add_argument("--root", action="append", default=[], metavar="NAME=PATH[:deny]")
    rm.add_argument("--journal", default="./bridge_journal")
    rm.add_argument("--transcript-out", default=None)
    rm.add_argument("--transcript-md", default=None)
    rm.add_argument("--dynamic-floor", type=int, default=microcase_mod.DYNAMIC_FLOOR_MICRO)
    rm.add_argument("--output-reserve", type=int, default=OUTPUT_RESERVE)

    u = sub.add_parser("undo", help="undo an auto-write journal entry")
    u.add_argument("entry_id")
    u.add_argument("--journal", required=True)

    mcp = sub.add_parser("mcp-serve", help="serve read-only ToolBelt tools over MCP stdio")
    mcp.add_argument("--root", action="append", default=[], metavar="NAME=PATH[:mode]")

    s = sub.add_parser("serve", help="serve the Mission Control GUI/API on localhost")
    s.add_argument("--edition", choices=("atelier", "full"), default="full",
                   help="atelier: the public v0.1 surfaces only; full: the private full edition (default).")
    s.add_argument("--port", type=int, default=8484)
    s.add_argument("--endpoint", default=LEGACY_LMSTUDIO_ENDPOINT)
    s.add_argument("--model", required=True)
    s.add_argument("--model-window", type=int, required=True)
    s.add_argument("--root", action="append", default=[], metavar="NAME=PATH[:mode]")
    s.add_argument("--heavyweight-runs-root", default=None)
    s.add_argument("--runtime-profiles", default=None, metavar="PATH",
                   help="Models & Context editor registry; does not select the dispatch model.")
    s.add_argument("--governed", action="store_true",
                   help="Compose the governed campaign hosts from configuration (MVP-01 G1).")
    s.add_argument("--governed-config", default=None, metavar="PATH",
                   help="Governed services configuration (default: Bridge/governed_services.json).")

    pm = sub.add_parser("plan-mission", help="slice a mission brief into packet-row JSON")
    pm.add_argument("brief")
    model_args(pm)
    pm.add_argument("--submit", default=None, metavar="MISSION_ID")
    pm.add_argument("--bridge-endpoint", default="http://127.0.0.1:8484",
                    help="Mission Control endpoint for --submit.")

    san = sub.add_parser("sanctum", help="SANCTUM-1 heavyweight profile integrity verbs")
    san_sub = san.add_subparsers(dest="sanctum_cmd", required=True)
    san_v = san_sub.add_parser("verify", help="verify a Sanctum profile manifest")
    san_v.add_argument("profile")
    san_r = san_sub.add_parser("reseal", help="operator-authorized Sanctum manifest reseal")
    san_r.add_argument("profile")
    san_r.add_argument("--authorized-by", required=True, help=ATTRIBUTION_HELP)
    san_i = san_sub.add_parser("initialize", help="create a first manifest for an existing profile")
    san_i.add_argument("profile")
    san_i.add_argument("--agent", required=True)
    san_i.add_argument("--authorized-by", required=True, help=ATTRIBUTION_HELP)
    san_h = san_sub.add_parser("hire", help="instantiate a Sanctum profile from a template")
    san_h.add_argument("template")
    san_h.add_argument("profile")
    san_h.add_argument("--agent", default="UNAUTHORED")
    for verb in ("acknowledge", "resolve", "archive"):
        parser = san_sub.add_parser(verb, help=f"{verb} a Sanctum recovery flag")
        parser.add_argument("profile")
        parser.add_argument("--by", required=True)

    key = sub.add_parser("key", help="operator-controlled Sealbox key lifecycle")
    key_sub = key.add_subparsers(dest="key_cmd", required=True)
    for verb in ("backup", "restore"):
        parser = key_sub.add_parser(verb)
        parser.add_argument("profile")
        parser.add_argument("backup")
        parser.add_argument("--secret-env", required=True)
        parser.add_argument("--authorized-by", required=True, help=ATTRIBUTION_HELP)
    key_rotate = key_sub.add_parser("rotate")
    key_rotate.add_argument("profile")
    key_rotate.add_argument("--authorized-by", required=True, help=ATTRIBUTION_HELP)
    key_mark = key_sub.add_parser("mark")
    key_mark.add_argument("profile")
    key_mark.add_argument("state", choices=sorted(key_lifecycle.VALID_STATES))
    key_mark.add_argument("--detail", required=True)
    key_mark.add_argument("--authorized-by", required=True, help=ATTRIBUTION_HELP)

    fab = sub.add_parser(
        "fabrication-check",
        help="mechanical fabrication tripwires (Fable_Ruling_S00073.6 rule 3): zero-tool-call "
             "research/analysis/review claims, hash/near-identical deliverables",
    )
    fab.add_argument("mission_json", help="mission JSON file (as written to .aether/missions/)")
    fab.add_argument(
        "--deliverable", action="append", default=[],
        help="PACKET_ID=path -- a deliverable file to hash-check for packet PACKET_ID "
             "(repeatable)",
    )
    fab.add_argument(
        "--root", action="append", default=[],
        help="pre-existing-file search root for the hash/near-identical check (repeatable)",
    )

    chk = sub.add_parser("check", help="run all existing mission integrity tripwires")
    chk.add_argument("mission_json", nargs="?", help="one stored mission JSON")
    chk.add_argument("--corpus", help="directory of stored mission JSON files")

    from aether.runtime.orchestration import configured_launcher
    configured_launcher.add_parser(sub)
    from aether.mission_control import configured_mission_launcher
    configured_mission_launcher.add_parser(sub)
    from aether.mission_control import mission_launch_editor
    mission_launch_editor.add_parser(sub)
    args = ap.parse_args(argv)

    if args.cmd == 'run-configured-packet':
        return configured_launcher.main(args)
    if args.cmd == 'serve-configured':
        return configured_mission_launcher.main(args)
    if args.cmd == 'edit-mission-launch':
        return mission_launch_editor.main(args)

    if args.cmd == "hash":
        ok = True
        for file in args.files:
            try:
                with open(file, "rb") as f:
                    text = canon.decode_utf8(f.read())
                print(f"{canon.canon_sha(text)}  {file}")
            except OSError as e:
                print(f"HASH FAIL: {file}: {e}", file=sys.stderr)
                ok = False
        return 0 if ok else 1

    if args.cmd == "fabrication-check":
        try:
            mission_data = json.loads(open(args.mission_json, encoding="utf-8").read())
        except (OSError, json.JSONDecodeError) as e:
            print(f"FABRICATION-CHECK FAIL: could not read mission JSON: {e}", file=sys.stderr)
            return 2
        packets = mission_data.get("packets", [])
        replies = {}
        for entry in mission_data.get("transcript") or []:
            if entry.get("kind") == "reply" and entry.get("packet_id") is not None:
                replies[str(entry["packet_id"])] = entry.get("text", "")
        deliverables = {}
        for item in args.deliverable:
            pid, sep, path = item.partition("=")
            if not sep:
                print(f"FABRICATION-CHECK FAIL: bad --deliverable '{item}' (expected PID=path)",
                      file=sys.stderr)
                return 2
            deliverables[pid] = path
        findings = run_tripwires(
            packets,
            deliverables=deliverables or None,
            roots=args.root or None,
            replies=replies,
        )
        for f in findings:
            label = f.classification or f.check
            print(f"{f.verdict:5s} {label:38s} {f.packet_id:6s} {f.reason}")
        verdict = overall_verdict(findings)
        print(f"OVERALL: {verdict}")
        return 0 if verdict == "PASS" else 1

    if args.cmd == "check":
        if bool(args.mission_json) == bool(args.corpus):
            print("CHECK FAIL: provide exactly one mission JSON or --corpus DIR", file=sys.stderr)
            return 2
        try:
            if args.corpus:
                from pathlib import Path
                report = check_corpus(Path(args.corpus), write=True)
            else:
                from pathlib import Path
                report = check_mission_file(Path(args.mission_json), write=True)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"CHECK FAIL: {exc}", file=sys.stderr)
            return 2
        for tripwire in WIRED_TRIPWIRES:
            print(f"WIRED: {tripwire}")
        print(verdict_line(report))
        return 1 if report["verdict"] == "FAIL" else 0

    if args.cmd == "assemble":
        return assemble_main([args.agent_spec, "--out", args.out])

    if args.cmd == "mcp-serve":
        mcp_argv: list[str] = []
        for root in args.root:
            mcp_argv.extend(["--root", root])
        return mcp_main(mcp_argv)

    if args.cmd == "serve":
        serve_argv = ["--port", str(args.port), "--endpoint", args.endpoint,
                      "--model", args.model, "--model-window", str(args.model_window)]
        for root in args.root:
            serve_argv.extend(["--root", root])
        if args.heavyweight_runs_root:
            serve_argv.extend(["--heavyweight-runs-root", args.heavyweight_runs_root])
        if args.runtime_profiles:
            serve_argv.extend(["--runtime-profiles", args.runtime_profiles])
        if args.governed:
            serve_argv.append("--governed")
        if args.governed_config:
            serve_argv.extend(["--governed-config", args.governed_config])
        return serve_main(serve_argv)

    if args.cmd == "plan-mission":
        try:
            context_window, model_window, warning = resolve_windows(args.context_window, args.model_window)
        except ValueError as e:
            print(f"CONFIG FAIL: {e}", file=sys.stderr)
            return 2
        emit_window_warning(warning)
        try:
            from pathlib import Path
            bridge_dir = BRIDGE_ROOT
            template = template_path(bridge_dir).read_text(encoding="utf-8")
            brief = open(args.brief, encoding="utf-8").read()
            client = LMStudio(endpoint=args.endpoint, model=args.model, context_window=context_window,
                              api_key=args.api_key,
                              dialect=select_dialect(args.endpoint, args.dialect))
            plan = plan_with_client(client, template, brief)
            if args.submit:
                print(json.dumps(submit_plan(args.bridge_endpoint, args.submit, plan), ensure_ascii=False, indent=2))
            else:
                print(json.dumps({"planned_by": plan["planned_by"], "packets": plan["packets"]},
                                 ensure_ascii=False, indent=2))
            return 0
        except Exception as e:
            print(f"PLAN-MISSION FAIL: {e}", file=sys.stderr)
            return 1

    if args.cmd == "sanctum":
        try:
            if args.sanctum_cmd == "hire":
                manifest = sanctum.hire(args.template, args.profile, args.agent)
                print(f"SANCTUM HIRE PASS: {args.profile} agent={manifest['agent']} protected={len(manifest['protected'])}")
                return 0
            if args.sanctum_cmd == "reseal":
                manifest = sanctum.reseal(args.profile, args.authorized_by)
                print(f"SANCTUM RESEAL PASS: {args.profile} prior_manifest={manifest['prior_manifest']} protected={len(manifest['protected'])}")
                return 0
            if args.sanctum_cmd == "initialize":
                manifest = sanctum.initialize(args.profile, args.agent, args.authorized_by)
                print(f"SANCTUM INITIALIZE PASS: {args.profile} agent={manifest['agent']} protected={len(manifest['protected'])}")
                return 0
            if args.sanctum_cmd in ("acknowledge", "resolve", "archive"):
                operation = getattr(sanctum, f"{args.sanctum_cmd}_recovery")
                path = operation(args.profile, args.by)
                print(f"RECOVERY {args.sanctum_cmd.upper()} PASS: {path}")
                return 0
            report, recovery = sanctum.verify_or_refuse(args.profile, "cli")
            print(sanctum.report_text(report))
            if not report.ok:
                if recovery["artifact_written"]:
                    print(f"RECOVERY_FLAG written: {recovery['artifact']}")
                else:
                    print(f"RECOVERY_FLAG not written: {recovery.get('artifact_error')}")
            elif recovery["state"] == "RESOLVED":
                print(f"RECOVERY RESOLVED: {recovery['artifact']}")
            return 0 if report.ok else 1
        except (sanctum.SanctumError, OSError, ValueError) as e:
            print(f"SANCTUM {args.sanctum_cmd.upper()} FAIL: {e}")
            if args.sanctum_cmd == "verify":
                report = sanctum.VerifyReport(False, args.profile, [], str(e))
                try:
                    flag = sanctum.write_recovery_flag(args.profile, report)
                    print(f"RECOVERY_FLAG written: {flag}")
                except OSError:
                    pass
            return 1

    if args.cmd == "key":
        try:
            if args.key_cmd in ("backup", "restore"):
                secret = os.environ.get(args.secret_env)
                if secret is None:
                    raise key_lifecycle.KeyLifecycleError(
                        f"secret environment variable is not set: {args.secret_env}"
                    )
                operation = getattr(key_lifecycle, f"{args.key_cmd}_key")
                if args.key_cmd == "backup":
                    result = operation(
                        args.profile, args.backup, secret, args.authorized_by
                    )
                else:
                    result = operation(
                        args.profile, args.backup, secret, args.authorized_by
                    )
            elif args.key_cmd == "rotate":
                result = key_lifecycle.rotate_key(args.profile, args.authorized_by)
            else:
                result = key_lifecycle.mark_state(
                    args.profile, args.state, args.detail, args.authorized_by
                )
            print(f"KEY {args.key_cmd.upper()} PASS: {json.dumps(result, default=str)}")
            return 0
        except (key_lifecycle.KeyLifecycleError, sealbox.SealboxError, OSError) as exc:
            print(f"KEY {args.key_cmd.upper()} FAIL: {exc}")
            return 1

    if args.cmd == "undo":
        try:
            result = undo(args.journal, args.entry_id)
        except (ToolError, OSError, ValueError) as e:
            print(f"UNDO FAIL: {e}")
            return 1
        print(f"UNDO PASS: {result['action']} {result['path']}")
        return 0

    if args.cmd == "run-micro-packet":
        try:
            mc = microcase_mod.parse_file(args.microcase)
        except (microcase_mod.MicrocaseError, OSError) as e:
            print(f"PARSE FAIL: {e}")
            return 2
        try:
            context_window, model_window, warning = resolve_windows(args.context_window, args.model_window)
        except ValueError as e:
            print(f"CONFIG FAIL: {e}", file=sys.stderr)
            return 2
        emit_window_warning(warning)
        client = LMStudio(endpoint=args.endpoint, model=args.model, context_window=context_window,
                          api_key=args.api_key,
                          dialect=select_dialect(args.endpoint, args.dialect))
        belt = ToolBelt(args.journal, max_result_chars=microcase_mod.MAX_RESULT_CHARS_MICRO)
        for spec in args.root:
            name, path, mode = parse_root_spec(spec)
            belt.register_root(name, path, mode)
        packet_text = open(args.packet, encoding="utf-8").read()
        result = run_micro_packet(client, mc, packet_text, belt, dynamic_floor=args.dynamic_floor,
                                  output_reserve=args.output_reserve,
                                  expects=[{"kind": "reply"}])
        print(f"STATUS: {result.status}  tool_calls={result.tool_calls_used}")
        if result.detail:
            print(f"DETAIL: {result.detail}")
        if result.reply:
            print("--- REPLY ---")
            print(result.reply)
        tag_lint_entries = [t for t in result.transcript if t.get("kind") == "tag_lint"]
        if tag_lint_entries:
            print(f"TAG-LINT: {tag_lint_entries[-1]['verdict']}")
        if args.transcript_out:
            with open(args.transcript_out, "w", encoding="utf-8") as f:
                json.dump(result.transcript, f, ensure_ascii=False, indent=1)
            print(f"transcript -> {args.transcript_out}")
        if args.transcript_md:
            write_markdown(result, args.transcript_md)
            print(f"transcript markdown -> {args.transcript_md}")
        return 0 if result.status == "complete" else 1

    try:
        bc = parse_file(args.briefcase)
    except (BriefcaseError, OSError) as e:
        print(f"PARSE FAIL: {e}")
        return 2

    if args.cmd == "validate":
        rep = verify(bc)
        print(f"agent={bc.header.agent} sections={len(bc.sections)} est_tokens={bc.header.est_tokens}")
        if rep.ok:
            print("VALIDATE PASS — all section checksums match the manifest")
            return 0
        for label, detail in rep.failures:
            print(f"  FAIL {label}: {detail}")
        return 1

    try:
        context_window, model_window, warning = resolve_windows(args.context_window, args.model_window)
    except ValueError as e:
        print(f"CONFIG FAIL: {e}", file=sys.stderr)
        return 2
    emit_window_warning(warning)

    client = LMStudio(endpoint=args.endpoint, model=args.model,
                      context_window=context_window, api_key=args.api_key,
                      dialect=select_dialect(args.endpoint, args.dialect))

    if args.cmd == "ack-test":
        transcript: list[dict] = []
        ok, _, detail = deliver_and_gate(client, bc, transcript)
        print(detail)
        return 0 if ok else 1

    # run-packet
    belt = ToolBelt(args.journal)
    for spec in args.root:
        name, path, mode = parse_root_spec(spec)
        belt.register_root(name, path, mode)
    packet_text = open(args.packet, encoding="utf-8").read()

    result = run_packet(client, bc, packet_text, belt, dynamic_floor=args.dynamic_floor,
                        output_reserve=args.output_reserve, expects=[{"kind": "reply"}])
    print(f"STATUS: {result.status}  tool_calls={result.tool_calls_used}")
    if result.detail:
        print(f"DETAIL: {result.detail}")
    if result.reply:
        print("--- REPLY ---")
        print(result.reply)
    if args.transcript_out:
        with open(args.transcript_out, "w", encoding="utf-8") as f:
            json.dump(result.transcript, f, ensure_ascii=False, indent=1)
        print(f"transcript -> {args.transcript_out}")
    if args.transcript_md:
        write_markdown(result, args.transcript_md)
        print(f"transcript markdown -> {args.transcript_md}")
    return 0 if result.status == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())
