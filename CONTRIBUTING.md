# Contributing

Aether v0.1 is an experimental Atelier developer preview. Keep changes focused, preserve agent authorship and automatic recovery, and keep memory/closure advisory. Host budget, consent, secret and harm boundaries remain.

Use Windows, Python 3.10 and the versions in `requirements-dev.txt`. From the repository root, `python tools/run_public_tests.py --list` prints the exact public selection. Run a specific file with `python tools/run_public_tests.py --group bridge --select tests/test_v01_integration_recovery.py`, or omit selection to run the public tier. The runner creates disposable scratch state and propagates test failures to its exit status.

Private Runtime, full-server and historical resident-fixture tests do not ship. `python -m aether.modules check` describes the full private module registry and is not a public-source command. Do not remove a relevant public assertion merely to get a passing result.

Include the problem, changed behavior and relevant verification with each pull request. Report failures and limitations accurately. Keep model calls scripted in tests; do not access paid providers, resident homes, local model sessions or real credentials. Do not introduce extra acknowledgment steps or erase uncertainty after interrupted external effects.

Preserve existing file bytes/line endings unless an intentional edit requires a change; new text files should use UTF-8/LF. Never commit runtime settings, credentials, user cartridges or personal histories. The source export's `.gitignore` covers common local outputs, but review your diff before submitting.

For bug reports, include version, Windows/Python/model information, exact steps and redacted diagnostic excerpts. Do not post raw private cartridges, keys or transcripts. See SECURITY.md for sensitive reports.
