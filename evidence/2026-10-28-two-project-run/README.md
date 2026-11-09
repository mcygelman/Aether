# Two projects in a row, live: October 28, 2026

The whole workflow, run through the application on a hosted model (DeepSeek), by the people who build Aether, on
their development machine. The files here are the record of that run, unedited.

## What ran

- **The build:** an earlier Atelier edition build of October 28 (manifest `6f10e2b04b1b21d9…`). The current build differs
  in one renamed internal package, one safety fix, cleaned-up comments and wording, and updated tests;
  [VALIDATION.md](../../VALIDATION.md) lists them.
- **The team:** three fresh agents made with `prepare-minds`: a lead (AtelierMaster), a developer (Scribe) and a
  reviewer (Auditor). Each started with an empty memory apart from a few labelled worked examples.
- **The work:** the Harbor Ledger demonstration (`AetherPublicFacing/Release/acceptance/`). Project A asks for a
  currency report on top of a shared helper that hides a rounding defect. Project B asks for a further report in the
  same code. Neither brief mentions the defect.
- **The operator:** its words were fixed before the run: "Approve the plan" when a plan waited, "Your call; disclose
  it in the review" for questions, "Accepted." once the reviewer passed the work and the lead said it was ready, and
  "Carry on." if the room went in circles. Nothing else was said to the team.
- **The grading:** after each acceptance, a checklist the team never saw ran against a copy of the delivered code.

## What happened

| | Project A | Project B |
|---|---|---|
| Plan | 3 sprints, approved with one click | 3 sprints, approved with one click |
| Review | The reviewer ran the tests itself and wrote four findings; the developer corrected two | The reviewer ran the tests itself; its three findings were each answered |
| Accepted | Yes; three closing notes filed | Yes; three closing notes filed |
| Hidden checklist | **27 of 27** ([grade-project-a.json](grade-project-a.json)) | **29 of 29**, including the rounding defect ([grade-project-b.json](grade-project-b.json)) |
| Model calls | 41 | 32 |

- **Memory, used without being asked.** In Project A the lead found the rounding defect and filed "parse_minor uses
  float and will mis-round" into its own memory. At its first wake in Project B it loaded that note, planned the fix in
  the shared helper, and wrote that the approach was "settled by the conventions and by my memory". See the note in
  [daybook-project-b.jsonl](daybook-project-b.jsonl) and its `facts.memory.loaded` list.
- **A crash, then a sign-in.** Between the plan and acceptance of Project A the server was killed while it was hosting
  the team, between turns: no agent turn was running at that moment (the turns were shorter than the watcher's poll). The record has the kill at 14:21:22 and the new hosting heartbeat at 14:21:40, 18 seconds later, which
  includes restarting the server and signing in with the same account; nothing else was done
  ([crash-and-resume.txt](crash-and-resume.txt)).
- **Data kept.** After both projects: backup, verify, restore to a new folder, uninstall and reinstall. All 191 data
  files were byte-identical after reinstall ([hashes-before-backup.json](hashes-before-backup.json),
  [hashes-after-reinstall.json](hashes-after-reinstall.json)). The restored copy matched apart from lock and instance
  files, which are left out by design ([hashes-after-restore.json](hashes-after-restore.json)), and the memory
  databases matched by content ([sqlite-compare-after-restore.json](sqlite-compare-after-restore.json)).
- **The key stayed out.** The model key's value appeared in none of the 1,252 files of the installation, the backup
  and the run's evidence ([key-scan.json](key-scan.json)).

## The three numbers

| | Project A | Project B |
|---|---|---|
| Thinking per minute of overhead | 0.20 | 4.06 |
| Refusals per hundred calls | 0 | 0 |
| Approvals per sprint | 0.33 | 0.33 |

Project A's first number is low because it counts ten minutes the team waited for the test operator's late approval
(the waiting script watched for the wrong text). The team did not wait idle: it built, reviewed and corrected the
first version in that time. Files: [three-numbers-project-a.json](three-numbers-project-a.json),
[three-numbers-project-b.json](three-numbers-project-b.json).

## Check it yourself

- `python evidence/2026-10-28-two-project-run/verify_daybooks.py` recomputes both daybooks' hash chains with Aether's
  own digest. Each note carries its position, the previous note's hash and its own. Compare the last hashes it prints
  with the ones published in [VALIDATION.md](../../VALIDATION.md); a match means no note was changed after
  publication.
- `python evidence/2026-10-28-two-project-run/scan_for_key.py report.json <folders>` is the key scan, with the key in
  `AETHER_API_KEY`; it prints counts and matching paths, never the value.
- [operator-policy.md](operator-policy.md) has the operator's fixed words and what the operator did, in order.
- Each daybook line is one note: who wrote it, to whom, the text, any files by hash, the model cost and the facts the
  Atelier read from it.

## Limits

- One run of each project, on the development machine, by the builders. An independent review and a run on a newly
  built machine come next.
- The grade files check what the delivered code does. They say `independent_review: NOT_RUN` and
  `memory_benefit: NOT_ASSESSED` because the grader does not judge those. The review and the use of memory are shown
  in the daybooks instead.
- Closing receipts in the daybooks name absolute paths on the development machine. They are kept as written, because
  editing them would break the hash chain. The files attached to notes are not included here.
