"""Session guard (2026-10-08, hardest list item 10): tests never write into the Bridge's own
journal folder. Bridge/bridge_journal holds the legacy global journal, partly committed; a test
that builds a MissionManager without a journal_dir used to append to it. If any test changes
that folder, the run fails and names what changed."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

JOURNAL = Path(__file__).resolve().parents[1] / "bridge_journal"
LMS_NAMES = ("lms", "lms.exe")  # runtime_observation.LMS_EXECUTABLE on either platform

# These cases require intentionally unexported resident identities, historical
# transcripts or legacy curriculum assets. Report SKIP, never PASS, in the public
# tier; leave ordinary private-checkout runs unchanged. Synthetic cases remain.
PUBLIC_PRIVATE_FIXTURES = {
    'test_roundtrip_active_briefcases_byte_stable': 'resident sealed briefcases',
    'test_s00079_three_identity_delta_is_exactly_bounded': 'resident identity history',
    'test_tampered_assembled_briefcase_fails_verify': 'resident sealed briefcase',
    'test_s00079_1_all_future_assembly_specs_use_canonical_crib_sheet': 'private assembly specifications',
    'test_parity_sealed_briefcases': 'resident sealed briefcases',
    'test_parser_fails_closed': 'resident sealed briefcase',
    'test_tamper_detection': 'resident sealed briefcase',
    'test_seal_gate': 'resident sealed briefcase',
    'test_all_52_evidence_entries_parse_or_quarantine_without_raw_leaks': 'private historical Studio transcripts',
    'test_clean_evidence_sample_is_byte_identical_and_same_object': 'private historical Studio transcripts',
    'test_catalog_paths_and_all_curriculum_frames_validate': 'legacy curriculum not shipped in Atelier',
}


def pytest_collection_modifyitems(items):
    if os.environ.get('AETHER_PUBLIC_TEST_TIER') == '1':
        for item in items:
            reason = PUBLIC_PRIVATE_FIXTURES.get(item.name)
            if reason:
                item.add_marker(pytest.mark.skip(reason='Not exported: '+reason))


def _names_lms(args):
    """True when a process start would run an executable named lms (the real LM Studio CLI)."""
    if isinstance(args, (str, bytes, os.PathLike)):
        text = os.fsdecode(args).strip().lower()
        first = text[1:].split('"', 1)[0] if text.startswith('"') else (text.split() or [""])[0]
        return Path(first).name in LMS_NAMES or Path(text).name in LMS_NAMES
    try:
        first = list(args)[0]
    except (TypeError, IndexError):
        return False
    return Path(os.fsdecode(first)).name.lower() in LMS_NAMES


@pytest.fixture(autouse=True)
def _never_run_the_real_lms(monkeypatch):
    """G9 lane D guard (pattern of test_lms_quiescence.guard): no test may execute the real LM Studio
    `lms` binary. Any subprocess.Popen (so also run/check_output) naming lms fails the test; a test
    double that patches Popen itself (test_lms_quiescence.FakeLms) replaces this guard for its test.
    runtime_observation.REVIEWED_LMS is empty today; this protects the day it is not."""
    real = subprocess.Popen

    class Guarded(real):
        def __init__(self, args, *rest, **options):
            if _names_lms(args) or _names_lms([options.get("executable") or ""]):
                raise AssertionError(f"a test tried to execute the real LM Studio CLI ({args!r}); use a test double")
            super().__init__(args, *rest, **options)
    monkeypatch.setattr(subprocess, "Popen", Guarded)


@pytest.fixture(autouse=True, scope="session")
def _campaign_path_room_as_on_a_long_path_machine():
    """Test folders sit far deeper than real campaign locations, so campaign creation's Windows
    path-room guard (project_setup.require_path_room) would refuse them. Tests run as if long paths
    were enabled; tests/test_campaign_path_room.py switches this off to check the guard itself.
    Session-scoped, so module-scoped fixtures that create campaigns (test_minder_findings) see it too.
    Tests that reach the deepest closure records still need a short campaign root (lane F does)."""
    from aether.roadmaps import project_setup
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(project_setup, "windows_long_paths_enabled", lambda: True)
        yield


def _snapshot():
    if not JOURNAL.is_dir():
        return {}
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in JOURNAL.iterdir() if p.is_file()}


def pytest_sessionstart(session):
    session.config._bridge_journal_before = _snapshot()


def pytest_sessionfinish(session, exitstatus):
    before = getattr(session.config, "_bridge_journal_before", None)
    if before is None:
        return
    after = _snapshot()
    changed = sorted(n for n in set(before) | set(after) if before.get(n) != after.get(n))
    if changed:
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        message = (f"tests changed Bridge/bridge_journal ({len(changed)} files, e.g. {changed[:3]}); "
                   "give the MissionManager or ToolBelt a temp journal_dir")
        if reporter is not None:
            reporter.write_line("ERROR: " + message, red=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
