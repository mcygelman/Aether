"""Compatibility routing for unbound legacy callers, never a v3 fallback.

This is the sole authority for the historical local default. It is not a
deployment, a capability declaration, or an SDK/dispatch qualification rule.
Bound work supplies its endpoint explicitly from its captured deployment.
"""

LEGACY_LMSTUDIO_ENDPOINT = 'http://localhost:1234/v1'


def legacy_ip_endpoint(*, api_path=True):
    """Preserve the IP spelling and optional origin used by older callers."""
    endpoint = LEGACY_LMSTUDIO_ENDPOINT.replace('://localhost:', '://127.0.0.1:', 1)
    return endpoint if api_path else endpoint.removesuffix('/v1')
