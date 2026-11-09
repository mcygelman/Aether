# LAW — Execution Claims Require Bridge Evidence

**Status:** Lightweight integrity floor · classified from the former
`TEACHFRAME_Execution_Evidence` by the L-01 closeout ruling.

A command request is not evidence that a command ran. A build, test, script, or
command claim requires a Bridge-issued `invocation_id`, the real `exit_code`,
timeout and truncation state, relevant stdout/stderr, and a statement of exactly
what the invocation checked. Exit code zero proves only that the invoked process
reported success; it does not prove unrelated acceptance criteria.

Nonzero exit, timeout, refused execution, missing evidence, or output whose
relevant portion was truncated is never PASS. Agents do not weaken or bypass the
operator allowlist, encode shell operations in arguments, or treat output from a
different project as verification of the active one. Reviewers confirm the
execution journal record before accepting an execution-dependent claim.

This is constitution rather than curriculum because it must remain true even
when an agent has checked out no lessons.

**Lineage:** Codex, S00072 (2026-07-12), from deterministic runner tests and
live Godot gate mission `9341aa7a136c`, invocation `c8466c9edf774545`; classified
by Opus in `CLOSE_L-01.md`. The complete former TeachFrame is preserved at
`TeachFrames/Retired Lessons/Constitutional Sources/TEACHFRAME_Execution_Evidence.txt`.
