"""Own the one-time welcome of a new install's default Admin (Michel, 2026-11-07).

One button does what otherwise takes two pages: the Admin's own password, then their API key (DeepSeek, Claude or
OpenAI) saved encrypted, granted to their account, and offered as that provider's connection. The person types both;
nothing here keeps them.

Must never: run unless this account's welcome is due, keep or log a password or key, replace an existing key store,
or overwrite a model connection that already exists.
"""
import uuid

from . import key_assignments

STARTER = ('Starter values chosen in Model connections; not verified provider limits. '
           'A model listing does not verify capacity.')

# The same records Settings → Models saves for its Hosted provider presets (model_connections.html, PROVIDERS).
# Michel, 2026-11-07: users attach DeepSeek, Claude, OpenAI or a local model; the welcome offers the hosted three.
PROVIDERS = {
    'deepseek': dict(label='DeepSeek', connection='deepseek-flash', profile=dict(
        description='DeepSeek connection', adapter_type='deepseek', endpoint='https://api.deepseek.com',
        resource_pool='deepseek_cloud', requestedApiIdentifier='deepseek-flash', dialect='deepseek',
        contextWindow=65536, maxOutputTokens=8192, timeoutSecs=120, maxToolCalls=16,
        reasoningMode='off', thinking='disabled', reasoningTelemetry='not-reported')),
    # Claude through Anthropic's OpenAI-compatible endpoint; Aether sends no temperature there (model.sends_temperature).
    'anthropic': dict(label='Claude (Anthropic)', connection='claude-opus-5-5', profile=dict(
        description='Claude (Anthropic) connection', adapter_type='openai-compatible',
        endpoint='https://api.anthropic.com/v1', resource_pool='anthropic_cloud',
        requestedApiIdentifier='claude-opus-5-5', dialect='openai',
        contextWindow=200000, maxOutputTokens=16000, timeoutSecs=300, maxToolCalls=16)),
    'openai': dict(label='OpenAI', connection='gpt-5.5', profile=dict(
        description='OpenAI connection', adapter_type='openai-compatible', endpoint='https://api.openai.com/v1',
        resource_pool='openai_cloud', requestedApiIdentifier='gpt-5.5', dialect='openai',
        contextWindow=131072, maxOutputTokens=16000, timeoutSecs=180, maxToolCalls=16)),
}


def connection_profile(provider, key_id):
    p = dict(PROVIDERS[provider]['profile'])
    p.update(credential_ref=dict(kind='vault', name=key_id), identifierKind='logical-alias', capabilitySource=STARTER,
             contextBudget=dict(workingContextTokens=p['contextWindow'], toolResultReserveTokens=4096,
                                safetyMarginTokens=2048, minimumInputTokens=1024, maxToolResultChars=12000))
    return p


def complete(access, actor, body, state, token):
    """Return (answer, fresh session token or None)."""
    if not access.welcome_due(actor):
        raise ValueError('The welcome has already been completed or skipped.')
    action = body.get('action')
    if action == 'skip':
        access.welcome_done()
        return dict(skipped=True), None
    if action != 'finish':
        raise ValueError('Choose Finish setup or Skip for now.')
    key = body.get('api_key', body.get('deepseek_key')) or ''
    if not isinstance(key, str):
        raise ValueError('Paste the API key as text.')
    key = key.strip()
    provider = body.get('provider') or 'deepseek'
    if provider not in PROVIDERS:
        raise ValueError('Choose DeepSeek, Claude or OpenAI.')
    runtime = state.get('runtime_keys')
    store = state.get('model_settings')
    if key:
        # Check everything the key needs before the password changes, so a refusal changes nothing.
        from aether.keys.vault import _key
        _key(key)
        status = runtime.vault.status()
        if not status['supported']:
            raise ValueError('Saving API keys needs Windows. Finish without the key and add it on a Windows computer.')
        if status['configured']:
            raise ValueError('This installation already has saved API keys. Finish without the key, then add it in '
                             'Settings → API Keys.')
        if PROVIDERS[provider]['connection'] in store.read()['document']['profiles']:
            raise ValueError('That connection already exists. Finish without the key, then add it in Settings.')
    user, fresh = access.welcome_password(actor, body)
    connection = None
    try:
        if key:
            connection = _save_key(access, actor, body.get('password'), provider, key, runtime, store, fresh)
    except (ValueError, PermissionError, OSError) as exc:
        # The password is already changed and the old session ended: answer with the new session either way.
        access.welcome_done()
        return dict(user=user, connection=None, key_error=str(exc)), fresh
    access.welcome_done()
    return dict(user=user, connection=connection, provider=PROVIDERS[provider]['label'] if connection else None), fresh


def _save_key(access, actor, password, provider, key, runtime, store, fresh):
    # The API password (asked again only to change saved keys) starts as the same new password.
    vault = runtime.vault
    vault.create(password, password)
    vault.add(PROVIDERS[provider]['label'], provider, key)
    runtime.publish()
    key_id = vault.status()['keys'][0]['id']
    unlocked = key_assignments.lease(access, fresh, vault, grant=True)
    mine = next(u for u in access.read()['accounts'] if u['id'] == actor['id']).get('keys', [])
    key_assignments.change(access, actor, dict(action='assign', revision=access.read()['revision'],
                                               id=actor['id'], keys=[*mine, key_id]), runtime, unlocked)
    current = store.read()
    document = current['document']
    name = PROVIDERS[provider]['connection']
    document['profiles'][name] = connection_profile(provider, key_id)
    store.save(document, current['revision'], operator=str(uuid.UUID(actor['id'])))
    try:
        from aether.backends.settings_repair import retain_known_good
        retain_known_good(store)
    except (OSError, ValueError):
        pass  # A missing known-good copy never undoes a saved connection (as Settings → Models).
    vault.lock()  # Saved keys keep working while locked; changing them asks for the API password.
    return name
