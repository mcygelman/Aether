"""The tool belt: scoped project-root file tools, mounted twice
(OpenAI `tools` for missions; MCP for LM Studio chat — see mcp_server.py).

Safety model (S00063 design §3, as amended by Open_Decisions D3 ruling):
- Scoped roots only; path traversal rejected mechanically.
- Writes AUTO-APPLY inside registered roots (D3: lean in, least friction),
  but every write is journaled with the prior content for review/undo.
- Per-root mode: 'auto' (default), 'deny' (read-only), or authenticated
  'sealed' storage.
- Resolved-path write protections can span every root alias.
- Tool results are ground truth; caps on result size are enforced here.
- Which tools may run is the shared tool registry's call (registry.py, Bridge Overhaul M2):
  a belt offers exactly the tools the registry would let it run, and refuses the rest.
"""
from __future__ import annotations

import fnmatch
import itertools
import json
import os
import re
import time
import uuid
import threading
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

MAX_RESULT_CHARS = 24_000   # per tool call; counts against the packet budget
MAX_SEARCH_HITS = 200
TRUNCATION_PRIOR_BYTES = 2_048
TRUNCATION_RATIO = 0.5
from aether.integrity.undo_journal import JOURNAL_IDS as _JOURNAL_IDS, write_entry  # noqa: E402


class ToolError(Exception):
    pass


class ToolAdmissionError(ToolError):
    """Admission refused before implementation entry (not an uncertain effect)."""
    dispatch_entered = False


class ToolAdmissionUnavailable(ToolAdmissionError):
    """Registry unavailable; refresh/retry admission, never reconcile an effect."""


@dataclass
class Root:
    name: str
    path: Path
    mode: str = "auto"  # auto | deny | sealed
    keyfile: Path | None = None


@dataclass(frozen=True)
class PathRule:
    path: Path
    recursive: bool
    reason: str
    exempt_roots: frozenset[str] = frozenset()


@dataclass(frozen=True)
class NamedFolderRule:
    base: Path
    names: frozenset[str]
    reason: str
    exempt_roots: frozenset[str] = frozenset()


class ToolBelt:
    def __init__(self, journal_dir: str | Path, execution_runner=None,
                 max_result_chars: int = MAX_RESULT_CHARS, calculate_enabled: bool = True,
                 registry=None):
        # max_result_chars: per-tool-call truncation size. Defaults to the module constant
        # (unchanged behavior for every existing LW caller). Ultralight/Studio microcase
        # sessions pass MAX_RESULT_CHARS_MICRO (microcase.py) here instead -- Roadmap U-03
        # tuned-tier wiring.
        self.roots: dict[str, Root] = {}
        self.journal_dir = Path(journal_dir)
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        self.execution_runner = execution_runner
        self.max_result_chars = max_result_chars
        self._write_protections: list[tuple[Path, bool, str]] = []
        self._write_gates: list[PathRule] = []
        self._named_folder_write_gates: list[NamedFolderRule] = []
        self._access_denials: list[PathRule] = []
        self._denied_filenames: dict[str, str] = {}
        self._gated_filenames: dict[str, str] = {}
        self._approval_project_root: Path | None = None
        self._approval_manifest_path: Path | None = None
        self._delete_file_enabled = False
        self.calculate_enabled = bool(calculate_enabled)
        # The shared tool registry (M2); None means this installation's registry file.
        self.registry = registry
        self._attached: dict[str, tuple[dict, object]] = {}
        self._source_definitions = {}
        self._tool_lock = threading.RLock()

    def _registry(self):
        if self.registry is None:
            from aether.toolbelt.registry import ToolRegistry
            self.registry = ToolRegistry()
        return self.registry

    def attach(self, schema: dict, handler, *, source: str, source_definition=None) -> None:
        """An external tool (for example an MCP server's) joins the belt. It is announced to the
        registry and stays unoffered and unrunnable until an operator approves it."""
        from aether.toolbelt.registry import tool_name
        name = tool_name(schema)
        if name in {tool_name(t) for t in self._all_tools()} and name not in self._attached:
            raise ToolError(f"tool '{name}' already exists on this belt")
        with self._tool_lock:
            schema = deepcopy(schema)
            definition = deepcopy(schema if source_definition is None else source_definition)
            self._registry().register(schema, source=source, source_definition=definition)
            self._attached[name] = (schema, handler)
            self._source_definitions[name] = definition

    def detach(self, name: str) -> bool:
        """An attached tool leaves this belt: neither offered nor run here again. Its registry
        record and history stay. True if it was attached."""
        with self._tool_lock:
            self._source_definitions.pop(name, None)
            return self._attached.pop(name, None) is not None

    def tool_registry(self):
        """The shared tool registry this belt obeys."""
        return self._registry()

    # ---- root registry ----

    def register_root(self, name: str, path: str | Path, mode: str = "auto",
                      keyfile: str | Path | None = None) -> None:
        p = Path(path).resolve()
        if not p.is_dir():
            raise ToolError(f"root '{name}': not a directory: {p}")
        if mode not in ("auto", "deny", "sealed"):
            raise ToolError(f"root '{name}': bad mode '{mode}'")
        resolved_key = Path(keyfile).resolve() if keyfile is not None else None
        if mode == "sealed" and resolved_key is None:
            raise ToolError(f"root '{name}': sealed mode requires keyfile")
        if mode != "sealed" and resolved_key is not None:
            raise ToolError(f"root '{name}': keyfile is only valid for sealed mode")
        if resolved_key is not None:
            from aether.integrity import sealbox
            try:
                sealbox.read_key(resolved_key)
            except sealbox.SealboxError as exc:
                raise ToolError(str(exc)) from exc
        self.roots[name] = Root(name=name, path=p, mode=mode, keyfile=resolved_key)

    def _resolve(self, root: str, rel: str) -> tuple[Root, Path]:
        if root not in self.roots:
            raise ToolError(f"unknown root '{root}' (known: {sorted(self.roots)})")
        r = self.roots[root]
        p = (r.path / rel).resolve()
        if not (p == r.path or r.path in p.parents):
            raise ToolError(f"path escapes root '{root}': {rel}")
        self._check_access(p)
        return r, p

    def protect_path(self, path: str | Path, recursive: bool = False,
                     reason: str = "protected path") -> None:
        """Deny writes to one resolved target, or to an entire target subtree.

        Protections apply across every registered root alias. This closes the
        gap where a read-only named root could be bypassed through a broader
        writable mount.
        """
        target = Path(path).resolve()
        item = (target, bool(recursive), str(reason))
        if item not in self._write_protections:
            self._write_protections.append(item)

    def gate_path(self, path: str | Path, recursive: bool = False,
                  reason: str = "operator-gated path",
                  exempt_roots: set[str] | frozenset[str] = frozenset()) -> None:
        """Require an exact operator-manifest approval before mutation.

        Gated rules apply across aliases unless a named, separately governed
        root is explicitly exempted. Hard ``protect_path`` rules still dominate.
        """
        rule = PathRule(
            Path(path).resolve(), bool(recursive), str(reason),
            frozenset(str(name) for name in exempt_roots),
        )
        if rule not in self._write_gates:
            self._write_gates.append(rule)

    def gate_filename(self, filename: str,
                      reason: str = "operator-gated file class") -> None:
        name = Path(filename).name.casefold()
        if not name or name in {".", ".."}:
            raise ToolError("gated filename must name one file")
        self._gated_filenames[name] = str(reason)

    def gate_named_folders(
        self, base: str | Path, names: set[str] | frozenset[str],
        reason: str = "operator-gated folder class",
        exempt_roots: set[str] | frozenset[str] = frozenset(),
    ) -> None:
        normalized = frozenset(str(name).casefold() for name in names if str(name))
        if not normalized:
            raise ToolError("named folder gate requires at least one folder name")
        rule = NamedFolderRule(
            Path(base).resolve(), normalized, str(reason),
            frozenset(str(name) for name in exempt_roots),
        )
        if rule not in self._named_folder_write_gates:
            self._named_folder_write_gates.append(rule)

    def enable_file_deletion(self) -> None:
        """Expose journaled file deletion for an explicitly authorized session."""
        self._delete_file_enabled = True

    def deny_path(self, path: str | Path, recursive: bool = False,
                  reason: str = "no-access path") -> None:
        rule = PathRule(Path(path).resolve(), bool(recursive), str(reason))
        if rule not in self._access_denials:
            self._access_denials.append(rule)

    def deny_filename(self, filename: str,
                      reason: str = "no-access file class") -> None:
        name = Path(filename).name.casefold()
        if not name or name in {".", ".."}:
            raise ToolError("denied filename must name one file")
        self._denied_filenames[name] = str(reason)

    def configure_operator_write_gate(
        self, project_root: str | Path, manifest_path: str | Path
    ) -> None:
        project = Path(project_root).resolve()
        manifest = Path(manifest_path).resolve()
        if not (manifest == project or project in manifest.parents):
            raise ToolError("operator approval manifest must be inside the project root")
        self._approval_project_root = project
        self._approval_manifest_path = manifest
        # The authority file is never writable through agent tools, even if an
        # operator accidentally lists it as its own approved target.
        self.protect_path(
            manifest, recursive=False,
            reason="operator approval manifest is operator-side only",
        )

    @staticmethod
    def _rule_matches(rule: PathRule, target: Path) -> bool:
        return target == rule.path or (
            rule.recursive and rule.path in target.parents
        )

    def _access_denial_reason(self, path: Path) -> str | None:
        target = path.resolve()
        if target.name.casefold() in self._denied_filenames:
            return self._denied_filenames[target.name.casefold()]
        for rule in self._access_denials:
            if self._rule_matches(rule, target):
                return rule.reason
        return None

    def _check_access(self, path: Path) -> None:
        reason = self._access_denial_reason(path)
        if reason:
            raise ToolError(f"access blocked by {reason}: {path.resolve()}")

    def _check_write_protection(self, path: Path) -> None:
        target = path.resolve()
        for protected, recursive, reason in self._write_protections:
            blocked = target == protected or (
                recursive and protected in target.parents
            )
            if blocked:
                raise ToolError(
                    f"write blocked by {reason}: {target}"
                )

    def _matching_write_gates(self, root: Root, path: Path) -> list[str]:
        target = path.resolve()
        reasons = [
            rule.reason for rule in self._write_gates
            if root.name not in rule.exempt_roots and self._rule_matches(rule, target)
        ]
        filename_reason = self._gated_filenames.get(target.name.casefold())
        if filename_reason:
            reasons.append(filename_reason)
        for rule in self._named_folder_write_gates:
            if root.name in rule.exempt_roots:
                continue
            if target == rule.base or rule.base not in target.parents:
                continue
            folder_parts = target.relative_to(rule.base).parts[:-1]
            if any(part.casefold() in rule.names for part in folder_parts):
                reasons.append(rule.reason)
        return list(dict.fromkeys(reasons))

    def _load_operator_approvals(self) -> tuple[dict[Path, dict], str | None]:
        manifest = self._approval_manifest_path
        project = self._approval_project_root
        if manifest is None or project is None:
            return {}, "operator approval manifest is not configured"
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return {}, f"operator approval manifest unavailable or invalid: {exc}"
        if not isinstance(raw, dict) or raw.get("schema") != "AETHER-APPROVED-WRITES-1":
            return {}, "operator approval manifest has the wrong schema"
        entries = raw.get("approvals")
        if not isinstance(entries, list):
            return {}, "operator approval manifest approvals must be a list"

        approvals: dict[Path, dict] = {}
        for index, item in enumerate(entries):
            if not isinstance(item, dict):
                return {}, f"operator approval entry {index} must be an object"
            rel = item.get("path")
            approved_by = item.get("approved_by")
            reference = item.get("reference")
            if not all(isinstance(value, str) and value.strip()
                       for value in (rel, approved_by, reference)):
                return {}, (
                    f"operator approval entry {index} requires nonblank "
                    "path, approved_by, and reference"
                )
            rel_path = Path(rel)
            if rel_path.is_absolute() or ".." in rel_path.parts:
                return {}, f"operator approval entry {index} escapes the project root"
            target = (project / rel_path).resolve()
            if not (target == project or project in target.parents):
                return {}, f"operator approval entry {index} escapes the project root"
            if target in approvals:
                return {}, f"duplicate operator approval path: {rel}"
            approvals[target] = {
                "path": target.relative_to(project).as_posix(),
                "approved_by": approved_by.strip(),
                "reference": reference.strip(),
            }
        return approvals, None

    def operator_approval_summary(self) -> dict:
        approvals, error = self._load_operator_approvals()
        return {
            "manifest": (
                str(self._approval_manifest_path)
                if self._approval_manifest_path is not None else None
            ),
            "approved_path_count": len(approvals),
            "valid": error is None,
            "error": error,
        }

    def _authorize_mutation(self, op: str, root: Root, path: Path) -> dict | None:
        self._check_access(path)
        self._check_write_protection(path)
        reasons = self._matching_write_gates(root, path)
        if not reasons:
            return None

        approvals, manifest_error = self._load_operator_approvals()
        approval = approvals.get(path.resolve()) if manifest_error is None else None
        if approval is not None:
            return approval

        entry_id = self._journal(
            "operator_gated_mutation", root, path, None,
            status="refused",
            requested_op=op,
            gate_reasons=reasons,
            approval_manifest=(
                str(self._approval_manifest_path)
                if self._approval_manifest_path is not None else None
            ),
            manifest_error=manifest_error,
        )
        detail = manifest_error or "exact path is absent from the operator approval manifest"
        raise ToolError(
            f"operator-gated {op} refused for {path.resolve()}: {detail}; "
            f"journal={entry_id}"
        )

    # ---- tools (read) ----

    def list_roots(self) -> list[dict]:
        out = []
        for r in self.roots.values():
            protections = []
            for protected, recursive, reason in self._write_protections:
                if protected == r.path or r.path in protected.parents or (
                    recursive and protected in r.path.parents
                ):
                    protections.append({
                        "path": str(protected),
                        "recursive": recursive,
                        "reason": reason,
                    })
            item = {
                "name": r.name,
                "path": str(r.path),
                "mode": (
                    "read-only (reads allowed, writes denied)"
                    if r.mode == "deny"
                    else r.mode
                ),
            }
            if protections:
                item["write_protections"] = protections
            gates = []
            for rule in self._write_gates:
                if r.name in rule.exempt_roots:
                    continue
                if rule.path == r.path or r.path in rule.path.parents or (
                    rule.recursive and rule.path in r.path.parents
                ):
                    gates.append({
                        "path": str(rule.path),
                        "recursive": rule.recursive,
                        "reason": rule.reason,
                    })
            if self._gated_filenames:
                gates.extend({
                    "filename": name, "recursive": True, "reason": reason,
                } for name, reason in sorted(self._gated_filenames.items()))
            for rule in self._named_folder_write_gates:
                if r.name in rule.exempt_roots:
                    continue
                if rule.base == r.path or r.path in rule.base.parents or rule.base in r.path.parents:
                    gates.append({
                        "base": str(rule.base),
                        "folder_names": sorted(rule.names),
                        "recursive": True,
                        "reason": rule.reason,
                    })
            if gates:
                item["operator_gated_writes"] = gates
            denials = []
            for rule in self._access_denials:
                if rule.path == r.path or r.path in rule.path.parents or (
                    rule.recursive and rule.path in r.path.parents
                ):
                    denials.append({
                        "path": str(rule.path),
                        "recursive": rule.recursive,
                        "reason": rule.reason,
                    })
            if self._denied_filenames:
                denials.extend({
                    "filename": name, "recursive": True, "reason": reason,
                } for name, reason in sorted(self._denied_filenames.items()))
            if denials:
                item["no_access"] = denials
            out.append(item)
        return out

    def list_dir(self, root: str, path: str = ".") -> list[dict]:
        _, p = self._resolve(root, path)
        if not p.is_dir():
            raise ToolError(f"not a directory: {path}")
        out = []
        for e in sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
            if self._access_denial_reason(e):
                continue
            out.append({"name": e.name, "type": "file" if e.is_file() else "dir",
                        "size": e.stat().st_size if e.is_file() else None})
        return out

    def read_file(self, root: str, path: str, offset: int = 0, limit: int = 0,
                  preserve_line_endings: bool = False) -> str:
        r, p = self._resolve(root, path)
        if not p.is_file():
            raise ToolError(f"not a file: {path}")
        if type(preserve_line_endings) is not bool:
            raise ToolError('preserve_line_endings must be a boolean')
        if preserve_line_endings:
            if r.mode == 'sealed':
                raise ToolError('exact text reads require an unsealed root')
            with p.open('r', encoding='utf-8', errors='strict', newline='') as stream:
                text = stream.read()
        else:
            text = self._read_text(r, p)
        lines = text.splitlines(keepends=True)
        if offset or limit:
            lines = lines[offset: offset + limit if limit else None]
        out = "".join(lines)
        if len(out) > self.max_result_chars:
            # 2026-08-02 (Launch Seam Leg 4b, live incident): this notice used to report a
            # CHARACTER count and then advise "use offset/limit" -- but offset/limit are LINE
            # indices (see the slice above). A heavyweight agent followed the advice literally,
            # asked for offset=24001 on a 143-line file, and would have received an empty read.
            # The tool knows both numbers; emit the continuation line, not a character count.
            kept = out[:self.max_result_chars]
            lines_kept = (offset or 0) + kept.count("\n")
            total_lines = len(text.splitlines())
            out = kept + (
                f"\n[TRUNCATED at {self.max_result_chars} chars after line {lines_kept} "
                f"of {total_lines} — continue with offset={lines_kept} "
                f"(offset/limit are LINE numbers, not characters)]")
        return out

    def glob(self, root: str, pattern: str) -> list[str]:
        r, _ = self._resolve(root, ".")
        hits = []
        for p in r.path.rglob("*"):
            if self._access_denial_reason(p):
                continue
            rel = p.relative_to(r.path).as_posix()
            if fnmatch.fnmatch(rel, pattern):
                hits.append(rel)
                if len(hits) >= MAX_SEARCH_HITS:
                    break
        return hits

    def search(self, root: str, regex: str, glob_filter: str = "*") -> list[dict]:
        r, _ = self._resolve(root, ".")
        try:
            rx = re.compile(regex)
        except re.error as e:
            raise ToolError(f"bad regex: {e}")
        hits = []
        for p in r.path.rglob("*"):
            if self._access_denial_reason(p):
                continue
            if not p.is_file():
                continue
            rel = p.relative_to(r.path).as_posix()
            if not fnmatch.fnmatch(rel, glob_filter):
                continue
            try:
                for i, line in enumerate(self._read_text(r, p).splitlines(), 1):
                    if rx.search(line):
                        hits.append({"file": rel, "line": i, "text": line.strip()[:300]})
                        if len(hits) >= MAX_SEARCH_HITS:
                            return hits
            except OSError:
                continue
        return hits

    def _read_text(self, root: Root, path: Path) -> str:
        if root.mode != "sealed":
            return path.read_text(encoding="utf-8", errors="replace")
        from aether.integrity import sealbox
        try:
            return sealbox.open_file(path, root.keyfile, require_sealed=True)
        except sealbox.SealboxError as exc:
            raise ToolError(f"sealed read failed for {path.name}: {exc}") from exc

    # ---- tools (write — auto-apply + journal, D3 ruling) ----

    def write_file(self, root: str, path: str, content: str) -> dict:
        r, p = self._resolve(root, path)
        if r.mode == "deny":
            raise ToolError(f"root '{root}' is read-only")
        rel_parts = p.relative_to(r.path).parts
        if rel_parts and rel_parts[0].lower() == ".aether":
            raise ToolError("runtime audit path '.aether' is reserved and cannot be written by agent tools")
        approval = self._authorize_mutation("write_file", r, p)
        if p.exists() and not p.is_file():
            raise ToolError(f"not a file: {path}")
        prior = self._read_text(r, p) if p.is_file() else None
        journal_prior = p.read_bytes() if p.is_file() and r.mode == "sealed" else prior
        prior_bytes = len(prior.encode("utf-8")) if prior is not None else None
        new_bytes = len(content.encode("utf-8"))
        truncation_flagged = (
            prior_bytes is not None
            and prior_bytes > TRUNCATION_PRIOR_BYTES
            and new_bytes < prior_bytes * TRUNCATION_RATIO
        )
        journal_detail = {"prior_bytes": prior_bytes, "new_bytes": new_bytes}
        if truncation_flagged:
            journal_detail.update({
                "flagged": True,
                "flag": "possible_truncation",
                "detail": (
                    f"replacement shrank existing file from {prior_bytes} bytes "
                    f"to {new_bytes} bytes"
                ),
            })
        if approval is not None:
            journal_detail.update({
                "operator_gate": "approved",
                "approved_path": approval["path"],
                "approved_by": approval["approved_by"],
                "approval_reference": approval["reference"],
            })
        entry_id = self._journal("write_file", r, p, journal_prior, **journal_detail)
        p.parent.mkdir(parents=True, exist_ok=True)
        if r.mode == "sealed":
            from aether.integrity import sealbox
            sealbox.write_sealed_file(p, content, r.keyfile)
        else:
            with p.open("w", encoding="utf-8", newline="\n") as f:
                f.write(content)
        return {
            "applied": True,
            "journal": entry_id,
            "bytes": new_bytes,
            "truncation_flagged": truncation_flagged,
            "operator_gate": "approved" if approval is not None else None,
            "approval_reference": approval["reference"] if approval is not None else None,
        }

    def edit_file(self, root: str, path: str, old_string: str, new_string: str,
                  replace_all: bool = False) -> dict:
        # 2026-07-20 (Sprint 00076, live incident, DeepSeek DECISIONS.md truncation): write_file
        # is whole-file-only -- any change requires regenerating the entire file, which silently
        # truncated a ~15KB file when the calling model's own output budget (shared with its
        # reasoning trace) ran out mid-regeneration. edit_file changes only the matched snippet,
        # so no agent -- local or API -- needs to reproduce a whole file just to change part of
        # it. Same guards as write_file (deny-root, reserved .aether path), same journal/undo
        # discipline via _journal(). Uniqueness-by-default (fail-closed, not first-match): a
        # silent multi-match replace is exactly the kind of surprising behavior this project's
        # design philosophy argues against elsewhere (sanctum.py, sealbox.py) -- replace_all is
        # an explicit opt-in, never a default.
        r, p = self._resolve(root, path)
        if r.mode == "deny":
            raise ToolError(f"root '{root}' is read-only")
        rel_parts = p.relative_to(r.path).parts
        if rel_parts and rel_parts[0].lower() == ".aether":
            raise ToolError("runtime audit path '.aether' is reserved and cannot be written by agent tools")
        approval = self._authorize_mutation("edit_file", r, p)
        if not p.is_file():
            raise ToolError(f"not a file: {path}")
        prior = self._read_text(r, p)
        if old_string == "":
            raise ToolError("old_string must not be empty")
        count = prior.count(old_string)
        if count == 0:
            raise ToolError(f"text not found in {path} -- old_string must match the file's "
                            f"current content exactly, including whitespace")
        if count > 1 and not replace_all:
            raise ToolError(f"old_string is not unique in {path} ({count} matches) -- add more "
                            f"surrounding context to make it unique, or pass replace_all=true")
        new_content = prior.replace(old_string, new_string) if replace_all \
            else prior.replace(old_string, new_string, 1)
        journal_prior = p.read_bytes() if r.mode == "sealed" else prior
        detail = {}
        if approval is not None:
            detail.update({
                "operator_gate": "approved",
                "approved_path": approval["path"],
                "approved_by": approval["approved_by"],
                "approval_reference": approval["reference"],
            })
        entry_id = self._journal("edit_file", r, p, journal_prior, **detail)
        if r.mode == "sealed":
            from aether.integrity import sealbox
            sealbox.write_sealed_file(p, new_content, r.keyfile)
        else:
            with p.open("w", encoding="utf-8", newline="\n") as f:
                f.write(new_content)
        return {
            "applied": True,
            "journal": entry_id,
            "replacements": count if replace_all else 1,
            "bytes": len(new_content.encode("utf-8")),
            "operator_gate": "approved" if approval is not None else None,
            "approval_reference": approval["reference"] if approval is not None else None,
        }

    def delete_file(self, root: str, path: str) -> dict:
        """Delete one file with a journaled, undoable prior snapshot."""
        if not self._delete_file_enabled:
            raise ToolError("delete_file is unavailable for this session")
        r, p = self._resolve(root, path)
        if r.mode == "deny":
            raise ToolError(f"root '{root}' is read-only")
        rel_parts = p.relative_to(r.path).parts
        if rel_parts and rel_parts[0].lower() == ".aether":
            raise ToolError("runtime audit path '.aether' is reserved and cannot be written by agent tools")
        approval = self._authorize_mutation("delete_file", r, p)
        if not p.is_file():
            raise ToolError(f"not a file: {path}")
        prior = p.read_bytes() if r.mode == "sealed" else self._read_text(r, p)
        detail = {}
        if r.mode == "sealed":
            detail["prior_storage"] = "sealed-bytes"
        if approval is not None:
            detail.update({
                "operator_gate": "approved",
                "approved_path": approval["path"],
                "approved_by": approval["approved_by"],
                "approval_reference": approval["reference"],
            })
        entry_id = self._journal("delete_file", r, p, prior, **detail)
        p.unlink()
        return {
            "applied": True,
            "deleted": True,
            "journal": entry_id,
            "operator_gate": "approved" if approval is not None else None,
            "approval_reference": approval["reference"] if approval is not None else None,
        }

    def undo(self, entry_id: str) -> dict:
        return undo(self.journal_dir, entry_id)

    def _journal(self, op: str, root: Root, p: Path,
                 prior: str | bytes | None, **detail) -> str:
        # The writer lives in Integrity since 2026-10-08 (SANCTUM's reseal uses it too).
        return write_entry(self.journal_dir, op, root.name, root.path, p, prior, **detail)

    # ---- OpenAI tool schemas + dispatch ----

    def openai_tools(self) -> list[dict]:
        """The tools offered: exactly those the registry would let this belt run."""
        from aether.config.journal import ConfigJournalError
        with self._tool_lock:
            registry = self._registry()
            tools, definitions = deepcopy(self._all_tools()), deepcopy(self._source_definitions)
        try:
            return registry.offered(tools, definitions=definitions)
        except ConfigJournalError as exc:
            raise ToolAdmissionUnavailable(
                'Tool admission registry unavailable; no tool entered. Retry after the registry transaction finishes; '
                'if it persists, inspect Mission Control /projects/tools.') from exc

    def _all_tools(self) -> list[dict]:
        def fn(name, desc, props, req):
            return {"type": "function", "function": {"name": name, "description": desc,
                    "parameters": {"type": "object", "properties": props, "required": req}}}
        root_p = {"root": {"type": "string", "description": "Registered project root name"}}
        path_p = {"path": {"type": "string", "description": "Path relative to the root"}}
        tools = [
            fn("list_roots", "List the registered project roots you may work in.", {}, []),
            fn("list_dir", "List a directory inside a root.", {**root_p, **path_p}, ["root"]),
            fn("read_file", "Read a file inside a root. Large files: pass offset/limit (lines).",
               {**root_p, **path_p,
                "offset": {"type": "integer"}, "limit": {"type": "integer"},
                "preserve_line_endings": {"type": "boolean", "description": "Preserve exact UTF-8 line endings on unsealed roots; bounds still apply."}}, ["root", "path"]),
            fn("glob", "Find files matching a glob pattern (e.g. '**/*.md').",
               {**root_p, "pattern": {"type": "string"}}, ["root", "pattern"]),
            fn("search", "Regex-search file contents inside a root.",
               {**root_p, "regex": {"type": "string"},
                "glob_filter": {"type": "string", "description": "Optional filename glob"}},
               ["root", "regex"]),
            fn("write_file", "Write/overwrite a file inside a root (auto-applies; journaled).",
               {**root_p, **path_p, "content": {"type": "string"}}, ["root", "path", "content"]),
            fn("edit_file", "Replace an exact snippet of text inside an existing file "
               "(auto-applies; journaled). old_string must match the file's current content "
               "exactly and must be unique unless replace_all is set. Prefer this over "
               "write_file for any change smaller than the whole file -- especially on files "
               "too large to safely regenerate in full.",
               {**root_p, **path_p, "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean", "description": "Replace every match instead "
                                "of requiring exactly one match (default false)."}},
               ["root", "path", "old_string", "new_string"]),
        ]
        if self.calculate_enabled:
            from aether.toolbelt.calc import CalcTool
            tools.append(CalcTool.schema)
        if self._delete_file_enabled:
            tools.append(
                fn("delete_file", "Delete one file inside a writable root (auto-applies, journaled, and undoable).",
                   {**root_p, **path_p}, ["root", "path"])
            )
        if self.execution_runner and self.execution_runner.enabled:
            tools.append(fn("run_command", "Run an operator-allowlisted argv command in the active project; result is authoritative.",
                            {"argv": {"type": "array", "items": {"type": "string"}},
                             "cwd": {"type": "string", "description": "Project-relative cwd"},
                             "timeout_seconds": {"type": "integer"}}, ["argv"]))
        tools.extend(schema for schema, _ in self._attached.values())
        return tools

    def dispatch(self, name: str, args: dict):
        """Persist exactly the enforced admission before entering the implementation.

        This receipt is authority evidence, never a claim that an effect succeeded.
        The shared path lock linearizes concurrent withdrawal against dispatch.
        """
        from aether.toolbelt.registry import definition_digest, ToolRegistryError, ToolRegistryUnavailable
        from aether.config.journal import ConfigJournalError
        with self._tool_lock:
            args = deepcopy(args)
            schema = next((t for t in self._all_tools() if t['function']['name'] == name), None)
            if schema is None:
                if name == 'calculate' and not self.calculate_enabled:
                    from aether.toolbelt.calc_adapter import refusal
                    return refusal('calculate is disabled for this agent')
                raise ToolAdmissionError(f"unknown tool '{name}' (unavailable on this belt); refresh the offered tools before retrying")
            schema = deepcopy(schema)
            definition = deepcopy(self._source_definitions.get(name, schema))
            handler = self._attached.get(name, (None, None))[1]
            registry = self._registry()
        # Neither belt state nor registry authority is locked during arbitrary tool code.
        # The admitted handler/definitions are frozen even if this belt is reattached.
        try:
            with registry.admission(schema, source_definition=definition) as authorization:
                receipt = dict(schema='aether-tool-admission-1', id=uuid.uuid4().hex,
                               tool=name, definition=schema, source_definition=definition,
                               arguments_sha256=definition_digest(args),
                               authorization=authorization, at=time.time(),
                               disposition='ADMITTED' if authorization['allowed'] else 'REFUSED')
                folder = self.journal_dir / 'tool-admissions'
                folder.mkdir(parents=True, exist_ok=True)
                with (folder / (receipt['id'] + '.json')).open('x', encoding='utf-8', newline='\n') as stream:
                    stream.write(json.dumps(receipt, sort_keys=True, ensure_ascii=False, allow_nan=False) + '\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                if not authorization['allowed']:
                    raise ToolAdmissionError(authorization['reason'])
        except ToolRegistryError as exc:
            if not isinstance(exc, ToolRegistryUnavailable):
                raise ToolAdmissionError(str(exc)) from exc
            raise ToolAdmissionUnavailable(str(exc) + ' No tool entered.') from exc
        except ConfigJournalError as exc:
            raise ToolAdmissionUnavailable(
                'Tool admission registry unavailable; no tool entered. Retry after the registry transaction finishes; '
                'if it persists, inspect Mission Control /projects/tools.') from exc
        except (OSError, ValueError) as exc:
            raise ToolAdmissionError(f'Tool admission could not be persisted; no tool entered: {exc}. '
                                     'Inspect the journal storage and retry admission.') from exc
        try:
            return self._dispatch_admitted(name, args, handler=handler)
        except ToolAdmissionError as exc:
            # A nested tool may refuse admission after this outer implementation
            # has already effected something. Do not misclassify that as no entry.
            exc.dispatch_entered = True
            raise

    def _dispatch_admitted(self, name, args, *, handler=None):
        if handler is not None:
            return handler(args)
        if name == 'calculate':
            from aether.toolbelt.calc_adapter import dispatch, refusal
            return dispatch(args) if self.calculate_enabled else refusal('calculate is disabled for this agent')
        table = {"list_roots": self.list_roots, "list_dir": self.list_dir,
                 "read_file": self.read_file, "glob": self.glob,
                 "search": self.search, "write_file": self.write_file,
                 "edit_file": self.edit_file}
        if self._delete_file_enabled:
            table["delete_file"] = self.delete_file
        if name not in table:
            if name == "run_command" and self.execution_runner:
                try:
                    return self.execution_runner.run(**args)
                except (OSError, ValueError):
                    raise
                except Exception as e:
                    raise ToolError(str(e)) from e
            raise ToolError(f"unknown tool '{name}'")
        # 2026-07-20 (DEC-044, live bug, DeepSeek session): a model can call a tool with a
        # missing required argument (e.g. `search` without `root`) or an unexpected keyword --
        # that's a malformed CALL, not a crash. Previously this path had no guard at all (only
        # the run_command branch above did), so a bad call raised a raw TypeError/KeyError that
        # wasn't a ToolError, wasn't caught by studio.py's `except ToolError`, and propagated all
        # the way to the HTTP layer as an unhandled 500 -- killing the turn AND leaving
        # self.messages with a dangling assistant tool_calls entry with no matching tool
        # response, corrupting every later turn in the same session. Catching broadly here and
        # re-raising as ToolError restores the same "malformed call is a recoverable message the
        # model sees and can retry from" behavior every other tool already had.
        try:
            return table[name](**args)
        except ToolError:
            raise
        except TypeError as e:
            # 2026-08-02 (Launch Seam Leg 4a): a WRONG root already gets a helpful message from
            # _resolve ("unknown root 'x' (known: [...])"). A MISSING root got a raw Python
            # TypeError and nothing else -- less help for the easier mistake. Name the required
            # arguments and the roots that actually exist, so the next call can be correct.
            required = {
                "list_dir": "root, path", "read_file": "root, path[, offset, limit, preserve_line_endings]",
                "glob": "root, pattern", "search": "root, regex[, glob_filter]",
                "write_file": "root, path, content",
                "edit_file": "root, path, old_string, new_string[, replace_all]",
                "delete_file": "root, path",
            }.get(name)
            hint = f" expected: {name}({required})" if required else ""
            raise ToolError(
                f"bad arguments for '{name}': {e}.{hint} "
                f"known roots: {sorted(self.roots)}"
            ) from e


def _journal_entry(journal_dir: str | Path, entry_id: str) -> dict:
    journal_path = Path(journal_dir) / "journal.jsonl"
    if not journal_path.is_file():
        raise ToolError(f"journal not found: {journal_path}")
    found = None
    with journal_path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("id") == entry_id:
                found = rec
    if found is None:
        raise ToolError(f"journal entry not found: {entry_id}")
    if "root_path" not in found:
        raise ToolError(f"journal entry lacks root_path and cannot be undone: {entry_id}")
    return found


def undo(journal_dir: str | Path, entry_id: str) -> dict:
    """Undo one auto-write journal entry.

    If the write overwrote a file, restore the saved `.prior` snapshot. If it
    created a new file, remove that file.
    """
    jd = Path(journal_dir)
    rec = _journal_entry(jd, entry_id)
    root_path = Path(rec["root_path"]).resolve()
    target = (root_path / rec["path"]).resolve()
    if not (target == root_path or root_path in target.parents):
        raise ToolError(f"journal target escapes root: {entry_id}")

    prior_path = jd / f"{entry_id}.prior"
    if rec.get("had_prior"):
        if not prior_path.is_file():
            raise ToolError(f"prior snapshot missing for journal entry: {entry_id}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if rec.get("prior_storage") == "sealed-bytes":
            from aether.integrity import sealbox
            sealbox.atomic_write_bytes(target, prior_path.read_bytes())
        else:
            prior = prior_path.read_text(encoding="utf-8", errors="replace")
            with target.open("w", encoding="utf-8", newline="\n") as f:
                f.write(prior)
        action = "restored"
    else:
        if target.exists():
            if target.is_dir():
                raise ToolError(f"undo target is a directory, refusing delete: {target}")
            target.unlink()
        action = "deleted"
    return {"undone": True, "entry": entry_id, "action": action, "path": str(target)}


def parse_root_spec(spec: str) -> tuple[str, str, str]:
    """A root given on a command line, NAME=PATH[:auto|:deny] -> (name, path, mode).

    Moved here from aether.operations.cli on 2026-10-07 (MOD-15): roots are the Toolbelt's,
    and the Runtime must not import Operations, which sits above it.
    """
    name, sep, rest = spec.partition("=")
    if not sep or not name or not rest:
        raise ToolError(f"bad root spec '{spec}' (expected NAME=PATH[:mode])")
    path, mode = rest, "auto"
    for m in ("auto", "deny"):
        if rest.endswith(f":{m}"):
            path, mode = rest[: -(len(m) + 1)], m
            break
    return name, path, mode
