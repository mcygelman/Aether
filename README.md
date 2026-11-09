# Aether v0.2.0 · prototype

Aether is an MIT-licensed AI workspace created by Michel Cygelman. Talk with a model
or an agent, organize work into missions and sprints, and inspect the memories that
an agent carries between conversations. It runs locally on Windows and connects to
the model provider you configure.

## Start with a small demonstration

Ask the app's Meridian agent to remember a fictional rule. Open its memory viewer
and inspect the saved note. End the conversation, start a new Chat, select Meridian
again, and ask for the rule without supplying the answer. Compare the returned ID
with the stored note. The November 8 rehearsal verified a direct read of the same
note in a distinct, initially empty conversation. See [VALIDATION.md](VALIDATION.md).

This is persistent application memory. New conversations still receive the agent's
identity and continuity context. A memory's **Current until** date marks when it
starts fading faster; it is not a deletion date.

## Download and install

From this repository's [Releases](https://github.com/mcygelman/Aether/releases), use:

- **Aether-Windows-0.2.0.zip**: application and bundled Python runtime.
- **Aether-Source-0.2.0.zip**: the reviewed public source and documentation.
- **SHA256SUMS.txt**: hashes for the release files.

Extract the Windows ZIP into its own folder. To have an AI assistant guide the
installation, ask it to follow the included **INSTALL_WITH_AI.md**. You can also
use **Install.cmd**, then the installed **Aether.cmd**. On first start, use the
displayed default Admin account and complete the welcome with your own password
and model-provider configuration. For an existing profile, use its existing account.
The installer and data locations are described in [PUBLIC_SETUP.md](PUBLIC_SETUP.md).

## What is included

- **Chat:** model-only conversations or conversations with app Meridian. Meridian
  can file and retrieve its own memories; **End conversation** files its closing words.
- **Memory viewer:** inspect notes, IDs, links, strength and the Recorded date.
- **Missions, Roadmaps and Active Sprints:** organize work and follow execution.
- **Staff and Teach:** choose the supplied staff and teaching material, and create
  supported specialists through the workspace.
- **Accounts, API Keys and model connections:** local account roles and usage grants.
- **Creations, Help and local records:** inspect outputs and find operating guidance.

The Chat agent loop permits seven requests per operation: six with tools and one
final request without tools. Provider latency and output vary. Use the exact
validation scope in [VALIDATION.md](VALIDATION.md), including its outstanding checks.

## Run from source

Use Windows and Python 3.10. From the repository root in PowerShell:

```powershell
python -m pip install -r requirements.txt
$aetherRepo = (Get-Location).Path
$env:PYTHONPATH = Join-Path $aetherRepo 'AetherMemory'
Set-Location AetherAgents/LightweightAgents/Bridge
python -m aether_bridge.launcher start --edition atelier
```

The source server normally listens on http://127.0.0.1:8484. Stop it from the same
folder with `python -m aether_bridge.launcher stop`. **Help → About** shows the version.

## Tests and implementation

```powershell
python -m pip install -r requirements-dev.txt
python tools/run_public_tests.py --list
python tools/run_public_tests.py
```

The public tests use scripted providers. The application lives under
`AetherAgents/LightweightAgents/Bridge/`; the memory engine is under
`AetherMemory/memory_fabric/`. Start with [Architecture](docs/ARCHITECTURE.md),
[Extending Aether](docs/EXTENDING.md) and the [Glossary](docs/GLOSSARY.md).
The [publication inventory](PUBLICATION_MANIFEST.json) records the selected source
and documentation hashes; it excludes its own file from that hash list.

## Scope

This is a Windows prototype. The recorded live Chat checks used DeepSeek; other
provider paths have distinct coverage. Independent evaluation and a clean-machine
installation remain separate work. File tools have application boundaries, but
generated code runs with your user rights rather than in an operating-system sandbox.
Hosted models receive the context you send them. See [SECURITY.md](SECURITY.md).

MIT licensed. See [LICENSE](LICENSE), [RELEASE_NOTES.md](RELEASE_NOTES.md),
[CONTRIBUTING.md](CONTRIBUTING.md), [SUPPORT.md](SUPPORT.md) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
