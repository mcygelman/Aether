"""Strict request bodies for local JSON routes (lane F, 10-11/10-12): one shared copy.

declared_length: the one exact Content-Length, with the framing rules of the shared refusal disposition
(model_settings_http.dispose_request_body): no Transfer-Encoding, one distinct value, ASCII digits only (int() alone
would accept "1_0" or " +10"). read_body: exactly the declared bytes against a deadline; a stalled or short body is
refused, never awaited longer.
"""
from __future__ import annotations

import time

BODY_SECONDS = 30.0


def declared_length(headers, message='Submit exact JSON'):
    lengths = headers.get_all('Content-Length') or []
    if headers.get('Transfer-Encoding') is not None or len(set(v.strip() for v in lengths)) != 1:
        raise ValueError(message)
    value = lengths[0].strip()
    if not value.isdigit() or not value.isascii():
        raise ValueError(message)
    return int(value)


def read_body(handler, size, seconds=None):
    seconds = BODY_SECONDS if seconds is None else seconds
    sock = getattr(handler, 'connection', None)
    deadline = time.monotonic() + seconds; chunks = []; remaining = size
    previous = sock.gettimeout() if sock is not None else None
    reader = getattr(handler.rfile, 'read1', handler.rfile.read)
    try:
        while remaining > 0:
            left = deadline - time.monotonic()
            if left <= 0: raise ValueError('The request body did not arrive in time')
            if sock is not None: sock.settimeout(left)
            chunk = reader(min(remaining, 65536))
            if not chunk: raise ValueError('The request body ended early')
            chunks.append(chunk); remaining -= len(chunk)
    except OSError as exc:   # Includes socket.timeout.
        raise ValueError('The request body did not arrive in time') from exc
    finally:
        if sock is not None:
            try: sock.settimeout(previous)
            except OSError: pass
    return b''.join(chunks)


def json_body(handler, limit, message):
    """Content-Type application/json, an exact declared length within `limit`, read against the deadline."""
    import json
    if handler.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
        raise ValueError(message)
    size = declared_length(handler.headers, message)
    if not 0 < size <= limit:
        raise ValueError(message)
    return json.loads(read_body(handler, size).decode('utf-8'))
