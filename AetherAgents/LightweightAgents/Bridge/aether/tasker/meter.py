"""Live spend of the turns in flight, so a sprint's cost cap can end a turn at its next model call.

The Timer binds each turn's worker thread to a key (begin), the shared tool loop reports every model call's tokens
(report), the Timer reads the in-flight totals when it checks the cap (inflight) and settles a turn once its final
cost is accounted (settle). A thread with no binding (tests, a turn run by hand) reports nowhere.

Never: decide anything. It only counts; the Timer compares the count with the cap.
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_local = threading.local()
_inflight: dict = {}          # key -> [prompt tokens, completion tokens]


def begin(key) -> None:
    with _lock:
        _inflight[key] = [0, 0]
    _local.key = key


def leave() -> None:
    _local.key = None


def current_key():
    return getattr(_local, 'key', None)


def report(prompt_tokens, completion_tokens) -> None:
    key = getattr(_local, "key", None)
    if key is None:
        return
    with _lock:
        if key in _inflight:
            _inflight[key][0] += int(prompt_tokens or 0)
            _inflight[key][1] += int(completion_tokens or 0)


def inflight(keys) -> tuple:
    with _lock:
        rows = [_inflight[k] for k in keys if k in _inflight]
    return sum(r[0] for r in rows), sum(r[1] for r in rows)


def settle(key) -> None:
    with _lock:
        _inflight.pop(key, None)
