"""Credential redaction (MOD-03 Credentials): registered secrets and credential-shaped text never
reach a log, a record or an error message.

Moved here on 2026-10-08, unchanged, from aether.runtime.orchestration.charter_dialog, which
still offers every name (the same objects, so `_secrets` stays one set). Backends' charter
transport registers its key here without importing the Runtime.

Inputs:  register_secret(value): an already-loaded credential, never read for logging.
Outputs: redact(value) for dicts, lists and text; exception_summary(exc), redacted then cut.
"""
import json
import re
import threading

_secrets = set()
_secret_lock = threading.RLock()
_credential_field = re.compile(r'authorization|proxy[_-]?authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|secret|password', re.I)
_header_field = re.compile(r'(?:(?:request|response|http)[_-])?headers?', re.I)


def register_secret(value):
    """Transport registers an already-loaded credential; never read it for logging."""
    if isinstance(value,str) and value:
        with _secret_lock:
            _secrets.update((value,json.dumps(value)[1:-1],repr(value)[1:-1]))


def exception_summary(exc):
    # Redact before truncation; never cut a token so it evades a credential rule.
    return (type(exc).__name__+': '+' '.join(redact(str(exc)).split()))[:500]


def redact(value):
    if isinstance(value, dict):
        return {k: ('[REDACTED]' if (_credential_field.fullmatch(k) or
                    (_header_field.fullmatch(k) and isinstance(v,(dict,list))))
                    else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        with _secret_lock:
            for secret in sorted(_secrets,key=len,reverse=True):
                value=value.replace(secret,'[REDACTED]')
        try:
            parsed=json.loads(value)
            if isinstance(parsed,(dict,list)):
                safe=redact(parsed)
                if safe!=parsed:
                    return json.dumps(safe,ensure_ascii=False)
        except (ValueError,TypeError,RecursionError):
            pass
        value = re.sub(r'(?i)\bBearer\s+[^\s"\'<>]+', 'Bearer [REDACTED]', value)
        value = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[REDACTED]', value)
        # A document's "headers: Introduction, Examples" is ordinary prose.
        # Header collections and explicit credential assignments remain private.
        value = re.sub(r'''(?im)(?:\b|\\[nrt])((?:(?:request|response|http)[_-])?headers?)["']?\s*[:=]\s*(?:\{[^\r\n]*?\}|\[[^\r\n]*?\])''',r'\1: [REDACTED]',value)
        value = re.sub(r'(?im)(?:\b|\\[nrt])(authorization|proxy[_-]?authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password)["\']?\s*[:=]\s*[^\r\n]+', r'\1: [REDACTED]', value)
    return value
