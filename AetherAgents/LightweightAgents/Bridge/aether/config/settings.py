"""Installation settings (MOD-04): the one front door for an installation's own settings.

Each setting is declared once, here: its key, the module that owns it, what it means, how a
value is checked, and its default. Values live in one file per installation
(`Lightweight/.aether_control/installation_settings.json`, git-ignored), journaled by
ConfigJournal: who changed what, when, why and the old value, and any change can be undone.

Inputs:  a key from the catalog; for writes, the value, who (`by`) and why (`reason`).
Outputs: InstallationSettings (get, set, remove, list, history, undo) and CATALOG.
Never:   accepts an unknown key (a typo must not quietly create a setting), stores a value
         its validator refused, or writes without who and why.

CLI:     python -m aether.config list
         python -m aether.config get KEY
         python -m aether.config set KEY VALUE --by WHO --reason WHY
         python -m aether.config remove KEY --by WHO --reason WHY
         python -m aether.config history [KEY]
         python -m aether.config undo CHANGE_ID --by WHO --reason WHY

Model and deployment settings keep their own store (aether.models.settings_store), with its
own operator attribution and audit; this front door is for installation-wide settings.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from aether.paths import LIGHTWEIGHT_ROOT

from .journal import Change, ConfigJournal, ConfigJournalError

DEFAULT_PATH = LIGHTWEIGHT_ROOT / ".aether_control" / "installation_settings.json"


class SettingsError(ConfigJournalError):
    pass


def absolute_folder(value: Any) -> str:
    """An absolute folder path, stored with forward slashes. It need not exist yet."""
    if not isinstance(value, str) or not value.strip():
        raise SettingsError("a folder path is required")
    path = Path(value.strip())
    if not path.is_absolute():
        raise SettingsError(f"use an absolute folder path, not {value!r}")
    if path.exists() and not path.is_dir():
        raise SettingsError(f"{value!r} exists and is not a folder")
    return path.as_posix()


_NAME = re.compile(r"[A-Za-z0-9_-]+")
_MCP_FIELDS = {"name", "command", "timeout_seconds", "cwd", "max_response_bytes", "prefix"}


def _positive(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise SettingsError(f"{field} must be a positive, finite number")
    return value


def mcp_servers(value: Any) -> List[Dict[str, Any]]:
    """External MCP servers (Toolbelt): a list, or its JSON text, of {"name", "command": [argv...],
    "timeout_seconds", optional "cwd" (absolute), "max_response_bytes", "prefix"}. Moved here from
    aether.toolbelt.mcp_client on 2026-10-08 so Configuration validates it without importing Toolbelt."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise SettingsError(f"mcp_servers must be a JSON list: {exc}") from exc
    if not isinstance(value, list):
        raise SettingsError("mcp_servers must be a list of server entries")
    out, names = [], set()
    for i, entry in enumerate(value):
        where = f"mcp_servers[{i}]"
        if not isinstance(entry, dict):
            raise SettingsError(f"{where} must be an object")
        unknown = set(entry) - _MCP_FIELDS
        if unknown:
            raise SettingsError(f"{where}: unknown field(s) {sorted(unknown)}")
        name = entry.get("name")
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise SettingsError(f"{where}: name must use only A-Z a-z 0-9 _ -")
        if name in names:
            raise SettingsError(f"{where}: server name {name!r} is used twice")
        names.add(name)
        command = entry.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(a, str) and a for a in command):
            raise SettingsError(f"{where}: command must be a non-empty argv list of strings")
        clean: Dict[str, Any] = {"name": name, "command": list(command)}
        try:
            clean["timeout_seconds"] = _positive(entry.get("timeout_seconds"), "timeout_seconds")
            if "max_response_bytes" in entry:
                clean["max_response_bytes"] = int(_positive(entry["max_response_bytes"], "max_response_bytes"))
        except SettingsError as exc:
            raise SettingsError(f"{where}: {exc}") from exc
        if "cwd" in entry:
            if not isinstance(entry["cwd"], str) or not Path(entry["cwd"]).is_absolute():
                raise SettingsError(f"{where}: cwd must be an absolute folder path")
            clean["cwd"] = Path(entry["cwd"]).as_posix()
        if "prefix" in entry:
            if not isinstance(entry["prefix"], str) or (entry["prefix"] and not _NAME.fullmatch(entry["prefix"])):
                raise SettingsError(f"{where}: prefix must use only A-Z a-z 0-9 _ - (empty: no namespace)")
            clean["prefix"] = entry["prefix"]
        out.append(clean)
    return out


@dataclass(frozen=True)
class Setting:
    key: str
    module: str                       # the module that owns the meaning of this setting
    meaning: str
    validate: Callable[[Any], Any]
    default: Any = None


CATALOG: Dict[str, Setting] = {s.key: s for s in (
    Setting("memory.minds_folder", "memory",
            "Where agents' memory cartridges live: outside the repository. "
            "A profile's memory/cartridge.json names its cartridge inside this folder.",
            absolute_folder),
    Setting("toolbelt.mcp_servers", "toolbelt",
            "External MCP servers whose tools join the tool belt unapproved, "
            "each {name, command argv, timeout_seconds, optional cwd, max_response_bytes, prefix}. "
            "Listing a server here does not start it; starting a configured server needs the operator's approval.",
            mcp_servers, default=[]),
)}


class InstallationSettings:
    def __init__(self, path=DEFAULT_PATH, clock=None):
        self.path = Path(path)
        self.journal = ConfigJournal(self.path, clock) if clock else ConfigJournal(self.path)

    @staticmethod
    def setting(key: str) -> Setting:
        if key not in CATALOG:
            raise SettingsError(f"unknown setting {key!r}; known: {', '.join(sorted(CATALOG))}")
        return CATALOG[key]

    def get(self, key: str) -> Any:
        return self.journal.get(key, self.setting(key).default)

    def set(self, key: str, value: Any, *, by: str, reason: str) -> Change:
        checked = self.setting(key).validate(value)
        return self.journal.set(key, checked, by=by, reason=reason)

    def remove(self, key: str, *, by: str, reason: str) -> Change:
        self.setting(key)
        return self.journal.remove(key, by=by, reason=reason)

    def list(self) -> List[dict]:
        stored = self.journal.all()
        return [{"key": s.key, "module": s.module, "value": stored.get(s.key, s.default),
                 "is_default": s.key not in stored, "meaning": s.meaning}
                for s in sorted(CATALOG.values(), key=lambda s: s.key)]

    def history(self, key: Optional[str] = None, limit: int = 50) -> List[Change]:
        if key is not None:
            self.setting(key)
        return self.journal.history(key, limit)

    def undo(self, change_id: int, *, by: str, reason: str = "undo") -> Change:
        # Only catalog settings, as set/remove require. A file that is another store's journal (the
        # tool registry) keeps its own guarded undo; this generic path must not bypass it (MVP-05 R1).
        target = next((c for c in self.journal._changes() if c.id == change_id), None)
        if target is not None:
            self.setting(target.key)
        return self.journal.undo(change_id, by=by, reason=reason)
