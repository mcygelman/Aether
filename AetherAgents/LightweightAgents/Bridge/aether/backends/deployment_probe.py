"""DRAFT saved-deployment health operation; no generation, loading or admission."""
import json
import math
import urllib.error
import urllib.request

from aether.config.context_configuration import ContextConfigurationError
from aether.backends.deployment_profiles import ADAPTER_TYPES, AVAILABLE_ADAPTERS, validate_deployments
from aether.config.model_settings_store import StaleSettings, utc_now
from aether.credentials.provider_credentials import resolve_reference, CredentialError
from aether.credentials.redaction import register_secret, redact

# Availability is shared with preparation; never guess a fallback route.
PROBE_ADAPTERS = AVAILABLE_ADAPTERS
# Probe parsing safeguard only; not an inference/context/output allowance.
MAX_PROBE_BODY_BYTES = 1 << 20


def adapter_status():
    return {name: {'probe_available': name in PROBE_ADAPTERS} for name in ADAPTER_TYPES}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, 'Redirect refused', headers, fp)


class HealthClient:
    """Only the existing GET /models probe semantics; intentionally no chat/warmup."""
    def __init__(self, endpoint, api_key):
        self.endpoint = endpoint
        self.model_ids = []
        self.http_status = None
        self.headers = {'Content-Type': 'application/json'}
        if api_key:
            self.headers['Authorization'] = 'Bearer ' + api_key

    def probe(self, timeout_secs):
        self.model_ids = []
        request = urllib.request.Request(self.endpoint.rstrip('/') + '/models',
                                         method='GET', headers=dict(self.headers))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(request, timeout=timeout_secs) as response:
                self.http_status = response.status
                reachable = 200 <= response.status < 300
                # IDs are observations only, not model loading/tool qualification.
                try:
                    raw = response.read(MAX_PROBE_BODY_BYTES + 1)
                    body = json.loads(raw) if len(raw) <= MAX_PROBE_BODY_BYTES else None
                except (ValueError, UnicodeError):
                    body = None
                if isinstance(body, dict) and isinstance(body.get('data'), list):
                    self.model_ids = [item['id'] for item in body['data']
                        if isinstance(item, dict) and isinstance(item.get('id'), str)]
                return reachable
        except urllib.error.HTTPError as error:
            self.http_status = error.code
            error.close()
            return False
        except (OSError, urllib.error.URLError):
            return False


def probe_saved(store, profile_id, expected_revision, timeout_seconds):
    if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0):
        raise ContextConfigurationError('Probe requires an explicit positive finite timeout_seconds')
    loaded = store.read()
    if loaded['revision'] != expected_revision:
        raise StaleSettings('Settings changed before probe; reload first')
    profiles = validate_deployments(loaded['document'])
    if not isinstance(profile_id, str) or profile_id not in profiles:
        raise ContextConfigurationError('Select an existing deployment for probe')
    profile = profiles[profile_id]
    if profile.adapter_type not in PROBE_ADAPTERS:
        raise ContextConfigurationError('Selected adapter unavailable for probe')
    reference = profile.credential_ref.to_mapping() if profile.credential_ref else None
    if profile.adapter_type == 'deepseek' and reference is None:
        raise CredentialError('Missing credential reference. Select an environment variable or sealed entry name, never the key.')
    try:
        key = resolve_reference(reference)
    except CredentialError:
        raise CredentialError('Missing credential. Configure the selected environment variable in the Mission Control process '
                              'or use an existing sealed entry; restart Mission Control after changing its environment.') from None
    register_secret(key)  # Registered before anything can format an error or log line (Praxis M02).
    try:
        # Every operation gets detached headers and a detached route, never a live store lookup.
        client = HealthClient(profile.endpoint, key)
        reachable = client.probe(timeout_secs=timeout_seconds)
    except Exception:
        raise ContextConfigurationError('Deployment probe could not complete; no inference was performed') from None
    finally:
        key = None
        if 'client' in locals():
            client.headers.pop('Authorization', None)
    status = client.http_status
    listed = profile.requested_api_identifier in client.model_ids
    if status in (401, 403):
        code, message = 'bad_credential', 'Authentication refused. Replace or authorize the credential in its local source, then check again.'
    elif status == 429:
        code, message = 'rate_limited', 'Provider rate limit reached. Wait for the provider limit to reset, then check again.'
    elif not reachable:
        code, message = 'unreachable', 'Endpoint unavailable. Check the API base URL, network and provider status, then check again.'
    elif not client.model_ids:
        code, message = 'invalid_model_listing', 'The endpoint answered but returned no usable model listing. Check that the base URL exposes GET /models.'
    elif not listed:
        code, message = 'unknown_model', 'Requested model is not listed for this account. Choose an exact listed model ID, save, then check again.'
    else:
        code, message = 'ready', 'Authenticated model listing succeeded. Continue to Deployment admission (all campaign roles). No inference was performed.'
    return dict(profile_id=profile_id, revision=loaded['revision'], adapter_type=profile.adapter_type,
                endpoint=profile.endpoint, reachable=reachable, inference_performed=False,
                healthy=code == 'ready', code=code, message=message, http_status=status,
                authentication='accepted' if reachable and reference else 'refused' if status in (401,403) else 'unconfirmed',
                requested_model=profile.requested_api_identifier, model_listed=listed,
                identity_strength='provider-listing-weaker' if profile.adapter_type == 'deepseek' else 'model-listing-only',
                probed_at=utc_now(), model_ids=[redact(model) for model in client.model_ids])
