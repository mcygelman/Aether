"""Small filesystem/OS primitives shared by installed launch and maintenance.

Locks are kernel-owned, never PID checks or lock-file age guesses. Keep their
inode stable: a dead process releases the lock without deleting another lock.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import uuid


class BusyInstallation(ValueError):
    pass


@contextmanager
def exclusive(path):
    from .distribution import direct
    path = direct(path)
    if not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'\0'); handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise BusyInstallation('Aether is running or maintenance is in progress; close it before maintenance.') from error
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def atomic_json(path, value):
    """Same-directory replace; failed writes leave the prior document intact."""
    from .distribution import direct
    path = direct(path)
    temporary = path.with_name(path.name + '.pending')
    direct(temporary)
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    with temporary.open('wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


def maintenance_paths(app):
    return {name: app.with_name('.' + app.name + suffix) for name, suffix in (
        ('lock', '-maintenance.lock'), ('journal', '-transaction.json'),
        ('stage', '-staging'), ('previous', '-previous'),
        ('removing', '-removing'), ('retiring', '-retiring'), ('uninstall', '-uninstall.json'))}


def check_owned_tree(path, parent):
    """Preflight the exact sibling tree BEFORE any rename or removal."""
    from .distribution import direct
    path, parent = direct(path), direct(parent)
    if path.parent != parent or path == parent:
        raise ValueError('Maintenance removal escaped its application parent')
    if not path.exists():
        return
    for folder, directories, files in os.walk(path, followlinks=False):
        for name in directories + files:
            direct(Path(folder) / name)


def remove_owned_tree(path, parent):
    """Journal ownership permits partial payloads, never reparse traversal."""
    check_owned_tree(path, parent)
    if path.exists():
        shutil.rmtree(path)


@contextmanager
def instance(data):
    """Recover stale metadata only after the OS grants exclusive ownership."""
    control = data / '.aether_control'
    control.mkdir(parents=True, exist_ok=True)
    metadata = control / 'installed-instance.json'
    with exclusive(control / 'installed-server.lock'):
        stale = metadata.exists()
        token = uuid.uuid4().hex
        record = dict(status='STARTING', token=token, pid=os.getpid(), data=str(data),
                      stale_instance_recovered=stale)
        atomic_json(metadata, record)
        try:
            yield record, metadata
        finally:
            # Still holding the OS lock; no replacement owner can be removed.
            if metadata.exists():
                metadata.unlink()


def running(data):
    """Advisory metadata, read only after the caller observed lock contention."""
    from .distribution import direct
    try:
        value = json.loads(direct(data / '.aether_control/installed-instance.json').read_bytes())
        if value.get('data') == str(data) and value.get('status') in ('STARTING', 'READY'):
            return value
    except (OSError, ValueError):
        pass
    return None
