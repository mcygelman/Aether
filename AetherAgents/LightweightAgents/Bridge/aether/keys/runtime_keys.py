"""Own restart-persistent Windows-protected key copies and live grant checks.

Never expose key material to account APIs, reuse management passwords, or allow
an unassigned operator to resolve a managed key. Operator-launched workers retain
the initiating account; unscoped background requests cannot borrow its grants.
"""
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
import base64
import hashlib
import json

from aether.integrity.sealbox import atomic_write_bytes

principal = ContextVar('credential_principal', default=None)
_policies = {}


@contextmanager
def as_operator(aid):
    token=principal.set(aid)
    try:yield
    finally:principal.reset(token)


def inherited(target, *args):
    context=copy_context()
    return lambda: context.run(target,*args)


class RuntimeKeys:
    def __init__(self, vault, access):
        self.vault,self.access=vault,access
        self.path=vault.root/'runtime-keys.json'
        _policies[str(vault.root.resolve())]=self

    def publish(self):
        from .vault import _dpapi
        with self.vault._lock:
            self.vault._check_current()
            raw=json.dumps(dict(revision=self.vault._revision.hex(),keys=self.vault._entries)).encode()
            cipher=_dpapi(raw,b'Aether runtime keys v1')
            assert _dpapi(cipher,b'Aether runtime keys v1',decrypt=True)==raw
            atomic_write_bytes(self.path,json.dumps(dict(version=1,ciphertext=base64.b64encode(cipher).decode())).encode())

    def contents(self):
        from .vault import _dpapi, VaultError
        try:
            if self.path.is_symlink():raise ValueError()
            raw=self.path.read_bytes()
            if len(raw)>65536:raise ValueError()
            value=json.loads(raw)
            doc=json.loads(_dpapi(base64.b64decode(value['ciphertext'],validate=True),b'Aether runtime keys v1',decrypt=True))
            if doc['revision']!=hashlib.sha256(self.vault.path.read_bytes()).hexdigest():
                raise VaultError('Saved keys changed. A key manager must unlock management to refresh assigned usage.')
            return doc['keys']
        except VaultError:raise
        except Exception:
            raise VaultError('Assigned keys are unavailable. A key manager must unlock API management on this Windows account.') from None

    def allowed(self, aid, name):
        # Query on every request; no cached user permissions or grants.
        doc=self.access.read()
        if doc.get('suspended',False):return False
        user=next((u for u in doc['accounts'] if u['id']==aid and u['active']),None)
        return bool(user and name in user.get('keys',[]))

    def resolve(self, name):
        from .vault import VaultError
        aid=principal.get()
        if not self.allowed(aid,name):
            raise VaultError('This API key is not assigned to your active account. Ask an API assignment manager for access.')
        entries=self.contents()
        if name not in entries:raise VaultError('The assigned API key was removed. Ask an API assignment manager for access.')
        return entries[name]['api_key']

    def visible(self, aid):
        try:entries=self.contents()
        except ValueError:return []
        return [dict(id=k,label=v['label'],provider=v['provider']) for k,v in entries.items() if self.allowed(aid,k)]


def policy(vault):
    return _policies.get(str(vault.root.resolve()))
