"""S00064 core tests — canon vectors, parser fail-closed, seal gate, budget,
toolbelt scoping/journal, and PARITY against the three sealed briefcases.

Run:  python -m pytest tests/ -q     (or: python tests/test_core.py)
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aether_bridge import ack, briefcase, budget, canon  # noqa: E402
from aether_bridge.toolbelt import ToolBelt, ToolError  # noqa: E402

BRIEFCASES = Path(__file__).resolve().parents[2] / "Briefcases"
SEALED = [BRIEFCASES / n / f"{n}_Briefcase.txt" for n in ("Kirito", "Grace", "Linus")]


def test_canon_vectors():
    assert canon.canon_bytes("a\r\nb\rc") == b"a\nb\nc"
    assert canon.canon_bytes("é") == canon.canon_bytes("é")  # NFC
    assert canon.decode_utf8(b"\xef\xbb\xbfhi") == "hi"                 # BOM
    assert canon.sha256_hex(b"") == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")


def test_parity_sealed_briefcases():
    """The gate for the doctrine port: all three sealed briefcases parse and
    every section checksum matches the manifest (agrees with the Rust wrapper)."""
    for path in SEALED:
        bc = briefcase.parse_file(path)
        rep = briefcase.verify(bc)
        assert rep.ok, f"{path.name}: {rep.failures}"
        assert bc.header.est_tokens > 0
        # Post-S00073.7 C-2 PROTOCOL_DIGEST reseal: sections 02 CRIB_SHEET + 03 WORKFLOW_INSTRUCTIONS
        # + 04-07 PROTOCOL (Task/Result/Scaffold/Canvas) collapsed into a single PROTOCOL_DIGEST
        # section by design (design decision Q3) — the old ">= 8" floor assumed the
        # pre-digest section layout. New floor: IDENTITY + PROTOCOL_DIGEST + at least one
        # TeachFrame + TASK_PACKAGE.
        assert len(bc.sections) >= 4


def test_parser_fails_closed():
    raw = SEALED[0].read_text(encoding="utf-8")
    for bad in (raw.replace("=== AETHER BRIEFCASE ===", "=== BRIEFCASE ===", 1),
                raw.replace("=== END MANIFEST ===", "", 1),
                raw.replace("FORMAT: BRIEFCASE-1", "FORMAT: BRIEFCASE-2", 1)):
        try:
            briefcase.parse(bad)
            assert False, "should have failed closed"
        except briefcase.BriefcaseError:
            pass


def test_tamper_detection():
    bc = briefcase.parse_file(SEALED[1])  # Grace
    bc.sections[0].body = bc.sections[0].body.replace("Grace", "Mallory", 1)
    rep = briefcase.verify(bc)
    assert not rep.ok and rep.failures[0][0].endswith("IDENTITY")


def test_seal_gate():
    bc = briefcase.parse_file(SEALED[1])
    seals = " ".join(s.seal for s in bc.sections)
    good = ack.parse_ack(f"AGENT: Grace\nSTATUS: COMPLETE\nSEALS: {seals}")
    assert ack.seal_gate(bc, good).passed
    # drop one seal -> fail with the section named; add a confabulated one -> stray
    partial = ack.parse_ack(f"SEALS: {seals.rsplit(' ', 1)[0]} ffffff")
    gate = ack.seal_gate(bc, partial)
    assert not gate.passed and len(gate.missing) == 1 and "ffffff" in gate.unmatched
    # fallback scan (no SEALS: line)
    loose = ack.parse_ack(f"I see these seals: {seals}. STATUS: COMPLETE")
    assert ack.seal_gate(bc, loose).passed


def test_budget():
    assert budget.over_budget(20_000, 32_768, 0.5)
    assert not budget.over_budget(13_800, 32_768, 0.5)
    assert budget.est_tokens("x" * 35) == 10


def test_toolbelt_scoping_and_journal():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "proj"
        root.mkdir()
        (root / "a.md").write_text("hello aether\n", encoding="utf-8")
        belt = ToolBelt(Path(td) / "journal")
        belt.register_root("proj", root)
        assert belt.read_file("proj", "a.md").startswith("hello")
        assert belt.search("proj", "aether")[0]["file"] == "a.md"
        for esc in ("../x", "../../etc/passwd", "a/../../x"):
            try:
                belt.read_file("proj", esc)
                assert False, f"escape not blocked: {esc}"
            except ToolError:
                pass
        # write auto-applies + journals prior content (D3 ruling)
        r = belt.write_file("proj", "a.md", "v2\n")
        assert r["applied"] and (root / "a.md").read_text() == "v2\n"
        priors = list((Path(td) / "journal").glob("*.prior"))
        assert len(priors) == 1 and priors[0].read_text() == "hello aether\n"
        # deny-mode root is read-only
        belt.register_root("ro", root, "deny")
        try:
            belt.write_file("ro", "a.md", "nope")
            assert False
        except ToolError:
            pass


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"{len(fns)}/{len(fns)} green")
