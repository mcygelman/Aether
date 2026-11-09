# Tasker prompt workflow candidate

This is a local source candidate for the revamped headless Tasker. It is not a
qualified public release or the existing Mission Control desktop workflow.
Python 3.10 or newer is required. The supported pilot uses local task inputs,
Staff Home identities, automatic briefcases and explicit task dependencies.
Optional external TeachFrame catalogs and heavyweight delegation are outside
this pilot's qualification scope.

From the extracted candidate, open PowerShell in
`AetherAgents/LightweightAgents/Bridge`. Keep the writable data root short to
avoid Windows path-length problems. These commands use an example data root:

```powershell
python -m aether.tasker staff seed --data-root C:/AetherData
python -m aether.tasker mind create --data-root C:/AetherData --id iris --name Iris
python -m aether.tasker mind list --data-root C:/AetherData
```

Create a home once. On later runs, use its existing ID. Creating the same ID
again fails without overwriting identity or memory. Staff seed also preserves
existing staff. Identities stay in the homes, including while a sprint runs.

Provide your runtime profile separately from the candidate. Use an environment
credential reference, never put a credential in the goals or a task prompt.
The profile's deployment name must match `--deployment`.

```powershell
$created = python -m aether.tasker sprint new --data-root C:/AetherData --goals C:/Work/goals.txt --contracts C:/Work/output-contracts.json --overseer iris --deployment deepseek-flash --profiles C:/Work/profiles.json --read-root C:/Work/inputs --cap max_cost_per_sprint=5 --no-timer --json | ConvertFrom-Json
$sprint = $created.path
python -m aether.tasker timer run "$sprint"
```

`--no-timer` prepares the sprint so it can be checked before starting. Omit it
to start the Timer in the background. `--read-root` is repeatable and grants
the Overseer read access to the named existing file or directory. The Overseer
gives staff their necessary folder grants with `briefcase_create(read_roots=...)`.
Use full paths for external inputs. Relative read paths start in the sprint
folder; staff write paths default to that task's output folder. Programs must
use the stated working directory and the exact dependency paths.

In a second terminal, `sprint status <path> --json` shows progress and use.
`sprint stop <path>` stops after in-flight turns. `sprint resume <path>` lifts
a pause; it does not start a new Timer process. Use `timer run <path>` when a
stopped process needs restarting. Do not resume a held qualification failure.

Write each request simply: name the inputs, the one required result, its exact
output path/format and how to check it. Name any dependency and its acceptance
condition. Let Tasker statuses carry progress. Do not introduce a second
readiness or verdict protocol. Staff start and close their own tasks; the
Overseer coordinates the sequence and closure.

The current recovery policy allows two bounded corrections of failed commands,
closure format and malformed tool-call JSON. JSON syntax alone does not need
`retry=true`; a failed command or closure-format correction does. Preserve a
retry already present when repairing its JSON. Stop once corrected. Other real
issues, unresolved corrections and exhausted attempts stop qualification.
Overseer setup correction before dispatch remains permitted. Long notes are
warnings only. Successful automatic recovery counts as a successful request;
incorrect output, incomplete work or unplanned human rescue counts as failure.

Qualification checks the output independently of the agent's own PASS note.
Offline scripted transport tests verify mechanics only. Live reliability must
be measured separately on one frozen candidate and a declared request mix.

## Native output checks and acceptance

New CLI sprints require `--contracts`: one operator-owned JSON file declaring the
expected outputs by task number. Keep this file and trusted evidence outside the
agent input folders. Tasker snapshots it under
`C:/AetherData/verification/<sprint-id>/`; changing the source later does not change
the sprint. No extra observer, launcher or monitoring agent is needed.

For a one-task JSON result with an independently known answer:

```json
{
  "1": {
    "version": 1,
    "review_required": false,
    "files": {"TOTAL.json": {"json": {"total": 7}}},
    "evidence": []
  }
}
```

The goals should ask for that work and task layout. The prime runtime context
supplies declared filenames alongside exact output directories. Expected answers
are not injected into the agent context. For a solo Overseer sprint, use `solo`
instead of `1`; the same gate checks its outputs before a passing sprint finish.

For prose, set `review_required` to `true` and declare concrete checks, for example
`"files": {"NOTE.md": {"nonempty": true, "required": ["--profiles"],
"forbidden": ["replace every placeholder with its exact full filesystem path"]}}`.
Literal checks cover specific requirements; they do not establish semantic quality.
Review-free acceptance requires an independent exact JSON value for every file.
The checker also supports trusted, prehashed JSON evidence reports; it never runs
output code or accepts a worker's unsupported claim that tests passed.

`task_close` checks output bytes and saves a receipt before committing Closed.
Failed checks leave the task Active, prevent dependency release and persist a
terminal stop. Do not repair or resume a held failure. Missing or changed receipts
also prevent successful final verification. Dependency bytes are rechecked before
the next task starts, and extra sprint output files prevent acceptance. Declared
output tasks cannot be dropped to release dependents; record a failed sprint when
the required work cannot be completed. Existing command/closure recovery
rules and warning-only note lengths are unchanged.

After normal agent closure, inspect current acceptance:

```powershell
python -m aether.tasker sprint verify "$sprint" --json
python -m aether.tasker sprint status "$sprint" --json
```

Exact JSON can report `accepted`. Prose reports `review_required` until the
operator actually reads it and writes a review map outside worker outputs:

```json
{
  "1": {
    "verdict": "PASS",
    "reviewer": "actual reviewer name",
    "receipt_sha256": "copy tasks.1.receipt_sha256 from sprint verify",
    "notes": "What was reviewed, findings and limits"
  }
}
```

Use FAIL when the review finds a defect. Then file that decision:

```powershell
python -m aether.tasker sprint accept "$sprint" --reviews C:/Work/reviews.json --json
```

The command rechecks bytes and writes a separate acceptance receipt. Status uses
the saved decision but rechecks current files, so changed outputs or contracts
invalidate acceptance. A bare PASS never suffices. `verify` and `accept` exit 0
only for accepted work, otherwise 2. Timer exit 0 means normal process completion;
it does not mean unreviewed prose is accepted. Existing closed runs retain their
historical state and report `legacy_unverified`; they are not migrated or rerun.
Embedding callers use `new_sprint(..., contracts=...)` for the same native gate.

`--profiles` takes a full file path; `--deployment` takes a configured profile
name. Quote paths containing spaces. Work from the packaged Bridge program
directory and use the returned `$sprint` path instead of reconstructing it.
These checks assume trusted operator contracts/reviews; host command execution
is not an OS sandbox against malicious code.
