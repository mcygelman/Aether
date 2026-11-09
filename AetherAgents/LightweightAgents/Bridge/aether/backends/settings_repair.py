"""Repair of a malformed saved settings registry from the Models & Context page (MVP-08 T01, case M2).

Today a registry that no longer decodes (truncated, hand-edited, half-written) stops the settings page at
"Loading profiles" with the decode error and no product path forward; the runbook calls it installation repair.
This module is the product path: identify the problem (the exact error, the corrupt bytes' digest and size),
retain the corrupt bytes (moved beside the registry, never deleted), and restore an attributed known-good copy.

Known-good copies: after every confirmed save the HTTP layer retains the saved bytes under
<registry>.known-good/<revision>.json (retain_known_good); a repair restores one of them, chosen by revision, or,
when none was ever retained, the bundled default registry when the installation has one. Both are validated with the
store's own decoder before they replace anything. The repair is audited in the registry's audit file with the
operator, the corrupt digest, the retained path and the restored revision.

Inputs:  inspect(store) -> the state of the registry for the page; repair(store, operator_id=, corrupt_sha256=,
         restore=('known-good', revision) | ('bundled', None)) -> the restored registry and the retained path.
Outputs: dicts for the page; files beside the registry.
Never:   deletes bytes, repairs by editing JSON, restores an unvalidated copy, replaces a registry that reads fine,
         replaces bytes other than the ones the operator saw (corrupt_sha256), or saves without an operator.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import uuid

from aether.config.model_settings_store import writer_lock, revision, utc_now
from aether.config.context_configuration import ContextConfigurationError

KNOWN_GOOD_DIR = '.known-good'
MAX_REGISTRY_BYTES = 4 * 1024 * 1024


class RepairRefused(ContextConfigurationError):
    pass


def _known_good_dir(store) -> Path:
    return store.path.with_name(store.path.name + KNOWN_GOOD_DIR)


def _bundled(store):
    from aether.models.runtime_profiles import DEFAULT_REGISTRY_PATH
    path = Path(DEFAULT_REGISTRY_PATH).resolve()
    return path if path.is_file() and path != store.path else None


def _decodes(store, raw):
    try:
        store.document_decoder(raw)
        return None
    except Exception as exc:   # the decoder raises its own contract errors; the page shows the text
        return f'{type(exc).__name__}: {exc}'


def retain_known_good(store):
    """Keep the current (readable) registry bytes under their revision; idempotent; best effort for the caller."""
    raw = store.read_bytes()
    if _decodes(store, raw) is not None:
        raise RepairRefused('Only a readable registry is retained as known good')
    folder = _known_good_dir(store)
    folder.mkdir(exist_ok=True)
    target = folder / (revision(raw) + '.json')
    if target.exists():
        if target.read_bytes() != raw:
            raise RepairRefused('A retained known-good copy differs from the bytes of the same revision')
        return str(target)
    temp = target.with_name(target.name + '.tmp')
    with temp.open('wb') as handle:
        handle.write(raw); handle.flush(); os.fsync(handle.fileno())
    os.replace(temp, target)
    return str(target)


def known_good(store):
    """The retained known-good copies that still decode, newest first by modification time."""
    folder = _known_good_dir(store)
    rows = []
    if folder.is_dir():
        for path in folder.glob('*.json'):
            raw = path.read_bytes()
            if path.stem != revision(raw) or _decodes(store, raw) is not None:
                continue
            rows.append(dict(revision=path.stem, path=str(path), size=len(raw),
                             retained_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat().replace('+00:00', 'Z')))
    rows.sort(key=lambda r: r['retained_at'], reverse=True)
    return rows


def retained_corrupt(store):
    pattern = store.path.name + '.corrupt-*'
    return sorted(str(p) for p in store.path.parent.glob(pattern))


def inspect(store):
    """What the page needs when the registry cannot be read (and a short readable=True answer when it can)."""
    try:
        raw = store.read_bytes()
    except FileNotFoundError:
        return dict(readable=False, missing=True, path=str(store.path), error='The settings registry file is missing',
                    known_good=known_good(store), bundled_default=str(_bundled(store)) if _bundled(store) else None,
                    retained_corrupt=retained_corrupt(store))
    error = _decodes(store, raw)
    if error is None:
        return dict(readable=True, path=str(store.path), revision=revision(raw))
    return dict(readable=False, missing=False, path=str(store.path), error=error, bytes_sha256=revision(raw), size=len(raw),
                known_good=known_good(store), bundled_default=str(_bundled(store)) if _bundled(store) else None,
                retained_corrupt=retained_corrupt(store))


def repair(store, *, operator_id, corrupt_sha256, restore):
    """Retain the corrupt bytes, restore a validated copy, audit the repair. restore: ('known-good', revision) or
    ('bundled', None). The registry must still be the unreadable bytes the operator saw (corrupt_sha256)."""
    kind, chosen = (restore if type(restore) in (list, tuple) and len(restore) == 2 else (None, None))
    if kind not in ('known-good', 'bundled'):
        raise RepairRefused('Choose a retained known-good revision or the bundled default registry')
    if not isinstance(corrupt_sha256, str) or len(corrupt_sha256) != 64:
        raise RepairRefused('The corrupt registry digest you inspected is required')
    with writer_lock(store.operator_registry_path.with_name(store.operator_registry_path.name + '.lock')), \
            writer_lock(store.path.with_name(store.path.name + '.lock')):
        actor = store.resolve_operator(operator_id)
        exists = store.path.exists()
        raw = store.read_bytes() if exists else b''
        if exists and len(raw) > MAX_REGISTRY_BYTES:
            raise RepairRefused('The registry file is too large to inspect')
        if exists and _decodes(store, raw) is None:
            raise RepairRefused('The registry reads fine now; reload instead of repairing')
        if exists and revision(raw) != corrupt_sha256:
            raise RepairRefused('The registry changed since you inspected it; inspect again')
        if not exists and corrupt_sha256 != revision(b''):
            raise RepairRefused('The registry is missing; inspect again')
        if kind == 'known-good':
            source = _known_good_dir(store) / (str(chosen) + '.json')
            if not source.is_file():
                raise RepairRefused('No retained known-good copy has that revision')
        else:
            source = _bundled(store)
            if source is None:
                raise RepairRefused('This installation has no bundled default registry')
        restored = source.read_bytes()
        problem = _decodes(store, restored)
        if problem is not None or (kind == 'known-good' and revision(restored) != str(chosen)):
            raise RepairRefused('The chosen copy does not decode as a settings registry: ' + str(problem))
        stamp = utc_now().replace(':', '').replace('-', '')
        retained = store.path.with_name(f'{store.path.name}.corrupt-{stamp}-{corrupt_sha256[:8]}.json') if exists else None
        event = dict(event_id=uuid.uuid4().hex, operator=actor, old_revision=corrupt_sha256, new_revision=revision(restored),
                     action='repair', corrupt_retained=str(retained) if retained else None, restored_from=str(source),
                     restore_kind=kind)
        store.append_audit(dict(event, time=utc_now(), state='intent'))
        if retained is not None:
            if retained.exists():
                raise RepairRefused('A retained copy with this name already exists')
            os.replace(store.path, retained)                        # the corrupt bytes are kept, never deleted
        temp = store.path.with_name(store.path.name + '.restore.tmp')
        with temp.open('wb') as handle:
            handle.write(restored); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, store.path)
        saved = store.read()
        store.append_audit(dict(event, time=utc_now(), state='committed'))
        return dict(saved, repaired=True, corrupt_retained=str(retained) if retained else None,
                    restored_from=str(source), restore_kind=kind, operator=actor)
