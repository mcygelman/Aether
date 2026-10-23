> Release clarification: use the default installation location or an explicit folder under your own user profile with a writable parent. Maintenance creates a sibling lock. The drive-root paths below are illustrative and are not a promise of write permission. See RELEASE_NOTES.md and VALIDATION.md for current acceptance scope; the full repeated A/B journey was omitted by the release owner.

# Aether Atelier v0.1 — installed candidate guide

This is a local acceptance candidate. Record its ZIP and manifest hashes; installed and live acceptance remain to be completed. Memory and closing reflections are advisory. There is no memory acknowledgment or clearance step.

## Install and start on Windows

Extract the entire Windows ZIP to a short, new folder. The bundled Python needs no system Python installation. Double-click `Install.cmd`, then double-click `Aether.cmd` in the installed folder shown by the installer. Keep that console open. Use the browser address printed there: the installed launcher chooses an available loopback port. Closing the console stops the server; use Places to stop a running place first and let its current turn finish.

For isolated acceptance, from the extracted folder in PowerShell use your chosen short, separate application, data and credential paths:

```powershell
.\runtime\python.exe -I -B .\aether.py install --destination C:\av01\app --data-root C:\av01\data --credentials-root C:\av01\credentials --explicit-campaign-folders --json
C:\av01\app\runtime\python.exe -I -B C:\av01\app\aether.py serve --governed
```

These are examples, not existing folders to replace. `--governed` is supplied by `Aether.cmd` and selects the established bound-installation entry; it adds no operator ceremony. Re-running installation with the same three roots updates application files while preserving user data. To uninstall, close Aether and run `Uninstall.cmd --destination C:\av01\app` from the original extracted bundle. It retains data and credentials. The installed executable cannot delete itself; retain the extracted bundle for maintenance.

The source ZIP uses a different layout: install Python 3.10 with Jinja2 3.1.6 and MarkupSafe 2.1.3, change to `AetherAgents/LightweightAgents/Bridge`, put the extracted `AetherMemory` directory on PYTHONPATH, and run `python -m aether_bridge.launcher start --edition atelier`. The source launcher defaults to port 8484. Use its matching `stop` command to stop that source server. Do not use the source launcher to stop an installed server.

## Models, minds and a place

Open **Models** in the navigation (`/settings/models-context/`). The page displays the profiles file in use; the installed file is `<data>/b/runtime_profiles.json`.

For DeepSeek, **Set up DeepSeek** prepares a profile. Select a credential reference, such as an environment-variable name already available to the application process. The credential field takes the name, not the key. Review the endpoint, requested model and context/output budgets, then **Save changes** and **Check connection**. A model listing does not verify context capacity. The starter budgets are configuration values, not a provider guarantee. Do not run inference until the acceptance run's finite call budget and provider are recorded.

For LM Studio, use the operator-selected loaded model and its actual endpoint, model identifier and budgets. Do not load or replace a model as part of setup without the operator's instruction. The inherited advanced Runtime admission controls are for the full edition and are not required for Atelier. Atelier reads the selected profile directly and automatically retries after relevant input or deployment changes; a temporary bad reload keeps an already working client until the repaired profile becomes usable.

The candidate includes public demonstration templates and a helper to author fresh homes. From the installed folder:

```powershell
.\runtime\python.exe -I -B .\resources\AetherPublicFacing\Release\acceptance\runner.py prepare-minds --dest C:\av01\data\homes --share C:\av01\data\atelier --deployment YOUR_SAVED_PROFILE_NAME
```

Use a new destination. This writes the supplied public identities and fresh cartridges; it makes no model calls and copies no resident memory. Add `--roles lone_master` for the separate single-mind case.

Open **Places** (`/projects/places`) and connect the share, the generated home folders, and `<data>/b/runtime_profiles.json`. Start the place when ready to run. The **Agents** page discovers these homes and shows their memory. Each place uses the same named deployment selected when the homes were authored.

Open **Atelier**, choose **New commission**, and send the objective letter with a finite budget (for example, `Budget: 20 model calls`). The exact approved run budget controls acceptance; do not adopt this example silently. Read the proposed sprint and use **Say "go"** when authorizing it. Review the actual deliverable and use **Say "Accepted."** when satisfied. Minds write their own closing words. Their wording is not fabricated by the host, and memory filing is not a prerequisite for the next work.

## Data and acceptance workload

The installation binding is `installation.json`. The share contains `daybooks/<commission>/daybook.jsonl`, hash-addressed attached files, `mailboxes/`, and `work/<commission>/`. A home contains `participant.json`, identity, continuity, and `memory/.cartridge.json`; its pointer may name a cartridge elsewhere. Workplace settings store the share and home locations. Record all chosen locations; do not assume every external share or cartridge is under the installation data root.

Acceptance materials are in `resources/AetherPublicFacing/Release/acceptance/`. Its README documents Project A, restart and Project B. Mount only the prepared agent inputs into the commission workspace; the evaluator and reference answers stay outside every agent's accessible roots. The matrix begins with NOT RUN results. The development machine's offline results do not establish independent Windows or live acceptance.

## Backup and new-root restore

Stop each place through Places (or the shipped `code/tools/atelier_place.py stop --share <share> --place <name>` using the bundled Python), wait for its turn to finish, then close Mission Control. Use the installed `Maintenance.cmd`; do not copy an open SQLite cartridge by hand.

```text
Maintenance.cmd prepare-backup --destination C:\av01-backup --plan C:\av01-plan.json
Maintenance.cmd backup --plan C:\av01-plan.json --plan-sha256 <hash returned by prepare-backup>
Maintenance.cmd verify-backup --archive C:\av01-backup --manifest-sha256 <returned manifest hash>
Maintenance.cmd restore --archive C:\av01-backup --manifest-sha256 <returned manifest hash> --target C:\av01-restored
```

Add `--mount share=<absolute share>` and additional disjoint mounts to `prepare-backup` for roots outside the bound data root, including external cartridges. Keep destinations short and separate. The existing plan/hash pairing identifies exactly what is backed up; it is not a condition for ordinary work or memory use. Credentials are excluded: retain or restore those separately through their provider.

Restore creates a new folder and leaves originals untouched. After verification, update Places to the restored share and homes (and the intended profiles file), then start the place. A relative cartridge pointer moves with its home; an absolute external pointer must be updated to the verified relocated cartridge path. If staging was interrupted before `restore.json` was produced, preserve the original roots and retry into another new folder. A complete staging receipt says `VERIFIED_STAGING_NOT_ACTIVE`; it does not silently activate anything.

The legacy same-path `switch` is optional and is not this v0.1 restore route. Its unfinished-transaction and history-preservation safeguards remain intact. No reconciliation acknowledgment is added to the new-root path.
