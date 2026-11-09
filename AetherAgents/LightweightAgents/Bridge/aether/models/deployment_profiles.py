"""Deployment profiles (MOD-05 Model Catalog): DRAFT v3 deployment records and their validation.

Moved here from aether.backends.deployment_profiles on 2026-10-08, unchanged except that
validation checks the adapter/dialect combination with check_dialect (the same table and
error) instead of building Backends' dialect object, so the Model Catalog validates v3
registries without importing Backends. Backends keeps deployment_dialect and profile_dialect
and re-exports everything here. No transport or secret I/O on validation.
"""
from dataclasses import dataclass, field
from copy import deepcopy
import re
from urllib.parse import urlsplit

from aether.models.runtime_profiles import RuntimeProfile, RuntimeProfileError, validate_registry

ADAPTER_TYPES = ('lmstudio', 'llamacpp', 'deepseek', 'openai-compatible')
AVAILABLE_ADAPTERS = ('lmstudio', 'llamacpp', 'deepseek', 'openai-compatible')
IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')
WINDOWS_RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
                    *(f'LPT{i}' for i in range(1, 10))}


def check_dialect(adapter_type, requested):
    """v3 adapter authority: refuse a contradictory adapter_type/dialect pair."""
    allowed = {'lmstudio': ('auto', 'openai'), 'llamacpp': ('auto', 'openai'),
               'deepseek': ('auto', 'deepseek'),
               'openai-compatible': ('auto', 'openai', 'deepseek')}
    if adapter_type not in allowed or requested not in allowed[adapter_type]:
        raise RuntimeProfileError('Contradictory adapter_type/dialect selection')


@dataclass(frozen=True)
class CredentialRef:
    kind: str
    name: str

    @classmethod
    def from_mapping(cls, value):
        if value is None:
            return None
        if (not isinstance(value, dict) or set(value) != {'kind', 'name'}
                or value['kind'] not in ('env', 'sealed', 'vault')
                or not isinstance(value['name'], str) or not IDENTIFIER.fullmatch(value['name'])
                or (value['kind'] == 'sealed' and value['name'].upper() in WINDOWS_RESERVED)):
            raise RuntimeProfileError('credential_ref requires an env, sealed or vault identifier, never a path or key')
        return cls(value['kind'], value['name'])

    def to_mapping(self):
        return {'kind': self.kind, 'name': self.name}


def endpoint_identity(endpoint):
    """Validate a route; canonical identity is only for scoped alias comparison."""
    try:
        if (not isinstance(endpoint, str) or not endpoint or any(c.isspace() for c in endpoint)
                or any(ord(c) < 32 for c in endpoint) or '\\' in endpoint or '?' in endpoint or '#' in endpoint):
            raise ValueError
        parts = urlsplit(endpoint)
        if (parts.scheme not in ('http', 'https') or not parts.hostname
                or parts.username is not None or parts.password is not None):
            raise ValueError
        port = parts.port
        if port is not None and port < 1:
            raise ValueError
        return (parts.scheme, parts.hostname.lower(), port or (443 if parts.scheme == 'https' else 80),
                parts.path.rstrip('/'))
    except (ValueError, TypeError):
        raise RuntimeProfileError('endpoint requires an HTTP(S) API base URL without credentials, query or fragment') from None


@dataclass(frozen=True)
class DeploymentProfile(RuntimeProfile):
    adapter_type: str = ''
    resource_pool: str = ''
    credential_ref: CredentialRef | None = None
    thinking: str | None = None
    _thinking_present: bool = field(default=False, repr=False)
    reasoning_telemetry: str | None = None

    @classmethod
    def from_mapping(cls, name, value):
        if not isinstance(value, dict):
            raise RuntimeProfileError('Deployment profile must be an object')
        required = {'adapter_type', 'resource_pool', 'credential_ref'}
        if not required.issubset(value) or 'backend' in value:
            raise RuntimeProfileError('v3 requires adapter_type, resource_pool and credential_ref; backend is not a v3 field')
        adapter = value['adapter_type']
        pool = value['resource_pool']
        if not isinstance(adapter, str) or adapter not in ADAPTER_TYPES:
            raise RuntimeProfileError('Unsupported adapter_type')
        if not isinstance(pool, str) or not pool.strip() or pool != pool.strip() or any(ord(c) < 32 for c in pool):
            raise RuntimeProfileError('resource_pool must be nonblank text without outer whitespace or controls')
        endpoint_identity(value.get('endpoint'))
        credential = CredentialRef.from_mapping(value['credential_ref'])
        thinking = value.get('thinking')
        if thinking is not None and (not isinstance(thinking, str)
                or thinking not in ('enabled', 'disabled') or adapter != 'deepseek'):
            raise RuntimeProfileError('thinking requires enabled/disabled on deepseek, or null/omitted')
        telemetry = value.get('reasoningTelemetry')
        if 'reasoningTelemetry' in value and telemetry not in ('reported', 'not-reported'):
            raise RuntimeProfileError('reasoningTelemetry requires reported or not-reported')
        operating = {k: deepcopy(v) for k, v in value.items() if k not in required | {'thinking', 'reasoningTelemetry'}}
        # Compatibility representation is private to the typed object, not another saved authority.
        operating['backend'] = 'lmstudio' if adapter == 'lmstudio' else 'openai-compatible'
        for key in ('description', 'requestedApiIdentifier', 'identifierKind', 'capabilitySource', 'dialect'):
            if not isinstance(operating.get(key), str) or not operating[key].strip():
                raise RuntimeProfileError(f'{key} must be nonblank text')
        if 'tokenCountPolicy' in operating and 'contextBudget' not in operating:
            raise RuntimeProfileError('tokenCountPolicy requires an advanced budget')
        base = RuntimeProfile.from_mapping(name, operating, schema_version=2 if 'contextBudget' in operating else 1)
        check_dialect(adapter, base.dialect)
        return cls(**base.__dict__, adapter_type=adapter, resource_pool=pool, credential_ref=credential,
                   thinking=thinking, _thinking_present='thinking' in value, reasoning_telemetry=telemetry)

    def reasoning_off_switch(self):
        return {'lmstudio': 'Enable Thinking OFF and Preserve Thinking OFF, then unload/reload the model',
                'llamacpp': '--reasoning off in the server load configuration',
                'deepseek': 'thinking: disabled',
                'openai-compatible': 'reasoningMode: off in the deployment load configuration'}[self.adapter_type]

    def to_mapping(self):
        result = super().to_mapping()
        del result['backend']
        result.update(adapter_type=self.adapter_type, resource_pool=self.resource_pool,
                      credential_ref=self.credential_ref.to_mapping() if self.credential_ref else None)
        if self._thinking_present or self.thinking is not None:
            result['thinking'] = self.thinking
        if self.reasoning_telemetry is not None:
            result['reasoningTelemetry'] = self.reasoning_telemetry
        return result


def rebuild(name, configuration):
    """Rebuild a captured runtime configuration: the one rule every host uses (G9 slice 2, D2).

    A capture that carries adapter_type is a v3 deployment (DeploymentProfile, whose mapping
    holds credential_ref as {kind, name} only). Any other capture is the advanced-budget
    (schema 2) runtime profile every capture held before v3, rebuilt by exactly the call each
    site made until now, so v1/v2 behaviour is unchanged. No settings are read.
    """
    if isinstance(configuration, dict) and 'adapter_type' in configuration:
        return DeploymentProfile.from_mapping(name, configuration)
    return RuntimeProfile.from_mapping(name, configuration, schema_version=2)


BINDING_FIELDS = ('binding_version', 'deployment_id', 'config_revision', 'profile_json', 'profile_sha256',
                  'selection_source', 'prepared_by')


def capture_binding(name, configuration, *, config_revision, prepared_by):
    """The DeploymentBinding mapping (Backends' deployment_binding, binding_version 1) of one
    explicitly selected v3 deployment, for layers that may not import Backends (Roadmaps).

    Same bytes as DeploymentBinding.capture(..., profile_id=name): the canonical profile JSON
    and its sha256. Backends' DeploymentBinding.from_mapping re-verifies it wherever it is used.
    config_revision=None is for captures that kept no settings revision (staffing, contribution
    and closure runtimes): the binding then names the captured profile by its own sha256.
    """
    import hashlib
    import json
    profile = DeploymentProfile.from_mapping(name, configuration)
    payload = json.dumps(profile.to_mapping(), sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                         allow_nan=False)
    profile_sha256 = hashlib.sha256(payload.encode('utf-8')).hexdigest()
    for value in (name, prepared_by):
        if not isinstance(value, str) or not value.strip() or value != value.strip() or any(ord(c) < 32 for c in value):
            raise RuntimeProfileError('A deployment binding needs a nonblank deployment and preparer ID')
    if config_revision is None:
        config_revision = profile_sha256
    if not isinstance(config_revision, str) or not re.fullmatch('[0-9a-f]{64}', config_revision):
        raise RuntimeProfileError('A deployment binding needs the sha256 settings revision it was captured from')
    return dict(binding_version=1, deployment_id=name, config_revision=config_revision, profile_json=payload,
                profile_sha256=profile_sha256, selection_source='explicit', prepared_by=prepared_by)


def _hosted(profile) -> bool:
    """A provider reached over HTTPS on a non-local host (DeepSeek and similar), not a model server on this network."""
    parts = urlsplit(profile.endpoint)
    host = (parts.hostname or '').lower()
    local = host in ('localhost', '::1') or host.startswith('127.') or host.endswith('.local') or \
        re.match(r'^(10|192\.168|172\.(1[6-9]|2\d|3[01]))\.', host) is not None
    return parts.scheme == 'https' and not local and profile.adapter_type in ('deepseek', 'openai-compatible')


def validate_deployments(raw):
    if (not isinstance(raw, dict) or set(raw) != {'schemaVersion', 'default_profile_id', 'profiles'}
            or type(raw['schemaVersion']) is not int or raw['schemaVersion'] != 3
            or not isinstance(raw['profiles'], dict) or not raw['profiles']):
        raise RuntimeProfileError('v3 requires schemaVersion 3, default_profile_id and nonempty profiles')
    default = raw['default_profile_id']
    if default is not None and (not isinstance(default, str) or default not in raw['profiles']):
        raise RuntimeProfileError('default_profile_id must name an existing profile or be null')
    result = {}
    aliases = {}
    for name, value in raw['profiles'].items():
        if not isinstance(name, str) or not name.strip() or name != name.strip() or any(ord(c) < 32 for c in name):
            raise RuntimeProfileError('Deployment ID must be nonblank text without outer whitespace or controls')
        profile = DeploymentProfile.from_mapping(name, value)
        # A local server's alias names one loaded model, so two budgets for it would contradict each other. A hosted
        # provider's model has no loaded instance; budgets are per request, so a second profile (a documented smaller
        # allowance, say) is a choice, not a conflict (rehearsal 4, O6).
        if profile.identifier_kind == 'logical-alias' and not _hosted(profile):
            key = (profile.adapter_type, endpoint_identity(profile.endpoint), profile.requested_api_identifier.casefold())
            if key in aliases:
                raise RuntimeProfileError('duplicate logical API identifier within deployment endpoint')
            aliases[key] = name
        result[name] = profile
    return result


def migrate_deployments(raw, choices, *, default_profile_id):
    """Build a candidate only. Every deployment choice is explicit; no source inference."""
    profiles = validate_registry(raw, budget_aware=True)
    if not isinstance(choices, dict) or set(choices) != set(profiles):
        raise RuntimeProfileError('Migration requires choices for exactly every existing profile')
    candidate = {'schemaVersion': 3, 'default_profile_id': default_profile_id, 'profiles': {}}
    for name in profiles:
        selection = choices[name]
        if not isinstance(selection, dict) or set(selection) != {'adapter_type', 'resource_pool', 'credential_ref'}:
            raise RuntimeProfileError('Migration requires explicit adapter_type, resource_pool and credential_ref')
        value = deepcopy(raw['profiles'][name])
        del value['backend']
        value.update(deepcopy(selection))
        candidate['profiles'][name] = value
    validate_deployments(candidate)
    return candidate
