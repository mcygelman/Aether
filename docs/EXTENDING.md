# Extending Aether

## Add an agent

- **The starter team:** **Staff → AI → Create starter team** makes a lead, a developer and a reviewer
  with fresh memories and registers them. No model is called.
- **From a template:** **Staff → AI → Create a specialist**, choose a shipped role, and Aether creates a fresh home
  and memory for it. Nothing starts until you add it to a project's **Team** tab.
- **Author your own template:** open the **Agent Editor**, set the role, subject, allowed tools and the project
  folders it may write, save a revision, then **Create specialist** from that revision.
- **Register an existing home:** **Staff → AI → Add an existing agent folder** registers a home folder you already have. No
  identity or memory is copied.
- **Scripted:** `AetherPublicFacing/Release/acceptance/runner.py prepare-minds` writes a lead, a developer and a
  reviewer with fresh memories, without calling a model.

An agent home is a folder with `participant.json` (id, kind, name, model connection, allowed folders), its identity
text, its continuity and a pointer to its memory cartridge. `aether/atelier/participants.py` reads and writes it.

## Connect an external tool server (MCP)

The **Data Sources** page (**Settings → Overview → Data Sources & Reports → Open Data Sources**) registers a reporting service that speaks the Model Context Protocol, over HTTPS or as a
local command. A source manager chooses which of its tools are exposed and to which roles. The service enforces its
own data permissions. The installation setting `toolbelt.mcp_servers` lists further MCP servers; listing one does not
start it.

## Add a tool for agents (code)

Agents' tools are declared in `tool_schemas()` and carried out in `Tools.call()`, both in
`aether/atelier/turn.py`. To add one:

1. Add its JSON schema to `tool_schemas()` (name, description, parameters).
2. Handle it in `Tools.call()`. Resolve any path with `self._resolve(rel, 'read' | 'write')`, which keeps the agent
   inside its allowed folders. Append a side-log row to `self.actions`, and return a short text result.
3. Decide who may use it: `aether/atelier/participant_policy.py` `permits()` checks the agent's `allowed_tools`.
4. Add an offline test beside `tests/test_test_run_environment.py`, which shows how to drive `Tools` without a
   model.

### Worked example: `count_lines`

A tool that tells an agent how many lines a file has, without reading it all.

In `tool_schemas()`, add one entry to the list it returns, beside `list_dir`:

```python
f('count_lines', 'Count the lines of a text file under the commission root.', dict(path=dict(type='string')), ['path']),
```

In `Tools.call()`, beside the `list_dir` branch:

```python
if name == 'count_lines':
    path = self._resolve(args['path'], 'read')          # refuses anything outside the agent's folders
    lines = path.read_text(encoding='utf-8', errors='replace').count('\n')
    self.actions.append(dict(kind='read', pointer=str(args['path']), tags=['count'], line=f'counted {lines} lines'))
    return f'{lines} lines'
```

A test, in the style of `tests/test_test_run_environment.py` (no model is called):

```python
from aether.atelier import turn

class Mind:
    roots = {}
    allowed_tools = None
    def technical(self):
        return False

def test_count_lines(tmp_path):
    root = tmp_path / 'commission'
    root.mkdir()
    (root / 'notes.txt').write_text('a\nb\nc\n', encoding='utf-8')
    tools = turn.Tools(Mind(), root)
    assert tools.call('count_lines', {'path': 'notes.txt'}) == '3 lines'
    assert 'outside' in tools.call('count_lines', {'path': '../escape.txt'})   # refused, as a message to the agent
```

Run it with `python tools/run_public_tests.py --group bridge --select tests/test_count_lines.py` after adding the file
to the `tests` list in `AetherPublicFacing/Release/public_surface.json`.

Keep tools small and their results short; the model reads every byte. Anything that runs code an agent wrote should
use the environment `test_environment()` gives, so it never inherits keys.

## Add a model provider

Connections are profiles in `<data>/b/runtime_profiles.json`, edited on the **Models** page. Servers that speak the
OpenAI-compatible chat API work through **This computer**, **Another computer on my network** or a custom hosted
address. A provider with a different API needs an adapter in `aether/backends/` and its entry in `aether/models/`.
