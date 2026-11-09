"""Real Windows DPAPI, local HTTP and cached-client checks; synthetic credentials only."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import urllib.error
import urllib.request

import pytest

from aether.keys import vault
from aether.keys.provider_credentials import resolve_reference, CredentialError
from aether.atelier.turn import Deployment
from aether.models.deployment_profiles import CredentialRef

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI integration')
PASSWORD = 'synthetic vault password 20261024'
KEY = 'fixture-api-key-never-a-real-credential-20261024'
REF = {'kind': 'vault', 'name': vault.KEY_NAME}


@pytest.fixture
def local_vault(tmp_path, monkeypatch):
    monkeypatch.setenv('AETHER_INSTALL_CREDENTIALS_ROOT', str(tmp_path / 'credentials'))
    instance = vault.get_vault()
    yield instance
    instance.lock()
    with vault._instances_lock:
        vault._instances.pop(instance.root, None)


def test_dpapi_roundtrip_password_restart_and_no_plaintext(local_vault):
    v = local_vault
    assert v.create(PASSWORD, PASSWORD, KEY)['unlocked']
    raw = v.path.read_bytes()
    assert KEY.encode() not in raw and PASSWORD.encode() not in raw
    other = vault.Vault(v.root)
    assert other.status()['configured'] and not other.status()['unlocked']
    with pytest.raises(vault.VaultError, match='Unlock'):
        other.resolve(vault.KEY_NAME)
    with pytest.raises(vault.VaultError, match='Could not unlock'):
        other.unlock('a different password')
    assert v.path.read_bytes() == raw
    assert other.unlock(PASSWORD)['unlocked']
    assert other.resolve(vault.KEY_NAME) == KEY
    other.lock()
    assert not other.status()['unlocked']
    assert CredentialRef.from_mapping(REF).to_mapping() == REF


def test_real_new_process_starts_locked(local_vault):
    local_vault.create(PASSWORD, PASSWORD, KEY)
    code = ('from aether.keys.vault import get_vault; '
            'v=get_vault(); assert v.status()["configured"]; assert not v.status()["unlocked"]; '
            'print("NEW_PROCESS_LOCKED")')
    run = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True, check=True)
    assert run.stdout.strip() == 'NEW_PROCESS_LOCKED'


def test_creation_failure_never_replaces_existing_key(local_vault):
    v = local_vault
    with pytest.raises(vault.VaultError, match='6 characters'):
        v.create('short', 'short', KEY)
    with pytest.raises(vault.VaultError, match='do not match'):
        v.create(PASSWORD, 'different', KEY)
    assert not v.path.exists()
    v.create(PASSWORD, PASSWORD, KEY)
    before = v.path.read_bytes()
    with pytest.raises(vault.VaultError, match='already exists'):
        v.create(PASSWORD, PASSWORD, 'replacement')
    with pytest.raises(vault.VaultError):
        v.replace_key('contains spaces')
    assert v.path.read_bytes() == before


def test_corrupt_ciphertext_fails_without_plaintext_or_overwrite(local_vault):
    v = local_vault
    v.create(PASSWORD, PASSWORD, KEY)
    value = json.loads(v.path.read_bytes())
    import base64
    encrypted = bytearray(base64.b64decode(value['ciphertext']))
    encrypted[-1] ^= 1
    value['ciphertext'] = base64.b64encode(encrypted).decode('ascii')
    v.path.write_text(json.dumps(value), encoding='utf-8')
    damaged = v.path.read_bytes()
    v.lock()
    with pytest.raises(vault.VaultError) as error:
        v.unlock(PASSWORD)
    assert KEY not in str(error.value) and PASSWORD not in str(error.value)
    assert v.path.read_bytes() == damaged


def test_stale_unlocked_session_cannot_resolve_or_overwrite(local_vault):
    v = local_vault
    v.create(PASSWORD, PASSWORD, KEY)
    other = vault.Vault(v.root)
    other.unlock(PASSWORD)
    other.replace_key('new-synthetic-key')
    with pytest.raises(vault.VaultError, match='changed'):
        v.replace_key('stale-key')
    assert not v.status()['unlocked']
    v.unlock(PASSWORD)
    assert v.resolve(vault.KEY_NAME) == 'new-synthetic-key'
    other.lock()


def test_failed_disk_replace_preserves_key_and_unlock(local_vault, monkeypatch):
    from aether.integrity import sealbox
    v = local_vault
    v.create(PASSWORD, PASSWORD, KEY)
    before = v.path.read_bytes()
    def fail(*args):
        raise OSError('synthetic write failure')
    monkeypatch.setattr(sealbox.os, 'replace', fail)
    with pytest.raises(OSError):
        v.replace_key('another-synthetic-key')
    assert v.path.read_bytes() == before and v.resolve(vault.KEY_NAME) == KEY
    assert not list(v.root.glob('*.tmp'))


@contextmanager
def running(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def request(base, route, body=None, origin=None, raw=None):
    data = raw if raw is not None else json.dumps(body).encode() if body is not None else None
    headers = {'Origin': origin or base, 'Content-Type': 'application/json'}
    req = urllib.request.Request(base + route, data=data, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(req, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.read(), dict(response.headers)


def test_http_creation_unlock_secrets_and_origin_guard(local_vault, tmp_path):
    import model_context_settings
    from aether.models.settings_store import SettingsStore
    store = SettingsStore(tmp_path / 'profiles.json')
    with running(model_context_settings.bind(store)) as base:
        body = dict(password=PASSWORD, confirmation=PASSWORD, api_key=KEY)
        code, raw, _ = request(base, '/api/credentials/create', body, origin='https://other.invalid')
        assert code == 403 and not local_vault.path.exists()
        code, raw, headers = request(base, '/api/credentials/create', body)
        assert code == 200 and json.loads(raw)['unlocked']
        assert KEY.encode() not in raw and PASSWORD.encode() not in raw
        assert headers['Cache-Control'] == 'no-store'
        code, _, _ = request(base, '/api/credentials/lock', {})
        assert code == 200
        code, raw, _ = request(base, '/api/credentials/unlock', {'password': 'wrong'})
        assert code == 400 and not local_vault.status()['unlocked']
        code, raw, _ = request(base, '/api/credentials/unlock', {'password': PASSWORD})
        assert code == 200 and json.loads(raw)['unlocked']
        code, raw, _ = request(base, '/api/credentials/create', raw=b'{"api_key":"'+KEY.encode()+b'"')
        assert code == 400 and KEY.encode() not in raw
        code, raw, headers = request(base, '/credentials.js')
        assert code == 200 and 'javascript' in headers['Content-Type']
        assert not store.path.exists()  # Keys never enter profile/audit writes.
    for p in tmp_path.rglob('*'):
        if p.is_file():
            assert KEY.encode() not in p.read_bytes() and PASSWORD.encode() not in p.read_bytes()


def test_cached_real_client_observes_lock_replacement_and_unlock(local_vault):
    expected = [KEY]
    seen = []
    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            assert self.headers['Authorization'] == 'Bearer ' + expected[0]
            seen.append(self.path)
            raw = json.dumps({'choices': [{'message': {'role': 'assistant', 'content': 'Synthetic reply'}}]}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def log_message(self, *args):
            pass
    with running(ThreadingHTTPServer(('127.0.0.1', 0), Provider)) as base:
        profile = dict(endpoint=base+'/v1', model='fixture', credential_ref=REF, maxOutputTokens=100)
        client = Deployment.from_profile(profile).client()
        assert client.api_key == ''
        with pytest.raises(CredentialError, match='Unlock'):
            client.chat([{'role': 'user', 'content': 'fixture'}])
        assert not seen
        local_vault.create(PASSWORD, PASSWORD, KEY)
        assert client.chat([{'role': 'user', 'content': 'fixture'}])['content'] == 'Synthetic reply'
        local_vault.lock()
        with pytest.raises(CredentialError, match='Unlock'):
            client.chat([{'role': 'user', 'content': 'fixture'}])
        assert len(seen) == 1
        local_vault.unlock(PASSWORD)
        expected[0] = 'replacement-synthetic-key'
        local_vault.replace_key(expected[0])
        client.chat([{'role': 'user', 'content': 'fixture'}])
        assert len(seen) == 2 and client.api_key == ''
        assert KEY not in repr(client) and PASSWORD not in repr(client)


def test_unencrypted_remote_endpoint_refused_before_key_lookup(local_vault):
    from aether.backends.model import ModelError
    client = Deployment.from_profile(dict(endpoint='http://example.invalid/v1', model='fixture', credential_ref=REF)).client()
    with pytest.raises(ModelError, match='HTTPS'):
        client._headers()


def test_installed_root_and_profile_only_reference(local_vault, tmp_path):
    local_vault.create(PASSWORD, PASSWORD, KEY)
    assert resolve_reference(REF, bridge=tmp_path / 'immutable-code') == KEY
    assert local_vault.path.parent == tmp_path / 'credentials'
    assert not (tmp_path / 'immutable-code').exists()


@pytest.mark.parametrize('mode', ['error', 'echo', 'redirect'])
def test_provider_cannot_echo_key_to_records_or_redirect_it(local_vault, mode):
    from aether.backends.model import ModelError
    seen = []
    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            seen.append(self.path)
            if mode == 'redirect':
                self.send_response(307)
                self.send_header('Location', '/stolen')
                raw = b''
            elif mode == 'error':
                self.send_response(401)
                raw = KEY.encode()
            else:
                self.send_response(200)
                raw = json.dumps({'choices': [{'message': {'role': 'assistant', 'content': KEY}}]}).encode()
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def log_message(self, *args):
            pass
    local_vault.create(PASSWORD, PASSWORD, KEY)
    with running(ThreadingHTTPServer(('127.0.0.1', 0), Provider)) as base:
        client = Deployment.from_profile(dict(endpoint=base+'/v1', model='fixture', credential_ref=REF)).client()
        if mode == 'echo':
            result = client.chat([{'role': 'user', 'content': 'fixture'}])
            assert KEY not in json.dumps(result) and '[REDACTED]' in result['content']
        else:
            with pytest.raises(ModelError) as error:
                client.chat([{'role': 'user', 'content': 'fixture'}])
            assert KEY not in str(error.value)
        assert seen == ['/v1/chat/completions']

def test_legacy_key_is_devkey_without_rewriting_and_survives_management(local_vault):
    import base64
    import hashlib
    v = local_vault
    salt = os.urandom(32)
    entropy = hashlib.pbkdf2_hmac('sha256', PASSWORD.encode(), salt, vault.ITERATIONS, dklen=32)
    raw = json.dumps(dict(format=vault.FORMAT, iterations=vault.ITERATIONS,
        salt=base64.b64encode(salt).decode(),
        ciphertext=base64.b64encode(vault._dpapi(json.dumps({vault.KEY_NAME:KEY}).encode(), entropy)).decode())).encode()
    v.root.mkdir(parents=True)
    v.path.write_bytes(raw)
    result = v.unlock(PASSWORD)
    assert result['keys'] == [dict(id=vault.KEY_NAME, label='Devkey', provider='deepseek', masked='••••1024')]
    assert v.path.read_bytes() == raw
    assert v.resolve(vault.KEY_NAME) == KEY
    result = v.add('Second key', 'openai-compatible', 'synthetic-second-api-key')
    second = next(k['id'] for k in result['keys'] if k['label'] == 'Second key')
    v.rename(second, 'Renamed key')
    v.replace_key('synthetic-second-replacement', second)
    assert v.resolve(vault.KEY_NAME) == KEY
    assert v.resolve(second) == 'synthetic-second-replacement'
    for forbidden in (KEY, PASSWORD, 'Devkey', 'Renamed key', 'synthetic-second-replacement'):
        assert forbidden.encode() not in v.path.read_bytes()
    v.lock()
    assert v.status()['keys'] == []
    v.unlock(PASSWORD)
    assert {k['label'] for k in v.status()['keys']} == {'Devkey', 'Renamed key'}
    v.delete(second)
    assert v.resolve(vault.KEY_NAME) == KEY
    v.delete(vault.KEY_NAME)
    v.lock()
    assert v.unlock(PASSWORD)['keys'] == []
    result = v.add('After empty', 'deepseek', KEY)
    assert v.resolve(result['keys'][0]['id']) == KEY
    with pytest.raises(vault.VaultError, match='missing'):
        v.resolve(vault.KEY_NAME)  # Never silently rebind a deleted deployment reference.


def test_management_validation_and_failure_preserve_other_keys(local_vault, monkeypatch):
    v = local_vault
    v.create(PASSWORD, PASSWORD, KEY, 'Devkey')
    before = v.path.read_bytes()
    for label in (' ', 'Devkey', 'devKEY', 'x'*81, 'bad\nlabel'):
        with pytest.raises(vault.VaultError):
            v.add(label, 'deepseek', 'synthetic-add-key')
        assert v.path.read_bytes() == before
    with pytest.raises(vault.VaultError):
        v.add('Valid label', 'unsupported', KEY)
    with pytest.raises(vault.VaultError):
        v.delete('missing')
    from aether.integrity import sealbox
    def fail(*args):
        raise OSError('fixture write failure')
    monkeypatch.setattr(sealbox.os, 'replace', fail)
    for operation in (lambda:v.rename(vault.KEY_NAME,'New label'), lambda:v.delete(vault.KEY_NAME),
                      lambda:v.add('Second','deepseek','synthetic-add-key')):
        with pytest.raises(OSError):
            operation()
        assert v.path.read_bytes() == before and v.resolve(vault.KEY_NAME) == KEY
    v.lock()
    for operation in (lambda:v.rename(vault.KEY_NAME,'New label'), lambda:v.delete(vault.KEY_NAME),
                      lambda:v.add('Second','deepseek','synthetic-add-key')):
        with pytest.raises(vault.VaultError, match='Unlock'):
            operation()


def test_http_management_usage_explicit_reveal_and_preserved_profiles(local_vault, tmp_path):
    import model_context_settings
    from aether.models.settings_store import SettingsStore
    store = SettingsStore(tmp_path/'profiles.json')
    # A deliberately narrow store reader supplies a valid usage snapshot without modifying profiles.
    store.read = lambda: {'document':{'profiles':{'Existing deployment':{'credential_ref':REF}}}}
    local_vault.create(PASSWORD, PASSWORD, KEY, 'Devkey')
    with running(model_context_settings.bind(store)) as base:
        code, raw, headers = request(base, '/api/credentials')
        result = json.loads(raw)
        assert code == 200 and result['usage_known']
        assert result['keys'][0]['deployments'] == ['Existing deployment']
        assert KEY.encode() not in raw
        code, raw, headers = request(base, '/api/credentials/reveal', {'id':vault.KEY_NAME})
        assert code == 200 and json.loads(raw)['api_key'] == KEY and headers['Cache-Control'] == 'no-store'
        assert request(base, '/api/credentials/reveal')[0] == 404
        for action, payload in [('reveal', {'id':vault.KEY_NAME}), ('delete', {'id':vault.KEY_NAME}),
                                ('rename', {'id':vault.KEY_NAME,'label':'Other'})]:
            assert request(base, '/api/credentials/'+action, payload, origin='https://hostile.invalid')[0] == 403
        code, raw, _ = request(base, '/api/credentials/add', {'label':'Other', 'provider':'deepseek', 'api_key':'synthetic-other-key'})
        assert code == 200 and len(json.loads(raw)['keys']) == 2 and b'synthetic-other-key' not in raw
        second = json.loads(raw)['keys'][1]['id']
        assert request(base, '/api/credentials/rename', {'id':second,'label':'Renamed'})[0] == 200
        assert request(base, '/api/credentials/replace', {'id':second,'api_key':'synthetic-rotated-key'})[0] == 200
        assert request(base, '/api/credentials/delete', {'id':second})[0] == 200
        assert local_vault.resolve(vault.KEY_NAME) == KEY
        request(base, '/api/credentials/lock', {})
        assert request(base, '/api/credentials/reveal', {'id':vault.KEY_NAME})[0] == 400
        assert json.loads(request(base, '/api/credentials')[1])['keys'] == []
        assert not store.path.exists()


RESET_ACK = 'DELETE_ALL_SAVED_API_KEYS'


def test_password_only_setup_accepts_six_and_enables_separate_keys(local_vault):
    v = local_vault
    for password in ('', '12345'):
        with pytest.raises(vault.VaultError):
            v.create(password, password)
    assert not v.path.exists()
    assert v.create('six-ok', 'six-ok')['keys'] == []
    assert v.status()['configured'] and v.status()['unlocked']
    v.lock()
    assert v.unlock('six-ok')['keys'] == []
    result = v.add('Devkey1', 'deepseek', KEY)
    assert v.resolve(result['keys'][0]['id']) == KEY


def test_locked_reset_deletes_all_keys_changes_password_and_invalidates_clients(local_vault):
    v = local_vault
    v.create(PASSWORD, PASSWORD, KEY, 'Devkey1')
    v.add('Second', 'deepseek', 'synthetic-second-key')
    other = vault.Vault(v.root)
    other.unlock(PASSWORD)
    before = v.path.read_bytes()
    v.lock()
    revision = v.status()['revision']
    result = v.reset('new123', 'new123', revision, RESET_ACK)
    assert result['unlocked'] and result['keys'] == []
    assert v.path.read_bytes() != before
    with pytest.raises(vault.VaultError, match='changed'):
        other.resolve(vault.KEY_NAME)
    assert not other.status()['unlocked']
    v.lock()
    with pytest.raises(vault.VaultError, match='Could not unlock'):
        v.unlock(PASSWORD)
    assert v.unlock('new123')['keys'] == []
    with pytest.raises(vault.VaultError, match='missing'):
        v.resolve(vault.KEY_NAME)
    result = v.add('Devkey1', 'deepseek', KEY)
    assert result['keys'][0]['id'] != vault.KEY_NAME
    for value in (KEY, PASSWORD, 'new123', 'Devkey1'):
        assert value.encode() not in v.path.read_bytes()


def test_reset_validation_staleness_and_disk_failure_preserve_keys(local_vault, monkeypatch):
    v = local_vault
    v.create(PASSWORD, PASSWORD, KEY)
    before = v.path.read_bytes()
    revision = v.status()['revision']
    for password, confirmation, reviewed, ack in [
            ('short','short',revision,RESET_ACK),
            ('new123','other1',revision,RESET_ACK),
            ('new123','new123',revision,''),
            ('new123','new123','stale',RESET_ACK)]:
        with pytest.raises(vault.VaultError):
            v.reset(password, confirmation, reviewed, ack)
        assert v.path.read_bytes() == before and v.resolve(vault.KEY_NAME) == KEY
    other = vault.Vault(v.root)
    other.unlock(PASSWORD)
    other.add('Concurrent key', 'deepseek', 'synthetic-concurrent-key')
    updated = v.path.read_bytes()
    with pytest.raises(vault.VaultError, match='changed'):
        v.reset('new123', 'new123', revision, RESET_ACK)
    assert v.path.read_bytes() == updated
    other.lock()
    v.unlock(PASSWORD)
    revision = v.status()['revision']
    from aether.integrity import sealbox
    def fail(*args):
        raise OSError('fixture reset write failure')
    monkeypatch.setattr(sealbox.os, 'replace', fail)
    with pytest.raises(OSError):
        v.reset('new123', 'new123', revision, RESET_ACK)
    assert v.path.read_bytes() == updated and v.resolve(vault.KEY_NAME) == KEY
    assert len(v.status()['keys']) == 2


def test_http_password_setup_and_confirmed_reset_leave_profiles_untouched(local_vault, tmp_path):
    import model_context_settings
    from aether.models.settings_store import SettingsStore
    store = SettingsStore(tmp_path/'profiles.json')
    store.read = lambda: {'document':{'profiles':{'Existing deployment':{'credential_ref':REF}}}}
    with running(model_context_settings.bind(store)) as base:
        code, raw, _ = request(base, '/api/credentials/create', {'password':'six-ok','confirmation':'six-ok'})
        assert code == 200 and json.loads(raw)['keys'] == []
        result = local_vault.add('Devkey1','deepseek',KEY)
        request(base, '/api/credentials/lock', {})
        status = json.loads(request(base, '/api/credentials')[1])
        assert status['vault_deployments'] == ['Existing deployment']
        payload = dict(password='new123',confirmation='new123',revision=status['revision'],acknowledgement=RESET_ACK)
        before = local_vault.path.read_bytes()
        assert request(base, '/api/credentials/reset')[0] == 404
        assert request(base, '/api/credentials/reset', payload, origin='https://hostile.invalid')[0] == 403
        assert request(base, '/api/credentials/reset', dict(payload,acknowledgement='no'))[0] == 400
        assert local_vault.path.read_bytes() == before
        code, raw, headers = request(base, '/api/credentials/reset', payload)
        assert code == 200 and json.loads(raw)['keys'] == [] and headers['Cache-Control'] == 'no-store'
        assert all(value.encode() not in raw for value in (KEY,'six-ok','new123'))
        assert not store.path.exists()


def test_separate_key_page_preserves_profiles_and_uses_guarded_shared_vault(local_vault, tmp_path):
    import model_context_settings
    from aether.models.settings_store import SettingsStore
    store = SettingsStore(tmp_path / 'profiles.json')
    store.path.write_bytes(b'{broken registry preserved for repair')
    before = store.path.read_bytes()
    with running(model_context_settings.bind(store)) as base:
        code, page, headers = request(base, '/api-keys/')
        assert code == 200 and headers['Cache-Control'] == 'no-store'
        assert b'id="apiVault"' in page and b'Set up DeepSeek' not in page
        assert b'Deployment profile' not in page and b'id="applyNotice"' not in page
        assert request(base, '/api-keys/api/settings')[0] == 404
        assert request(base, '/api-keys/api/save', {})[0] == 404
        payload = dict(password='six123', confirmation='six123')
        assert request(base, '/api-keys/api/credentials/create', payload, origin='https://hostile.invalid')[0] == 403
        assert not local_vault.path.exists()
        assert request(base, '/api-keys/api/credentials/create', payload)[0] == 200
        code, raw, _ = request(base, '/api-keys/api/credentials/add', dict(label='Devkey1', provider='deepseek', api_key=KEY))
        assert code == 200 and not json.loads(raw)['usage_known']
        cipher = local_vault.path.read_bytes()
        code, models, _ = request(base, '/')
        assert code == 200 and b'id="apiVault"' not in models and b'href="/api-keys/"' in models
        assert b'Set up DeepSeek' in models
        assert json.loads(request(base, '/api/credentials')[1])['keys'][0]['label'] == 'Devkey1'
        assert request(base, '/key-selector.js')[0] == 200
        assert request(base, '/api-keys/credentials.js')[0] == 200
        assert local_vault.path.read_bytes() == cipher and store.path.read_bytes() == before
