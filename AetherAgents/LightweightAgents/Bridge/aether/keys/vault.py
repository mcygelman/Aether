"""Password-unlocked, current-Windows-user DPAPI storage for local API keys.

Only ciphertext and a random salt are persisted. PBKDF2-derived password entropy
is required by DPAPI, not stored beside the blob. Unlocked state belongs to this
process; another process or a restart must unlock independently. No custom cipher.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import threading

FORMAT = 'AETHER-WINDOWS-VAULT-1'
ITERATIONS = 600_000
KEY_NAME = 'deepseek_api_key'
_instances = {}
_instances_lock = threading.Lock()


class VaultError(ValueError):
    """Messages are fixed text, never passwords, keys or provider responses."""


def credential_root(bridge=None):
    from aether.paths import BRIDGE_ROOT
    installed = os.environ.get('AETHER_INSTALL_CREDENTIALS_ROOT')
    return Path(installed).resolve() if installed else (Path(bridge or BRIDGE_ROOT) / 'secrets_local').resolve()


def get_vault(bridge=None):
    root = credential_root(bridge)
    with _instances_lock:
        return _instances.setdefault(root, Vault(root))


def _dpapi(raw, entropy, *, decrypt=False):
    if os.name != 'nt':
        raise VaultError('Local password-protected API storage requires Windows.')

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    def blob(value):
        buffer = ctypes.create_string_buffer(bytes(value))
        return buffer, Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))

    source_buffer, source = blob(raw)
    entropy_buffer, extra = blob(entropy)
    output = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    try:
        # CRYPTPROTECT_UI_FORBIDDEN only. Never CRYPTPROTECT_LOCAL_MACHINE.
        if not fn(ctypes.byref(source), None, ctypes.byref(extra), None, None, 1, ctypes.byref(output)):
            raise VaultError('Could not unlock the API vault. Check your password and Windows account.')
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.memset(source_buffer, 0, ctypes.sizeof(source_buffer))
        ctypes.memset(entropy_buffer, 0, ctypes.sizeof(entropy_buffer))
        if output.data:
            ctypes.memset(output.data, 0, output.size)
            kernel.LocalFree(output.data)


def _password(value, *, creating=False):
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise VaultError('Enter your API password (up to 1,024 characters).')
    if creating and len(value) < 6:
        raise VaultError('Choose an API management password with at least 6 characters.')
    return value.encode('utf-8')


def _key(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 4096
            or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise VaultError('Enter the API key without spaces or line breaks.')
    return value


def _label(value):
    if (not isinstance(value, str) or not 1 <= len(value.strip()) <= 80
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise VaultError('Enter a key label of 1 to 80 characters.')
    return value.strip()


def _provider(value):
    if value not in ('deepseek', 'openai', 'anthropic', 'openai-compatible'):
        raise VaultError('Choose a supported key provider.')
    return value


class Vault:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / 'api-vault.json'
        self._lock = threading.RLock()
        self._entropy = None
        self._salt = None
        self._entries = None
        self._revision = None

    def status(self):
        with self._lock:
            if self._entries is not None:
                self._check_current()
            revision = None
            if self.path.exists():
                try:
                    revision = hashlib.sha256(self._read()[0]).hexdigest()
                except VaultError:
                    pass  # Read/unlock reports damage; status must still permit locking.
            return dict(supported=os.name == 'nt', configured=self.path.exists(),
                        revision=revision,
                        unlocked=self._entries is not None,
                        has_deepseek_key=bool(self._entries and KEY_NAME in self._entries),
                        keys=[dict(id=name, label=entry['label'], provider=entry['provider'],
                                   masked='••••' + (entry['api_key'][-4:] if len(entry['api_key']) > 8 else ''))
                              for name, entry in (self._entries or {}).items()])

    def lock(self):
        with self._lock:
            if self._entropy is not None:
                self._entropy[:] = b'\0' * len(self._entropy)
            self._entropy = self._salt = self._entries = self._revision = None
            return self.status()

    @contextmanager
    def _writer(self):
        if os.name != 'nt':
            raise VaultError('Local password-protected API storage requires Windows.')
        import msvcrt
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / 'api-vault.lock').open('a+b') as handle:
            if handle.tell() == 0:
                handle.write(b'\0')
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise VaultError('Another API vault update is in progress. Try again.') from None
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

    def _read(self):
        try:
            if self.path.is_symlink():
                raise ValueError
            with self.path.open('rb') as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError
            value = json.loads(raw)
            if (set(value) != {'format', 'iterations', 'salt', 'ciphertext'}
                    or value['format'] != FORMAT or value['iterations'] != ITERATIONS):
                raise ValueError
            salt = base64.b64decode(value['salt'], validate=True)
            encrypted = base64.b64decode(value['ciphertext'], validate=True)
            if len(salt) != 32 or not 16 <= len(encrypted) <= 32768:
                raise ValueError
            return raw, salt, encrypted
        except (OSError, ValueError, TypeError, KeyError):
            raise VaultError('The API vault cannot be read. Its existing contents have been preserved.') from None

    def _commit(self, entries, salt, entropy):
        from aether.integrity.sealbox import atomic_write_bytes
        clear = json.dumps(dict(version=2, keys=entries)).encode('utf-8')
        if len(entries) > 32 or len(clear) > 24000:
            raise VaultError('API key storage is full. Remove an unused key before adding another.')
        encrypted = _dpapi(clear, entropy)
        if _dpapi(encrypted, entropy, decrypt=True) != clear:
            raise VaultError('The encrypted API key could not be verified. Nothing was saved.')
        raw = json.dumps(dict(format=FORMAT, iterations=ITERATIONS,
                             salt=base64.b64encode(salt).decode('ascii'),
                             ciphertext=base64.b64encode(encrypted).decode('ascii'))).encode('ascii')
        atomic_write_bytes(self.path, raw)
        self.lock()
        self._entries, self._salt, self._entropy = entries, salt, bytearray(entropy)
        self._revision = hashlib.sha256(raw).digest()
        from .runtime_keys import policy
        runtime = policy(self)
        if runtime: runtime.publish()
        return self.status()

    def create(self, password, confirmation, api_key=None, label='My DeepSeek key', provider='deepseek'):
        password_bytes = _password(password, creating=True)
        if password != confirmation:
            raise VaultError('The API passwords do not match.')
        # Older clients may still create their first key with the password.
        # The current UI creates an empty collection and adds keys separately.
        entries = {} if api_key is None else {
            KEY_NAME: dict(label=_label(label), provider=_provider(provider), api_key=_key(api_key))}
        with self._lock, self._writer():
            if self.path.exists():
                raise VaultError('An API vault already exists. Unlock it to manage your keys.')
            salt = secrets.token_bytes(32)
            entropy = hashlib.pbkdf2_hmac('sha256', password_bytes, salt, ITERATIONS, dklen=32)
            return self._commit(entries, salt, entropy)

    def reset(self, password, confirmation, revision, acknowledgement):
        """Explicit destructive reset; old password is not needed or recovered.

        Compare the reviewed ciphertext revision under the writer lock, then
        atomically replace it with an empty collection under a fresh password.
        Encryption/write failure leaves the old collection intact.
        """
        password_bytes = _password(password, creating=True)
        if password != confirmation:
            raise VaultError('The API passwords do not match.')
        if acknowledgement != 'DELETE_ALL_SAVED_API_KEYS':
            raise VaultError('Confirm that all saved API keys will be deleted before resetting the password.')
        with self._lock, self._writer():
            raw, _, _ = self._read()
            if not isinstance(revision, str) or hashlib.sha256(raw).hexdigest() != revision:
                raise VaultError('The saved keys changed. Review the reset again before deleting them.')
            salt = secrets.token_bytes(32)
            entropy = hashlib.pbkdf2_hmac('sha256', password_bytes, salt, ITERATIONS, dklen=32)
            return self._commit({}, salt, entropy)

    def unlock(self, password):
        with self._lock:
            password_bytes = _password(password)
            raw, salt, encrypted = self._read()
            entropy = hashlib.pbkdf2_hmac('sha256', password_bytes, salt, ITERATIONS, dklen=32)
            try:
                entries = json.loads(_dpapi(encrypted, entropy, decrypt=True))
                if isinstance(entries, dict) and set(entries) == {KEY_NAME}:
                    # Legacy single-key stores remain byte-for-byte intact on unlock.
                    # Keep their reference so existing deployments continue working.
                    entries = {KEY_NAME: dict(label='Devkey', provider='deepseek', api_key=_key(entries[KEY_NAME]))}
                elif (isinstance(entries, dict) and set(entries) == {'version', 'keys'}
                      and entries['version'] == 2):
                    entries = entries['keys']
                else:
                    raise ValueError
                if not isinstance(entries, dict) or len(entries) > 32:
                    raise ValueError
                labels = set()
                for name, entry in entries.items():
                    if (not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', name)
                            or not isinstance(entry, dict) or set(entry) != {'label', 'provider', 'api_key'}):
                        raise ValueError
                    label = _label(entry['label']).casefold()
                    if label in labels:
                        raise ValueError
                    labels.add(label)
                    _provider(entry['provider'])
                    _key(entry['api_key'])
            except (ValueError, TypeError, UnicodeError):
                raise VaultError('Could not unlock the API vault. Check your password and Windows account.') from None
            self.lock()
            self._entries, self._salt, self._entropy = entries, salt, bytearray(entropy)
            self._revision = hashlib.sha256(raw).digest()
            return self.status()

    def _check_current(self):
        if self._entries is None:
            raise VaultError('Unlock your API keys on the API keys page to continue.')
        try:
            raw, _, _ = self._read()
        except VaultError:
            self.lock()
            raise
        if hashlib.sha256(raw).digest() != self._revision:
            self.lock()
            raise VaultError('The API vault changed in another session. Unlock it again.')

    def resolve(self, name):
        with self._lock:
            self._check_current()
            if name not in self._entries:
                raise VaultError('This saved API key is missing. Choose another key in Models & Context.')
            return self._entries[name]['api_key']

    def replace_key(self, api_key, name=KEY_NAME):
        value = _key(api_key)
        with self._lock, self._writer():
            self.resolve(name)
            entries = {k: dict(v) for k, v in self._entries.items()}
            entries[name]['api_key'] = value
            return self._commit(entries, self._salt, bytes(self._entropy))

    def _unique_label(self, label, name=None):
        value = _label(label)
        if any(k != name and entry['label'].casefold() == value.casefold()
               for k, entry in self._entries.items()):
            raise VaultError('That label is already used. Choose a different label.')
        return value

    def add(self, label, provider, api_key):
        with self._lock, self._writer():
            self._check_current()
            entry = dict(label=self._unique_label(label), provider=_provider(provider), api_key=_key(api_key))
            entries = dict(self._entries)
            entries['key_' + secrets.token_hex(16)] = entry
            return self._commit(entries, self._salt, bytes(self._entropy))

    def rename(self, name, label):
        with self._lock, self._writer():
            self.resolve(name)
            entries = {k: dict(v) for k, v in self._entries.items()}
            entries[name]['label'] = self._unique_label(label, name)
            return self._commit(entries, self._salt, bytes(self._entropy))

    def delete(self, name):
        with self._lock, self._writer():
            self.resolve(name)
            entries = dict(self._entries)
            del entries[name]
            return self._commit(entries, self._salt, bytes(self._entropy))
