"""Strict JSON (MOD-09 Integrity): duplicate keys and non-finite numbers are refused.

Moved down from Configuration on 2026-10-08, unchanged, so Integrity's exact-decision
round trip no longer imports Configuration. Configuration still offers both names
(aether.config.context_configuration.ContextConfigurationError and
aether.config.model_settings_store.parse_json); they are these same objects.
"""
from __future__ import annotations

import json
import re


class ContextConfigurationError(ValueError):
    pass


def parse_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ContextConfigurationError(f'Duplicate configuration field: {key}')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                              parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, UnicodeError) as exc:
        raise ContextConfigurationError(f'Invalid settings JSON: {exc}') from exc


# -- Model text: exactly one fenced JSON block (design decision, 2026-10-10) --
# A model may wrap its whole JSON answer in ONE Markdown code fence. Accepted only when that single
# fence encloses the ENTIRE output: only whitespace outside it, an absent or `json` language tag, and
# no other fence inside. Anything else is returned unchanged and is refused by the strict parse
# exactly as before. Callers record `fence_unwrapped: True` and keep the raw bytes as received.
_FENCE_OPEN = '```'


def unfence(raw):
    """(inner, True) when `raw` (str or bytes) is exactly one whole-output fenced block, else (raw, False).
    The inner text is returned in the same type as `raw`."""
    as_bytes = isinstance(raw, (bytes, bytearray))
    try:
        text = bytes(raw).decode('utf-8') if as_bytes else raw
    except UnicodeError:
        return raw, False
    if not isinstance(text, str):
        return raw, False
    body = text.strip()
    if not (body.startswith(_FENCE_OPEN) and body.endswith(_FENCE_OPEN)) or len(body) < 2 * len(_FENCE_OPEN):
        return raw, False
    first, newline, rest = body[len(_FENCE_OPEN):].partition('\n')
    if not newline or first.strip() not in ('', 'json'):
        return raw, False
    inner = rest[:-len(_FENCE_OPEN)]
    if _FENCE_OPEN in inner:      # A second fence, or text after an inner closing fence: refused.
        return raw, False
    inner = inner.rstrip('\r\n')
    return (inner.encode('utf-8') if as_bytes else inner), True


# -- Model citations: exact quotes, whitespace-tolerant (design decision, 2026-10-11) --
# A quote matches when it occurs exactly, or after collapsing every run of whitespace (spaces, tabs, line breaks)
# to one space on BOTH the quote and the source. Every other character must match exactly: look-alike symbols
# (for example the corner brackets of Aether's lexicon) are never folded together.
_WHITESPACE_RUN = re.compile(r'[ \t\r\n\f\v]+')


def quote_match(quote, text):
    """'exact', 'whitespace-normalized', or None when the quote does not occur in the text."""
    if not isinstance(quote, str) or not isinstance(text, str) or not quote.strip():
        return None
    if quote in text:
        return 'exact'
    collapsed = _WHITESPACE_RUN.sub(' ', quote).strip()
    return 'whitespace-normalized' if collapsed and collapsed in _WHITESPACE_RUN.sub(' ', text) else None
