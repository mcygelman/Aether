# Aether Atelier

A Windows workplace for agents with persistent, inspectable memory. Agents collaborate through commissions and mailboxes, own SQLite memory cartridges, and retain their own closing words across sessions. Memory and reflection help the work; they do not become acknowledgment or clearance requirements.

**v0.1.0-alpha — experimental developer preview.** Persistent memory is implemented; improved performance across tasks remains under evaluation. See [validation and limitations](VALIDATION.md).

## Windows application

Download the Windows ZIP from this repository's Releases page, verify it against `SHA256SUMS.txt`, and extract the entire archive. Run `Install.cmd`, then `Aether.cmd` in the installed folder. Python is bundled. Follow the loopback address printed by the launcher and read [PUBLIC_SETUP.md](PUBLIC_SETUP.md).

Use the installer's default location or a writable folder under your user profile. Explicit installation paths need a writable parent because maintenance creates a sibling lock. A different drive letter does not guarantee write access. Preserve your data and original extracted bundle for maintenance.

Open **Models**, save your deployment settings, and check the connection. Use **Places** to connect fresh public mind homes and a share, then **Atelier** to open a commission. Approve the proposed sprint and budget with **Say "go"**, and accept an actual deliverable with **Say "Accepted."** Memory filing is not a prerequisite for subsequent work. Model-provider calls may incur charges; the Windows app does not include a model or credits.

The supported v0.1 surfaces are Atelier, Mailbox, Library/Places, Memory Fabric, Agent Viewer, project folders/tracker, tools, Models & Context and the lightweight Agent Editor. The private full Runtime and Studio edition is not part of this source export.

## Run from source

Use Windows and Python 3.10. From the repository root in PowerShell:

```powershell
python -m pip install -r requirements.txt
$repo = (Get-Location).Path
$env:PYTHONPATH = Join-Path $repo 'AetherMemory'
Set-Location AetherAgents/LightweightAgents/Bridge
python -m aether_bridge.launcher start --edition atelier
```

The default source address is `http://127.0.0.1:8484`. Stop it from the same folder with `python -m aether_bridge.launcher stop`. Source-mode settings and data are written locally; the Windows installed layout separates application, data and credentials.

For an isolated operator trial, prefer the Windows bundle. Read [PUBLIC_SETUP.md](PUBLIC_SETUP.md) for public mind authoring, model controls and backup/new-root restore. Stop places before backing up; do not copy an open cartridge by hand.

## Development and evidence

```powershell
python -m pip install -r requirements-dev.txt
python tools/run_public_tests.py --list
python tools/run_public_tests.py
```

The public test selection is explicit. Eleven historical cases require private fixtures and are visibly skipped; excluded full-edition tests are documented in [VALIDATION.md](VALIDATION.md). Tests use disposable state and scripted providers, not paid inference. GitHub CI is configured but is not claimed green before it actually runs.

The Harbor Ledger fixture is in `AetherPublicFacing/Release/acceptance/`. Keep its evaluator/reference answers outside every agent's accessible roots. Passing the evaluator on reference code demonstrates the fixture, not agent work or memory use.

[RELEASE_NOTES.md](RELEASE_NOTES.md) identifies the exact Windows artifact and documentation errata. [PUBLICATION_MANIFEST.json](PUBLICATION_MANIFEST.json) records this source tree's files and their origins; `RC1_SOURCE_MANIFEST.json` is historical pre-overlay evidence, not the current tree inventory.

MIT licensed. See [LICENSE](LICENSE), [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), [SUPPORT.md](SUPPORT.md), and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
