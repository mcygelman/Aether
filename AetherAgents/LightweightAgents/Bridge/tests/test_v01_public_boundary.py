"""v0.1 public boundary: the declared public surface imports and works with the Runtime blocked.

The contract is AetherPublicFacing/Release/public_surface.json. Each check runs a child Python whose
sitecustomize makes every `aether.runtime` import fail, so a public module that reaches the Runtime fails
here rather than in someone's fresh installation.
"""
import json

import pytest

import v01_public_support as support

KEYS = {"schema_version", "modules", "files", "tests", "launch", "private_exclusions"}


def test_the_surface_contract_names_real_public_files_only():
    surface = support.surface()
    assert set(surface) == KEYS and surface["schema_version"] == 1
    listed = surface["files"] + surface["tests"]
    assert not [f for f in listed if "*" in f or not (support.REPOSITORY / f).is_file()]
    assert not [f for f in listed if any(f.startswith(x) for x in surface["private_exclusions"])]
    assert not [m for m in surface["modules"] if m == "aether.runtime" or m.startswith("aether.runtime.")]
    assert surface["launch"]["argv"][-2:] == ["--edition", "atelier"]
    roots = {"aether": "AetherAgents/LightweightAgents/Bridge/", "aether_bridge": "AetherAgents/LightweightAgents/Bridge/",
             "memory_fabric": "AetherMemory/"}
    sources = {roots[m.split(".")[0]] + m.replace(".", "/") for m in surface["modules"]}
    assert not sorted(s for s in sources if s + ".py" not in surface["files"] and s + "/__init__.py" not in surface["files"])


def test_every_public_module_imports_with_the_runtime_blocked(tmp_path):
    outcome = support.import_failures(tmp_path, support.surface()["modules"])
    assert outcome == {"failed": {}, "runtime_modules": []}


@pytest.mark.parametrize("forbidden", [
    "from aether.runtime import planner\n",            # the Runtime itself
    "import aether.mission_control.server\n",          # the full server, which reaches the Runtime
])
def test_the_check_detects_an_introduced_forbidden_import(tmp_path, forbidden):
    probe = tmp_path / "probe"
    probe.mkdir()
    (probe / "v01_probe_public.py").write_text("from aether.atelier import atelier_http\n" + forbidden, encoding="utf-8")
    outcome = support.import_failures(tmp_path, ["aether.atelier.atelier_http", "v01_probe_public"], extra_path=[probe])
    assert list(outcome["failed"]) == ["v01_probe_public"]
    assert "BLOCKED v0.1 public boundary" in outcome["failed"]["v01_probe_public"]


def test_public_actions_work_and_private_routes_fail_plainly(tmp_path):
    done = support.run_scenario(tmp_path)
    assert done.returncode == 0, done.stderr[-4000:]
    outcome = json.loads(done.stdout.strip().splitlines()[-1])
    assert outcome["runtime_modules"] == []
    statuses = outcome["results"]
    assert statuses["GET /api/campaign-state?id=no-such-commission"] == 404
    assert statuses["GET /api/health host=evil.example:80"] == 403
    assert statuses["POST /api/atelier/open xsite"] == 403
