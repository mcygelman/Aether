# The operator's fixed policy, and what the operator did

The words the test operator could use were written down before the run, so that the result reflects the product and
not coaching. Nothing else was said to the team.

| When | The operator wrote |
|---|---|
| A plan (or a later sprint) waited for approval | "Approve the plan" (the page's button, which posts "go"; "Approve sprint N" when one sprint waited) |
| The team asked the operator a question | "Your call; disclose it in the review." |
| The reviewer's latest verdicts were PASS and the lead said the work was ready | "Accepted." |
| The room went in circles before it was ready | "Carry on." (never needed) |

The brief for each project was the fixture's own `TASK.md` text plus a budget line. The grader ran on a copy of the
delivered code, after acceptance.

## What the operator did, in order (times are UTC, from the daybooks and the run log)

1. Installed the build, created the first Admin, connected DeepSeek and checked the connection (no model call).
2. Created the three agents with `prepare-minds` (no model call) and added them in Staff.
3. **Project A:** opened it with its brief (budget 150 calls), added the team, and joined the network (14:09).
4. Approved the plan at 14:19:44. This was ten minutes late: the waiting script watched for the wrong text. The team
   had already built, reviewed and corrected the first version in the meantime.
5. **Crash:** killed the server at 14:21:22 while it was hosting, started it again and signed in with the same
   account. It did **not** select Join network or anything else. Hosting resumed with a new heartbeat at 14:21:40.
6. Accepted Project A at 14:21:51.
7. **Restart:** Leave network, stopped the server, started it, signed in, joined again.
8. **Project B:** opened it with its brief (budget 150 calls), added the same team, approved the plan, and accepted at
   14:25:26.
9. Backup, verify, restore to a new folder, uninstall and reinstall, then the key scan (`scan_for_key.py`).
