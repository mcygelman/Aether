"""S00073.7 (B2) — ModelAdapter shape, LMStudio api_key/header changes, deepseek_client factory.
No network calls: these test construction, header logic, and the ModelAdapter Protocol contract,
not live vendor behavior (that's a documented-path live gate on the actual host, not a mock)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aether_bridge.model import (  # noqa: E402
    DEFAULT_OUTPUT_RESERVE,
    DEEPSEEK_DEFAULT_WINDOW,
    DEEPSEEK_DEFAULT_MODEL,
    DEEPSEEK_ENDPOINT,
    DEEPSEEK_V4_CAPABILITY,
    DEEPSEEK_V41_FLASH_CAPABILITY,
    DEEPSEEK_V4_CAPABILITY_CHECKED_ON,
    DEEPSEEK_V4_CAPABILITY_SOURCE,
    LMStudio,
    ModelAdapter,
    ModelError,
    assistant_history_message,
    deepseek_client,
)
from aether_bridge.dialect import DEEPSEEK, OPENAI_ONLY  # noqa: E402
import aether_bridge.model as model_mod  # noqa: E402


def test_lmstudio_satisfies_modeladapter_protocol():
    client = LMStudio()
    assert isinstance(client, ModelAdapter)


def test_minimal_duck_typed_client_satisfies_modeladapter_protocol():
    class Client:
        context_window = 8192

        def chat(self, messages, tools=None):
            return {"role": "assistant", "content": "offline fixture"}

        def warmup(self, retries=2):
            return True

        def probe(self, timeout_secs=1.5):
            return True

    assert isinstance(Client(), ModelAdapter)


def test_lmstudio_default_has_no_auth_header_backward_compat():
    client = LMStudio()
    headers = client._headers()
    assert "Authorization" not in headers
    assert headers["Content-Type"] == "application/json"


def test_lmstudio_with_api_key_adds_bearer_header():
    client = LMStudio(api_key="sk-test-123")
    headers = client._headers()
    assert headers["Authorization"] == "Bearer sk-test-123"


def test_lmstudio_extra_headers_merge_without_dropping_auth():
    client = LMStudio(api_key="sk-test-123", extra_headers={"X-Custom": "1"})
    headers = client._headers()
    assert headers["Authorization"] == "Bearer sk-test-123"
    assert headers["X-Custom"] == "1"


def test_assistant_history_preserves_reasoning_without_private_telemetry():
    calls = [{"id": "c1", "type": "function", "function": {"name": "read", "arguments": "{}"}}]
    result = assistant_history_message({
        "content": "",
        "reasoning_content": "vendor thinking token",
        "tool_calls": calls,
        "_usage": {"total_tokens": 10},
        "_finish_reason": "tool_calls",
    })
    assert result == {
        "role": "assistant",
        "content": "",
        "reasoning_content": "vendor thinking token",
        "tool_calls": calls,
    }


def test_deepseek_client_factory_wires_endpoint_and_key():
    client = deepseek_client("sk-real-key", context_window=64000)
    assert client.endpoint == DEEPSEEK_ENDPOINT
    assert client.model == DEEPSEEK_DEFAULT_MODEL
    assert client.context_window == 64000
    assert client.api_key == "sk-real-key"
    assert client.dialect is DEEPSEEK
    assert isinstance(client, ModelAdapter)


def test_deepseek_client_requires_nonempty_key():
    try:
        deepseek_client("")
        raise AssertionError("expected ModelError for empty api_key")
    except ModelError:
        pass


# --- max_tokens hard cap (S00073.7 hardening, found live 2026-07-18) ---------------------------
# A qwen3.6 packet ran away past its own 35,580-token context_window (44k+ generated, cut off by
# the operator) because nothing capped a single generation's length. Fixed by always sending an
# OpenAI-compatible `max_tokens` in the request body unless explicitly disabled.

class _FakeHTTPResponse:
    def __init__(self, payload: dict):
        self._payload = payload
    def read(self):
        return json.dumps(self._payload).encode("utf-8")
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def _capture_request_body(monkeypatch_target, client, messages=None):
    """Swap urllib.request.urlopen for a fake that records the outgoing request body and
    returns a minimal valid completion, so chat() can be exercised with zero network I/O."""
    captured = {}
    real_request_cls = model_mod.urllib.request.Request

    def fake_request(url, data=None, headers=None, method=None):
        captured["body"] = json.loads(data.decode("utf-8"))
        return real_request_cls(url, data=data, headers=headers, method=method)

    def fake_urlopen(req, timeout=None):
        return _FakeHTTPResponse({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 1}, "model": "test-model",
        })

    monkeypatch_target.setattr(model_mod.urllib.request, "Request", fake_request)
    monkeypatch_target.setattr(model_mod.urllib.request, "urlopen", fake_urlopen)
    client.chat(messages or [{"role": "user", "content": "hi"}])
    return captured["body"]


class _MonkeyPatch:
    """Minimal hand-rolled monkeypatch (no pytest dependency in this stdlib-only test suite)."""
    def __init__(self):
        self._restores = []
    def setattr(self, obj, name, value):
        self._restores.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)
    def undo(self):
        for obj, name, old in reversed(self._restores):
            setattr(obj, name, old)


def test_chat_sends_max_tokens_by_default():
    mp = _MonkeyPatch()
    try:
        client = LMStudio(model="qwen-test")
        body = _capture_request_body(mp, client)
        assert body["max_tokens"] == 8192
    finally:
        mp.undo()


def test_chat_max_tokens_zero_disables_cap():
    mp = _MonkeyPatch()
    try:
        client = LMStudio(model="qwen-test", max_tokens=0)
        body = _capture_request_body(mp, client)
        assert "max_tokens" not in body
    finally:
        mp.undo()


def test_chat_custom_max_tokens_honored():
    mp = _MonkeyPatch()
    try:
        client = LMStudio(model="qwen-test", max_tokens=1024)
        body = _capture_request_body(mp, client)
        assert body["max_tokens"] == 1024
    finally:
        mp.undo()


def test_qwen_nonthinking_mode_omits_invalid_reasoning_effort_request():
    extras = LMStudio(model="aether-mobius", qwen_reasoning_mode="off")._body_extras()
    assert "reasoning_effort" not in extras
    assert "chat_template_kwargs" not in extras


def _qwen_response(reasoning_tokens_marker):
    details = {} if reasoning_tokens_marker is None else {
        "reasoning_tokens": reasoning_tokens_marker
    }
    return {
        "choices": [{"message": {
            "content": "ok",
            "tool_calls": [{"id": "danger", "type": "function", "function": {
                "name": "write_file", "arguments": "{}"
            }}],
        }, "finish_reason": "tool_calls"}],
        "usage": {
            "total_tokens": 2,
            "completion_tokens_details": details,
        },
        "model": "aether-mobius",
    }


def _chat_with_payload(payload):
    mp = _MonkeyPatch()
    real_request_cls = model_mod.urllib.request.Request
    try:
        mp.setattr(
            model_mod.urllib.request,
            "Request",
            lambda url, data=None, headers=None, method=None: real_request_cls(
                url, data=data, headers=headers, method=method
            ),
        )
        mp.setattr(
            model_mod.urllib.request,
            "urlopen",
            lambda req, timeout=None: _FakeHTTPResponse(payload),
        )
        return LMStudio(qwen_reasoning_mode="off").chat(
            [{"role": "user", "content": "work"}], tools=[{"type": "function"}]
        )
    finally:
        mp.undo()


def test_qwen_nonthinking_contract_accepts_explicit_zero_reasoning():
    result = _chat_with_payload(_qwen_response(0))
    assert result["_usage"]["completion_tokens_details"]["reasoning_tokens"] == 0


def test_qwen_nonthinking_contract_refuses_reasoning_before_returning_tool_calls():
    try:
        _chat_with_payload(_qwen_response(12))
        raise AssertionError("expected reasoning contract violation")
    except ModelError as exc:
        assert "before tool execution" in str(exc)


def test_qwen_nonthinking_contract_refuses_missing_reasoning_telemetry():
    try:
        _chat_with_payload(_qwen_response(None))
        raise AssertionError("expected missing telemetry refusal")
    except ModelError as exc:
        assert "unverified" in str(exc)


def test_qwen_medium_and_xhigh_are_supported_but_low_is_prohibited():
    for mode in ("medium", "xhigh"):
        mp = _MonkeyPatch()
        try:
            body = _capture_request_body(mp, LMStudio(qwen_reasoning_mode=mode))
            assert body["reasoning_effort"] == mode
        finally:
            mp.undo()
    try:
        LMStudio(qwen_reasoning_mode="low")
        raise AssertionError("expected low reasoning mode to be refused")
    except ValueError as exc:
        assert "low is prohibited" in str(exc)


def test_chat_extra_overrides_max_tokens():
    mp = _MonkeyPatch()
    try:
        client = LMStudio(model="qwen-test", max_tokens=4096, extra={"max_tokens": 200})
        body = _capture_request_body(mp, client)
        assert body["max_tokens"] == 200
    finally:
        mp.undo()


def test_chat_wraps_mid_request_disconnect_into_endpoint_outage_family():
    """ARA-09 live evidence (2026-08-22): stopping LM Studio mid-request raised a raw
    http.client.RemoteDisconnected that escaped chat(); the whole outage family must be
    reported as ModelError 'model endpoint unreachable'."""
    import http.client

    mp = _MonkeyPatch()
    try:
        def _closed_connection(req, timeout=None):
            raise http.client.RemoteDisconnected(
                "Remote end closed connection without response"
            )

        mp.setattr(model_mod.urllib.request, "urlopen", _closed_connection)
        try:
            LMStudio().chat([{"role": "user", "content": "work"}])
            raise AssertionError("expected endpoint outage ModelError")
        except ModelError as exc:
            assert "model endpoint unreachable" in str(exc)
    finally:
        mp.undo()


def test_deepseek_client_inherits_default_max_tokens_cap():
    assert DEEPSEEK_DEFAULT_MODEL == "deepseek-flash"
    assert DEEPSEEK_V41_FLASH_CAPABILITY.checked_on == "2026-09-10"
    client = deepseek_client("sk-real-key")
    assert DEEPSEEK.max_tokens_default == 384_000
    assert client.context_window == DEEPSEEK_DEFAULT_WINDOW == 1_000_000
    assert client.max_tokens == DEEPSEEK_V41_FLASH_CAPABILITY.max_output_tokens == 384_000
    assert client.max_tokens < client.context_window
    assert client.capability_source == DEEPSEEK_V41_FLASH_CAPABILITY.source


def test_deepseek_v4_capability_has_dated_official_provenance():
    assert DEEPSEEK_V4_CAPABILITY_SOURCE.startswith("https://api-docs.deepseek.com/")
    assert DEEPSEEK_V4_CAPABILITY_CHECKED_ON == "2026-08-05"
    assert DEEPSEEK_V4_CAPABILITY.context_window == 1_000_000
    assert DEEPSEEK_V4_CAPABILITY.max_output_tokens == 384_000
    assert (
        DEEPSEEK_V4_CAPABILITY.max_output_tokens + DEFAULT_OUTPUT_RESERVE
        <= DEEPSEEK_V4_CAPABILITY.context_window
    )


def test_local_client_retains_openai_dialect_and_8192_cap():
    client = LMStudio()
    assert client.dialect is OPENAI_ONLY
    assert client.max_tokens == 8192


def test_explicit_cap_is_clamped_to_declared_context_envelope():
    client = LMStudio(
        context_window=10_000,
        max_tokens=50_000,
        dialect=DEEPSEEK,
    )
    assert client.max_tokens == 10_000 - DEFAULT_OUTPUT_RESERVE


def test_chat_normalizes_dsml_and_preserves_raw_and_telemetry_end_to_end():
    raw = (
        "Checking now.\n<｜｜DSML｜｜tool_calls>"
        '<｜｜DSML｜｜invoke name="read_file">'
        '<｜｜DSML｜｜parameter name="path" string="true">notes.md</｜｜DSML｜｜parameter>'
        "</｜｜DSML｜｜invoke></｜｜DSML｜｜tool_calls>"
    )
    payload = {
        "choices": [{"message": {"content": raw}, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        "model": "deepseek-v4-flash",
    }
    mp = _MonkeyPatch()
    real_request_cls = model_mod.urllib.request.Request

    def fake_request(url, data=None, headers=None, method=None):
        return real_request_cls(url, data=data, headers=headers, method=method)

    def fake_urlopen(req, timeout=None):
        return _FakeHTTPResponse(payload)

    try:
        mp.setattr(model_mod.urllib.request, "Request", fake_request)
        mp.setattr(model_mod.urllib.request, "urlopen", fake_urlopen)
        result = deepseek_client("sk-test").chat([{"role": "user", "content": "read"}])
    finally:
        mp.undo()

    assert result["_dialect_outcome"] == "parsed"
    assert result["content"] == "Checking now.\n"
    assert len(result["tool_calls"]) == 1
    assert result["_usage"] == payload["usage"]
    assert result["_finish_reason"] == "tool_calls"
    assert result["_model"] == "deepseek-v4-flash"

    malformed_payload = {
        "choices": [{
            "message": {"content": "<｜｜FUTURE｜｜tool_calls>x</｜｜FUTURE｜｜tool_calls>"},
            "finish_reason": "stop",
        }],
        "usage": {"total_tokens": 4},
        "model": "deepseek-v4-flash",
    }
    mp = _MonkeyPatch()
    try:
        mp.setattr(model_mod.urllib.request, "Request", fake_request)
        mp.setattr(
            model_mod.urllib.request,
            "urlopen",
            lambda req, timeout=None: _FakeHTTPResponse(malformed_payload),
        )
        quarantined = deepseek_client("sk-test").chat(
            [{"role": "user", "content": "respond"}]
        )
    finally:
        mp.undo()
    assert quarantined["_dialect_raw"] == malformed_payload["choices"][0]["message"]["content"]
    assert quarantined["_usage"] == {"total_tokens": 4}
    assert quarantined["_finish_reason"] == "stop"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"{len(fns)}/{len(fns)} green")
