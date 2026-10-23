# Validation and limits

The application code in this public source export is byte-identical to the selected RC1 source candidate. Public repository documentation, CI and the test runner are separately listed in PUBLICATION_MANIFEST.json. No private repository history, resident homes, cartridges or configured credentials are included.

## Existing RC1 evidence

- 485 public Bridge tests passed, with 11 explicitly skipped historical private-fixture cases.
- 262 Memory Fabric tests passed.
- Selected extracted library imports passed with Runtime/private-checkout access blocked and inventory unchanged.
- Bundled Windows installation, public HTTP/settings actions, fresh mind authoring, upgrade/restart, backup verification, new-root restore and uninstall retaining data passed locally.
- Five bundled subprocess interruption/restart cases passed using a scripted loopback model through the production adapter. After a durable reply, restart did not send another model request. An interruption before durable send resumed with uncertainty recorded; arbitrary external effects are not promised exactly-once execution.
- Capacity/profile reload, 20% first/final request margin, adapted seed authorship and Welcome checks are retained from focused tests.

These are development-machine and deterministic-provider results, not a new independent live performance benchmark. The release owner chose to rely on prior functional evidence and focused RC1 checks and omit repeating the complete installed A -> restart -> B journey before this release. An operator installation walkthrough is planned after deployment. No unmeasured timing, refusal or approval metric is reported as a result.

Reference implementations passing Harbor Ledger's 27 A checks and 29 B checks qualify the fixture only. They do not demonstrate distinct agent review, authored closure or actual recall. Benefits across tasks remain under evaluation.

## Public test scope

`AetherPublicFacing/Release/public_surface.json` names the exact test files. The mixed Bridge tier skips 11 cases that need historical private identities/specifications/records. Six full files are outside the public tier: legacy conformance lint, private verdict audit, deferred blind memory-policy research, native staffing revision projection, full-edition installed-route ownership, and full staffing-editor contracts. Public synthetic checks are retained. The runner prints selected files before execution; CI runs the same groups.

The existing full private suite is not claimed green. Other operating systems, additional providers, a repeated RC1 live A/B journey and GitHub-hosted CI are not claimed tested by these local results.

## Runtime and privacy

The Windows ZIP retains the qualified existing CPython 3.10.8, OpenSSL 1.1.1q and SQLite 3.37.2 runtime recipe. Jinja2 is 3.1.6; MarkupSafe is 2.1.3. This is an exact-version statement, not a claim that those versions are current upstream. Applicable bundled notices are retained.

Raw content scans flag documentation examples, synthetic test strings and runtime debug/path strings. Findings are retained and assessed against exact file hashes; a scan finding is not silently rewritten as a clean scan. Local byte provenance is distinct from independently reconstructing every upstream runtime component.

The loopback server and configured file-tool boundaries are not an OS sandbox for hostile code. Hosted models receive the supplied context. Keep evaluator answers, private files and keys outside agent-accessible workspaces. Backup/new-root restore preserves originals; the optional legacy same-path switch retains its unfinished-transaction/history safeguards. Memory and closure add no clearance requirement.
