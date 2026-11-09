# Validation scope for v0.2.0

This document separates retained evidence from checks of the new release candidate.
The release verification receipt accompanying the candidate supplies its exact hashes
and current check results. No later build inherits a test count merely by using a version label.

## Live Chat and memory evidence — November 8, 2026

The repository's running local app used DeepSeek for a four-operation rehearsal:

| Operation | Requests | Duration |
|---|---:|---:|
| Save a fictional rule | 2 | 7 seconds |
| End first conversation | 4 | 13 seconds |
| Retrieve in an initially empty new Chat | 7 | 20 seconds |
| End second conversation | 3 | 12 seconds |

The original rule note's ID and body were confirmed in storage and in the viewer.
The new prompt omitted the fact and ID; an actual memory_load read returned that
same note, and the final answer cited its ID. Both closing messages matched the
saved closing bodies and closed events. The first close recovered from a rejected
exact-body claim. The second browser confirmation required the operator's click.
All evidence, including the rejected call, was retained privately rather than
publishing the operator's profile or transcripts.

The Chat backend in that rehearsal is unchanged in this release alignment. These
results demonstrate source-note retrieval and closure, not model-weight learning
or an absence of identity/continuity context. The response was verbose; Current
until means faster fading, not deletion. Behavior and latency vary by provider.

## Package verification

The v0.2.0 candidate is checked for exact inventory and hashes, version agreement,
declared source selection, protected-loop preservation, isolated startup, and an
upgrade from the preserved prior package. Results are recorded with the candidate.
These are checks on the development Windows host, not a clean-machine certification.
The current public test runner uses only the files selected in public_surface.json.

On November 8, the extracted public source passed **1,014 tests** on Windows:
751 Bridge tests and 263 Memory Fabric tests. Thirteen explicit Bridge skips cover
their named fixture/platform conditions; they are not passes. The disposable
test copy was removed after its JUnit reports were retained. This is a local run,
not a GitHub CI result for the unpublished release.

The shipped roadmap is a public snapshot of current roadmap and sprint fields.
It omits internal change logs and evidence links to files outside the release.
The canonical development tracker is preserved separately.

## Earlier evidence

Praxis18 passed 25 isolated upgrade checks, five bundled native scripted checks,
and four memory-viewer tests. Its backend was identical to the earlier live-tested
Chat build: three answers and two exact closings across 21 requests. Malformed
markup recovery and failed-answer-to-End cases were covered offline.

The [October 28 two-project run](evidence/2026-10-28-two-project-run/README.md) is
historical evidence for that build. Its 912-test count and its live acceptance
results do not describe this v0.2.0 artifact. Earlier GitHub CI likewise belongs
to its recorded commit; a new unpublished candidate has no new GitHub CI run.

## Still separate from these results

- Independent evaluation and installation on a clean Windows machine.
- Live acceptance of every model provider. The Claude rehearsal was paused by the
  operator; it has not passed. Configuration support is not a live acceptance claim.
- Live malformed-markup and failure-to-End scenarios (currently offline coverage).
- Broad comparative evaluation of whether persistent memory improves task outcomes.
- GitHub CI on the eventual release commit, after publication is authorized.

## Runtime and execution boundary

The Windows bundle retains the tested CPython 3.10.8 runtime recipe, including
OpenSSL 1.1.1q and SQLite 3.37.2. Its pinned Python dependencies are Jinja2 3.1.6,
MarkupSafe 2.1.3, pypdf 6.21.2 and typing_extensions 4.15.0. These are exact build
inputs, not a claim that they are current upstream versions. See THIRD_PARTY_NOTICES.md.
The server binds to loopback. Generated code executes with the operator's rights;
this is not an operating-system sandbox. See SECURITY.md.
