"""Safe settings files (Configuration, MOD-04): strict JSON, a cooperating writer lock, revisions.

Shared by about ten modules. The runtime-profile SettingsStore, decode and preview moved to
aether.models.settings_store on 2026-10-07 (they validate runtime profiles, which belong to
the Model Catalog).
"""
from contextlib import contextmanager
import hashlib
import json
import os
from datetime import datetime, timezone

from aether.config.context_configuration import ContextConfigurationError


class StaleSettings(ContextConfigurationError):
    pass


class SettingsAuditError(ContextConfigurationError):
    """A two-file save is not confirmed; clients must reload, never auto-retry."""

    def __init__(self, *, replaced, new_revision):
        self.replaced = replaced
        self.new_revision = new_revision
        super().__init__('Settings audit could not be confirmed; reload settings and inspect the audit before retrying.')


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def revision(raw):
    return hashlib.sha256(raw).hexdigest()


from aether.integrity.strict_json import parse_json  # moved down to Integrity 2026-10-08, unchanged


@contextmanager
def writer_lock(path):
    """Serialize cooperating processes; leave the lock inode stable between saves."""
    with path.open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'\0'); handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise StaleSettings('Another settings save is in progress; reload and try again') from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise StaleSettings('Another settings save is in progress; reload and try again') from exc
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
