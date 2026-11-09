"""Own local human accounts, credentials, sessions and additive permissions.

Never delete retained Chat/shared records or alter agent identities and memory.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import threading
import time
import uuid

from .account_store import AccountStore
from .operator_roles import effective, current_actor, ceiling, selection

PERMISSIONS = {
    'manage_missions': 'Manage Missions and Projects',
    'manage_agents': 'Manage Agents',
    'manage_teach': 'Manage Teach',
    'manage_guides': 'Manage Guides',
    'manage_dashboards': 'Manage Dashboards',
    'manage_settings': 'Manage Settings',
    'manage_accounts': 'Manage Accounts',
    'manage_roles': 'Manage Roles',
    'manage_api_keys': 'Manage API Keys',
    'manage_key_assignments': 'Assign API Keys',
    'manage_data_sources': 'Manage Data Sources',
    'use_data_sources': 'Use Data Sources and Reports',
}
ITERATIONS = 600000  # OWASP Password Storage Cheat Sheet: PBKDF2-HMAC-SHA256.
SESSION_SECONDS = 12 * 60 * 60
# Michel, 2026-11-07: an installed copy ships with a default Admin, the same on every install, "just like routers";
# "All users will end up changing the password." The one-time welcome screen asks for the new password first.
DEFAULT_ADMIN = dict(username='admin', name='Admin', password='aether-admin')
WELCOME = 'welcome-pending.json'


def can(user, permission):
    return bool(user and user.get('active') and
                (user.get('role') == 'admin' or permission in user.get('permissions', [])))


def public(user):
    return {k: deepcopy(user.get(k, [])) for k in ('id', 'username', 'name', 'role', 'permissions', 'direct_permissions', 'roles', 'active')}


def password_record(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError('Use an account password or passphrase of 12–256 characters.')
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), ITERATIONS).hex()
    return dict(salt=salt, digest=digest, iterations=ITERATIONS)


def matches(password, record):
    if not isinstance(password, str) or len(password) > 256:
        return False
    value = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(record['salt']), record['iterations']).hex()
    return hmac.compare_digest(value, record['digest'])


class OperatorAccess:
    def __init__(self, root):
        self.root = Path(root)
        self.store = AccountStore(self.root)
        self.path = self.store.path
        self.lock = threading.RLock()
        self.sessions = {}
        self.attempts = []

    def read(self):
        with self.lock:
            return self.store.read()

    @contextmanager
    def edit(self, revision=None):
        with self.lock, self.store.edit(revision) as doc:
            yield doc

    def seed_default_admin(self):
        """First launch of an installed copy only: create the default Admin when there is no account at all.

        The welcome marker (beside the accounts, not in them) names that account until its welcome is done or skipped.
        """
        with self.edit() as doc:
            if doc['accounts']:
                return None
            user = dict(id=uuid.uuid4().hex, username=DEFAULT_ADMIN['username'], name=DEFAULT_ADMIN['name'],
                        role='admin', permissions=[], active=True,
                        password=password_record(DEFAULT_ADMIN['password']), legacy_chats=True)
            doc['accounts'].append(user)
            (self.root / WELCOME).write_text(json.dumps(dict(account=user['id'])), encoding='utf-8')
        return user['id']

    def welcome_due(self, actor):
        try:
            marker = json.loads((self.root / WELCOME).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return False
        return bool(actor) and marker.get('account') == actor.get('id')

    def welcome_done(self):
        try:
            (self.root / WELCOME).unlink()
        except FileNotFoundError:
            pass

    def welcome_password(self, actor, body):
        """The welcome's new password: no current password asked, only while this account's welcome is due."""
        self.throttle()
        if not self.welcome_due(actor):
            raise ValueError('The welcome has already been completed. Use Change password.')
        if body.get('password') != body.get('confirmation'):
            raise ValueError('The new passwords do not match.')
        new = password_record(body.get('password'))
        with self.edit() as doc:
            user = next(u for u in doc['accounts'] if u['id'] == actor['id'])
            user['password'] = new
        with self.lock:
            self.sessions = {k: v for k, v in self.sessions.items() if v['id'] != actor['id']}
            return self.issue(user)

    def actor(self, aid):
        doc = self.read()
        return effective(next((u for u in doc['accounts'] if u['id'] == aid and u['active']), None), doc)

    def throttle(self):
        with self.lock:
            now = time.monotonic()
            self.attempts = [t for t in self.attempts if now - t < 60]
            if len(self.attempts) >= 20:
                raise ValueError('Too many account attempts. Wait one minute and try again.')
            self.attempts.append(now)

    def register(self, body):
        self.throttle()
        username = body.get('username', '')
        name = body.get('name', '')
        if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{2,63}', username):
            raise ValueError('Use 3–64 letters, numbers, dots, hyphens or underscores for the username.')
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError('Enter a display name up to 120 characters.')
        if body.get('password') != body.get('confirmation'):
            raise ValueError('The account passwords do not match.')
        password = password_record(body.get('password'))
        with self.edit() as doc:
            if any(u['username'].casefold() == username.casefold() for u in doc['accounts']):
                raise ValueError('That username is already taken.')
            first = not doc['accounts']
            user = dict(id=uuid.uuid4().hex, username=username, name=name.strip(),
                        role='admin' if first else 'user', permissions=[], active=True,
                        password=password, legacy_chats=first)
            doc['accounts'].append(user)
        return self.issue(user)

    def issue(self, user):
        with self.lock:
            now = time.time()
            self.sessions = {k: v for k, v in self.sessions.items() if v['expires'] > now}
            token = secrets.token_urlsafe(32)
            self.sessions[hashlib.sha256(token.encode()).hexdigest()] = dict(id=user['id'], expires=now + SESSION_SECONDS)
            return public(self.actor(user['id'])), token

    def login(self, body):
        self.throttle()
        username = body.get('username')
        if not isinstance(username, str):
            raise ValueError('Enter your username and account password.')
        with self.lock:
            user = next((u for u in self.read()['accounts'] if u['username'].casefold() == username.casefold()), None)
            # Do comparable work for an unknown username without retaining a password.
            record = user['password'] if user else dict(salt='00' * 16, digest='00' * 32, iterations=ITERATIONS)
            valid = matches(body.get('password'), record)
            if not user or not valid or not user['active']:
                raise ValueError('The username or password is incorrect, or the account is disabled.')
            return self.issue(user)

    def user(self, token):
        if not token or len(token) > 128:
            return None
        key = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            session = self.sessions.get(key)
            if not session or session['expires'] <= time.time():
                self.sessions.pop(key, None)
                return None
            user = self.actor(session['id'])
            return public(user) if user else None

    def logout(self, token):
        with self.lock:
            self.sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)

    def listing(self):
        doc = self.read()
        return dict(revision=doc['revision'], accounts=[public(effective(u,doc)) for u in doc['accounts']], permissions=PERMISSIONS, roles=doc['roles'])

    def update(self, actor, body):
        with self.edit(body.get('revision')) as doc:
            current = current_actor(doc, actor, 'manage_accounts')
            user = next((u for u in doc['accounts'] if u['id'] == body.get('id')), None)
            if not user:
                raise ValueError('Account not found.')
            role, permissions, active = body.get('role'), body.get('permissions'), body.get('active')
            if role not in ('admin', 'user') or type(active) is not bool:
                raise ValueError('Choose Admin or User and an account status.')
            if not isinstance(permissions, list) or any(p not in PERMISSIONS for p in permissions):
                raise ValueError('Choose permissions from the available management roles.')
            ceiling(current, user, doc)
            if user['role'] == 'admin' and user['active'] and (role != 'admin' or not active):
                if sum(u['role'] == 'admin' and u['active'] for u in doc['accounts']) <= 1:
                    raise ValueError('Keep at least one active Admin.')
            permissions, roles = selection(body, doc, current)
            user.update(role=role, permissions=permissions if role == 'user' else [], roles=roles if role == 'user' else [], active=active)
        if not active:
            with self.lock:
                self.sessions = {k: v for k, v in self.sessions.items() if v['id'] != user['id']}
        return self.listing()

    def create_account(self, actor, body):
        """Admin-created accounts do not replace the Admin's signed-in session."""
        with self.edit(body.get('revision')) as doc:
            current = current_actor(doc, actor, 'manage_accounts')
            username, name = body.get('username', ''), body.get('name', '')
            if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{2,63}', username):
                raise ValueError('Use 3–64 letters, numbers, dots, hyphens or underscores for the username.')
            if not isinstance(name, str) or not name.strip() or len(name) > 120:
                raise ValueError('Enter a display name up to 120 characters.')
            if any(u['username'].casefold() == username.casefold() for u in doc['accounts']):
                raise ValueError('That username is already taken.')
            role, permissions = body.get('role'), body.get('permissions')
            if role not in ('admin', 'user'):
                raise ValueError('Choose Admin or User.')
            if not isinstance(permissions, list) or any(p not in PERMISSIONS for p in permissions):
                raise ValueError('Choose permissions from the available management roles.')
            if body.get('password') != body.get('confirmation'):
                raise ValueError('The account passwords do not match.')
            permissions, roles = selection(body, doc, current)
            doc['accounts'].append(dict(
                id=uuid.uuid4().hex, username=username, name=name.strip(), role=role,
                permissions=sorted(set(permissions)) if role == 'user' else [], active=True,
                password=password_record(body.get('password')), legacy_chats=False, roles=roles if role=='user' else []))
        return self.listing()

    def set_password(self, actor, body):
        """Reset another account and revoke its sessions before another login can finish."""
        with self.lock:
            with self.edit(body.get('revision')) as doc:
                current = current_actor(doc, actor, 'manage_accounts')
                user = next((u for u in doc['accounts'] if u['id'] == body.get('id')), None)
                if not user:
                    raise ValueError('Account not found.')
                ceiling(current, user, doc)
                if user['id'] == actor['id']:
                    raise ValueError('Use Change password for your own account.')
                if body.get('password') != body.get('confirmation'):
                    raise ValueError('The new passwords do not match.')
                user['password'] = password_record(body.get('password'))
            self.sessions = {k: v for k, v in self.sessions.items() if v['id'] != user['id']}
            return self.listing()

    def delete_account(self, actor, body):
        """Remove sign-in access while keeping historical records under their original IDs."""
        with self.lock:
            with self.edit(body.get('revision')) as doc:
                current = current_actor(doc, actor, 'manage_accounts')
                user = next((u for u in doc['accounts'] if u['id'] == body.get('id')), None)
                if not user:
                    raise ValueError('Account not found.')
                if user['role'] == 'admin' and user['active'] and sum(
                        u['role'] == 'admin' and u['active'] for u in doc['accounts']) <= 1:
                    raise ValueError('Keep at least one active Admin.')
                ceiling(current, user, doc)
                if user['id'] == actor['id']:
                    raise ValueError('Another Admin must delete your account.')
                if body.get('confirmation') != user['username']:
                    raise ValueError('Type the exact username to confirm account deletion.')
                doc['accounts'].remove(user)
            self.sessions = {k: v for k, v in self.sessions.items() if v['id'] != user['id']}
            return self.listing()

    def change_password(self, actor, body):
        self.throttle()
        if body.get('password') != body.get('confirmation'):
            raise ValueError('The new passwords do not match.')
        new = password_record(body.get('password'))
        with self.edit() as doc:
            user = next(u for u in doc['accounts'] if u['id'] == actor['id'])
            if not matches(body.get('current_password'), user['password']):
                raise ValueError('The current account password is incorrect.')
            user['password'] = new
        with self.lock:
            self.sessions = {k: v for k, v in self.sessions.items() if v['id'] != actor['id']}
            return self.issue(user)

    def chat_root(self, actor, root):
        user = next(u for u in self.read()['accounts'] if u['id'] == actor['id'])
        return Path(root) if user.get('legacy_chats') else Path(root) / 'operators' / actor['id']
