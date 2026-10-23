"""Simulator box: the calibration bench for the "too many dials" problem.

Inputs:  scenarios (JSON): memories and links placed on a timeline, then
         steps (recall, load, bury, raise...) with expectations.
Outputs: ScenarioReport per scenario: every check, pass or fail, and why.

Each scenario runs against the REAL engine on an in-memory cartridge with a
simulated clock, so what passes here is what agents will get. Change a
profile or a setting, re-run the library, and see which situations improve
or break. That is how the dials get tuned before agents depend on them.

Scenario format (see scenarios/*.json):
{
  "name": "...", "origin": "...", "start": "2026-10-06T09:00:00Z",
  "agent": "test-agent",
  "settings": {"surface_threshold": 0.3},          # optional overrides
  "memories": [{"id", "title", "body", "kind", "tags", "category", "stratum",
                "switch_in_days", "importance", "proprietary", "at_day"}],
  "links": [{"a", "b", "relation", "strength", "at_day"}],
  "steps": [{"day": 2, "do": "recall", "tags": [...],
             "expect_surface": [...], "expect_absent": [...], "expect_first": "id",
             "expect_above": [["x", "y"]], "expect_through_fog": [...],
             "expect_recurring": [...], "note": "why this matters"},
            {"day": 3, "do": "load", "ids": [...]},
            {"day": 4, "do": "bury", "id": "..."},
            {"day": 5, "do": "raise", "id": "...", "importance": 0.9},
            {"day": 6, "do": "check", "expect_stronger": [["x", "y"]],
             "expect_note_count": 3}]
}
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import List

from .errors import MemoryFabricError
from .clock import FixedClock, add_days
from .engine import MemoryEngine


@dataclass
class CheckResult:
    day: float
    action: str
    detail: str
    passed: bool
    failures: List[str] = field(default_factory=list)
    note: str = ""


@dataclass
class ScenarioReport:
    name: str
    origin: str
    checks: List[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def counts(self) -> tuple:
        good = sum(1 for c in self.checks if c.passed)
        return good, len(self.checks)

    def render(self) -> str:
        good, total = self.counts
        lines = [f"{'PASS' if self.passed else 'FAIL'}  {self.name}  ({good}/{total} checks)"
                 f"  - {self.origin}"]
        for c in self.checks:
            mark = "ok " if c.passed else "XX "
            lines.append(f"    {mark} day {c.day:g} {c.action}: {c.detail}")
            for failure in c.failures:
                lines.append(f"         ! {failure}")
        return "\n".join(lines)


class ScenarioError(MemoryFabricError, ValueError):
    pass


def load_scenario(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def load_library(directory: str) -> List[dict]:
    names = sorted(n for n in os.listdir(directory) if n.endswith(".json"))
    return [load_scenario(os.path.join(directory, n)) for n in names]


def run_scenario(scenario: dict, *, settings: dict = None, profiles: dict = None) -> ScenarioReport:
    """Run one scenario. `settings`/`profiles` override the defaults (calibration)."""
    start = scenario["start"]
    clock = FixedClock(start)
    engine = MemoryEngine.create(":memory:", scenario.get("agent", "test-agent"), clock=clock, seed_resources=False)
    for key, value in {**scenario.get("settings", {}), **(settings or {})}.items():
        engine.change_setting(key, value, changed_by="simulator", reason="scenario calibration")
    for name, values in (profiles or {}).items():
        engine.tune_profile(name, changed_by="simulator", reason="calibration", **values)

    events = []
    for m in scenario.get("memories", []):
        events.append((m.get("at_day", 0), 0, "memory", m))
    for l in scenario.get("links", []):
        events.append((l.get("at_day", 0), 1, "link", l))
    for i, s in enumerate(scenario.get("steps", [])):
        events.append((s["day"], 2 + i, "step", s))
    events.sort(key=lambda e: (e[0], e[1]))

    report = ScenarioReport(scenario["name"], scenario.get("origin", ""))
    for day, _, kind, item in events:
        clock.set(add_days(start, day))
        if kind == "memory":
            engine.remember(
                item["title"], item.get("body", item["title"]), item["kind"], item.get("tags", []),
                note_id=item["id"], category=item.get("category", "Information"),
                stratum=item.get("stratum"), switch_in_days=item.get("switch_in_days"),
                importance=item.get("importance"), proprietary=item.get("proprietary", False),
                written_by=item.get("written_by", engine.agent_id))
        elif kind == "link":
            engine.link(item["a"], item["b"], item.get("relation", "context"), item.get("strength"))
        else:
            report.checks.append(_run_step(engine, day, item))
    engine.close()
    return report


def _run_step(engine: MemoryEngine, day: float, step: dict) -> CheckResult:
    action = step["do"]
    note = step.get("note", "")
    if action == "load":
        engine.load(step["ids"])
        return CheckResult(day, "load", f"pulled up {step['ids']}", True, note=note)
    if action == "bury":
        engine.bury(step["id"])
        return CheckResult(day, "bury", f"buried {step['id']}", True, note=note)
    if action == "raise":
        engine.raise_note(step["id"], step.get("importance"))
        return CheckResult(day, "raise", f"raised {step['id']}", True, note=note)
    if action == "recall":
        result = engine.recall(step["tags"])
        ids = result.ids()
        by_id = {h.note_id: h for h in result.headers}
        failures = []
        for wanted in step.get("expect_surface", []):
            if wanted not in ids:
                failures.append(f"expected '{wanted}' to surface")
        for unwanted in step.get("expect_absent", []):
            if unwanted in ids:
                failures.append(f"expected '{unwanted}' to stay down "
                                f"(reach {by_id[unwanted].reach})")
        if "expect_first" in step and (not ids or ids[0] != step["expect_first"]):
            failures.append(f"expected '{step['expect_first']}' first, got {ids[:1]}")
        for higher, lower in step.get("expect_above", []):
            if higher not in ids or (lower in ids and ids.index(higher) > ids.index(lower)):
                failures.append(f"expected '{higher}' ranked above '{lower}'")
        for fog in step.get("expect_through_fog", []):
            if fog not in by_id or not by_id[fog].through_fog:
                failures.append(f"expected '{fog}' to surface through the fog")
        for rec in step.get("expect_recurring", []):
            if rec not in by_id or not by_id[rec].recurring:
                failures.append(f"expected '{rec}' flagged recurring")
        shown = ", ".join(f"{h.note_id}({h.score:.2f}{'*' if h.through_fog else ''})"
                          for h in result.headers) or "nothing"
        return CheckResult(day, "recall", f"{step['tags']} -> {shown}", not failures,
                           failures, note)
    if action == "check":
        failures = []
        for stronger, weaker in step.get("expect_stronger", []):
            s1, s2 = engine.note(stronger)["strength"], engine.note(weaker)["strength"]
            if not s1 > s2:
                failures.append(f"expected '{stronger}' ({s1:.3f}) stronger than '{weaker}' ({s2:.3f})")
        if "expect_note_count" in step:
            count = engine.cart.conn.execute("SELECT count(*) FROM notes").fetchone()[0]
            if count != step["expect_note_count"]:
                failures.append(f"expected {step['expect_note_count']} notes, found {count}")
        return CheckResult(day, "check", "state checks", not failures, failures, note)
    raise ScenarioError(f"unknown step action: {action}")


def run_library(directory: str, **overrides) -> List[ScenarioReport]:
    return [run_scenario(s, **overrides) for s in load_library(directory)]


def render_library(reports: List[ScenarioReport]) -> str:
    good = sum(1 for r in reports if r.passed)
    checks = [c for r in reports for c in r.checks]
    lines = [f"MEMORY FABRIC CALIBRATION: {good}/{len(reports)} scenarios pass, "
             f"{sum(c.passed for c in checks)}/{len(checks)} checks", ""]
    for report in reports:
        lines.append(report.render())
    return "\n".join(lines)
