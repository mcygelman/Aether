"""Model clients — OpenAI-compatible /v1/chat/completions with tools.

Stdlib-only (urllib) so the Bridge runs anywhere Python runs, zero install.
Context window comes from config, not the server (LM Studio does not reliably
report it — same rule as wrapper adapter/lmstudio.rs; hosted APIs are asked
for their window at mission setup too, for the same reason: the *response*
tells us what model actually served the call, never what window it has).

S00073.7 (B2, design decision Q1): `LMStudio` is the one concrete implementation
of the informal ModelAdapter shape every caller in this package already uses
(`chat()`, `warmup()`, `probe()`, `.context_window`) — see the `ModelAdapter`
Protocol below. It was originally written LM-Studio-only; adding `api_key`
and `extra_headers` (both empty/no-op by default, zero behavior change for
existing local callers) turns it into a generic OpenAI-compatible client, so
a hosted vendor is a parameterized instance, not a parallel class. See
`deepseek_client()` at the bottom for the concrete Deepseek factory.
"""
from __future__ import annotations

import json
import hashlib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from aether.backends.dialect import DEEPSEEK, OPENAI_ONLY, Dialect, normalize
from aether.backends.legacy_endpoints import LEGACY_LMSTUDIO_ENDPOINT
from aether.models.runtime_profiles import DEFAULT_OUTPUT_RESERVE  # the Model Catalog's profile rule
_UNSET = object()


class ModelError(Exception):
    pass


def assistant_history_message(
    msg: dict,
    *,
    content: object = _UNSET,
    tool_calls: object = _UNSET,
) -> dict:
    """Build a replay-safe assistant message from a vendor response.

    DeepSeek thinking mode requires its returned ``reasoning_content`` to be
    passed back verbatim on the assistant history message. Rebuilding history
    from only visible content/tool calls loses that field and makes the next
    request fail with HTTP 400. Keep this vendor field while withholding all
    Aether-private ``_usage``/dialect telemetry from the outbound request.
    """
    history = {
        "role": "assistant",
        "content": msg.get("content") or "" if content is _UNSET else content,
    }
    if "reasoning_content" in msg:
        history["reasoning_content"] = msg["reasoning_content"]
    selected_calls = msg.get("tool_calls") if tool_calls is _UNSET else tool_calls
    if selected_calls:
        history["tool_calls"] = selected_calls
    return history


# ARA-05: the only two thinking states this Bridge will send. Reasoning effort is untouched.
THINKING_MODES = ("enabled", "disabled")
QWEN_REASONING_MODES = ("off", "medium", "xhigh")


@runtime_checkable
class ModelAdapter(Protocol):
    """Structural contract every model client in this package must satisfy.
    Not inherited from — Python duck-typing already enforces this everywhere
    `run_packet`/`deliver_and_gate`/`plan_with_client` accept a client. This
    Protocol exists so the contract is written down once, not re-derived from
    call sites."""
    context_window: int

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict: ...
    def warmup(self, retries: int = 2) -> bool: ...
    def probe(self, timeout_secs: float = 1.5) -> bool: ...


@dataclass
class LMStudio:
    endpoint: str = LEGACY_LMSTUDIO_ENDPOINT   # legacy only; bound callers pass their endpoint
    model: str = ""                              # coordinate with HITL pre-sprint
    temperature: float = 0.2
    context_window: int = 0  # caller must supply the real configured model window for budgeted runs
    timeout_secs: int = 300
    extra: dict = field(default_factory=dict)
    # empty = no Authorization header (local LM Studio needs none). G9 slice 2: neither the key
    # nor custom headers (which may carry Authorization) ever appear in repr()/str() or a log line.
    api_key: str = field(default="", repr=False)
    extra_headers: dict = field(default_factory=dict, repr=False)
    dialect: Dialect = OPENAI_ONLY
    capability_source: str = "unregistered/local configuration"
    # S00073.7 hardening (found live, 2026-07-18): nothing previously capped a single
    # generation's length -- a reasoning model can loop and generate far past its own
    # configured context_window with no natural stop, forcing KV-cache growth that can spill
    # into system RAM on modest hardware. max_tokens=0 disables the cap (old behavior,
    # available for tests); any positive value is sent as the OpenAI-compatible `max_tokens`
    # field so LM Studio/the vendor hard-stops generation there.
    # Raised 4096 -> 8192 same day: live C-2 rerun (mission cc25e152e815) showed 4096 is
    # sometimes too tight for this model's extended-thinking trace alone -- 2 of 8 packets
    # (Linus, Dewey) spent ~4094-4095 reasoning tokens and were cut off before emitting any
    # visible answer, landing as content-free "complete" packets. 8192 still hard-stops far
    # short of the ~44k-token runaway that motivated the cap in the first place.
    max_tokens: int | None = None
    # ARA-05 (2026-08-13): DeepSeek thinking mode, operator-selected per session. None means the
    # field is absent from the request body entirely -- the vendor default applies and nothing is
    # invented on the model's behalf. Only ever set for the DeepSeek dialect; local LM Studio
    # sessions leave it None. This does not change context/output limits or the tool loop.
    thinking: str | None = None
    # Stable Aether execution role selected from runtime_profiles.json. None preserves the
    # legacy direct-model path; it is telemetry only and never treated as served-model proof.
    runtime_profile: str | None = None
    # Qwen3.8 local-session contract. LM Studio 0.3.32's Qwen3.8 custom field accepts only
    # xhigh/medium/low as reasoning_effort values: sending `none` for Aether's `off` state is
    # rejected and silently skipped. Off is therefore selected in LM Studio's model Custom
    # Fields (Enable Thinking=false) and verified from response telemetry here. Deliberate modes
    # use valid effort values. `low` is deliberately unsupported after operator testing found it
    # consistently worse than either off or medium.
    qwen_reasoning_mode: str | None = None
    # Explicit v2 path; legacy clients retain their established reserve behavior.
    context_settings: object | None = None
    # Explicit deployment capability; omitted remains strict. No endpoint inference.
    reasoning_telemetry: str | None = None
    reasoning_off_switch: str = 'Enable Thinking OFF and Preserve Thinking OFF, then unload/reload the model'
    credential_ref: dict | None = field(default=None, repr=False)
    credential_bridge: object | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.reasoning_telemetry not in (None, 'reported', 'not-reported'):
            raise ValueError('unknown reasoning telemetry capability')
        if self.thinking is not None and self.thinking not in THINKING_MODES:
            raise ValueError(
                f"unknown thinking mode {self.thinking!r} (expected one of {THINKING_MODES})"
            )
        if (self.qwen_reasoning_mode is not None
                and self.qwen_reasoning_mode not in QWEN_REASONING_MODES):
            raise ValueError(
                f"unknown Qwen reasoning mode {self.qwen_reasoning_mode!r} "
                f"(expected one of {QWEN_REASONING_MODES}; low is prohibited)"
            )
        if self.max_tokens is None:
            self.max_tokens = self.dialect.max_tokens_default
        # The request cap and the budget layer must describe the same context
        # envelope. Clamp any vendor ceiling to leave room for a terminal answer.
        if self.context_settings is not None:
            from aether.config.context_configuration import ResolvedContext
            if type(self.context_settings) is not ResolvedContext:
                raise ValueError('typed resolved context settings required')
            self.context_settings.budget.validate_envelope(
                self.context_settings.declared_context_tokens, self.context_settings.max_output_tokens)
            if (self.context_window != self.context_settings.budget.working_context_tokens
                    or self.max_tokens != self.context_settings.max_output_tokens):
                raise ValueError('client window/output must match resolved context settings')
        elif self.context_window > DEFAULT_OUTPUT_RESERVE and self.max_tokens:
            self.max_tokens = min(
                self.max_tokens,
                self.context_window - DEFAULT_OUTPUT_RESERVE,
            )

    def _body_extras(self) -> dict:
        extras = {}
        if self.max_tokens:
            extras["max_tokens"] = self.max_tokens
        if self.thinking is not None:
            # DeepSeek's documented OpenAI-compatible shape. Absent when unset -- never
            # synthesized, and never derived from response history.
            extras["thinking"] = {"type": self.thinking}
        if self.qwen_reasoning_mode in ("medium", "xhigh"):
            extras["reasoning_effort"] = self.qwen_reasoning_mode
        return extras

    def _verify_qwen_reasoning_contract(self, usage: object, *, required: bool = False) -> None:
        """Fail before returning tool calls when a declared non-thinking run is unproven.

        A not-reported deployment relies on its configured off setting. Missing
        counts are never synthesized as zero; any reported count must still be 0.
        Other deployments retain strict telemetry. This never prompts an operator.
        """
        if not required and self.qwen_reasoning_mode != "off":
            return
        not_reported = self.reasoning_telemetry == 'not-reported' and self.qwen_reasoning_mode == 'off'
        correction = f'; configured off switch: {self.reasoning_off_switch}.'
        if not isinstance(usage, dict):
            if not_reported and usage is None:
                return
            raise ModelError(
                "Qwen non-thinking contract unverified: response omitted usage telemetry; "
                "no tools were released" + correction
            )
        details = usage.get("completion_tokens_details")
        if not_reported and ('completion_tokens_details' not in usage
                             or isinstance(details, dict) and 'reasoning_tokens' not in details):
            return
        reasoning_tokens = details.get("reasoning_tokens") if isinstance(details, dict) else None
        if isinstance(reasoning_tokens, bool) or not isinstance(reasoning_tokens, int):
            raise ModelError(
                "Qwen non-thinking contract unverified: response omitted integer "
                "completion_tokens_details.reasoning_tokens; no tools were released" + correction
            )
        if reasoning_tokens != 0:
            raise ModelError(
                f"Qwen non-thinking contract violated: model reported {reasoning_tokens} "
                "reasoning tokens; turn refused before tool execution" + correction
            )

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        key = self.api_key
        if self.credential_ref is not None:
            from aether.keys.provider_credentials import resolve_reference
            from urllib.parse import urlsplit
            route = urlsplit(self.endpoint)
            if route.scheme != 'https' and not (route.scheme == 'http' and route.hostname in ('127.0.0.1', '::1')):
                raise ModelError('Saved API keys require HTTPS, or loopback for local testing.')
            key = resolve_reference(self.credential_ref, bridge=self.credential_bridge)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        if self.credential_ref is None:
            headers.update(self.extra_headers)
        else:
            headers.update({k: v for k, v in self.extra_headers.items() if k.lower() != 'authorization'})
        return headers

    def probe(self, timeout_secs: float = 1.5) -> bool:
        """Check server reachability without consuming an inference slot."""
        req = urllib.request.Request(self.endpoint.rstrip("/") + "/models", method="GET",
                                     headers=self._headers())
        send = urllib.request.urlopen
        if self.credential_ref is not None:
            from aether.backends.deployment_probe import NoRedirect
            send = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open
        try:
            with send(req, timeout=timeout_secs) as resp:
                return 200 <= resp.status < 300
        except (OSError, urllib.error.URLError):
            return False

    @property
    def sent_temperature(self):
        """The temperature this client sends: none to hosts whose models refuse one (sends_temperature)."""
        return self.temperature if sends_temperature(self.endpoint) else None

    def request_body(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """Build the exact completion body, also inspectable by admission adapters."""
        body = {"model": self.model, "messages": messages,
                "temperature": self.sent_temperature, "stream": False,
                **self._body_extras(), **self.extra}
        if body["temperature"] is None:
            del body["temperature"]
        if tools:
            body["tools"] = tools
        return body

    def _rejected_completion(self, data, detail, *, diagnostic_sha=None):
        """Retain terminal usage before interpreting a prepared reply's envelope."""
        from aether.backends.inference_contract import ResponseRejected
        source = data if isinstance(data, dict) else {}
        choices = source.get('choices')
        choice = choices[0] if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict) else {}
        response = dict(_usage=source.get('usage'), _finish_reason=choice.get('finish_reason'), _model=source.get('model', self.model))
        if diagnostic_sha is not None:response['_aether_rejection_sha256'] = diagnostic_sha
        return ResponseRejected(detail, response,
            reported_model=source.get('model'), terminal_envelope=data)

    def _prepared_completion(self, data, terminal_sha, *, reasoning_required):
        from copy import deepcopy
        # Do not retain a second plaintext copy of unvalidated message/tool prose
        # in published telemetry. The rejection carries the detached envelope in memory.
        try:
            if not isinstance(data, dict) or data.get('error') is not None:
                raise ValueError('Invalid completion envelope')
            choices = data.get('choices')
            if (not isinstance(choices, list) or len(choices) != 1
                    or not isinstance(choices[0], dict) or not isinstance(choices[0].get('message'), dict)
                    or choices[0]['message'].get('role', 'assistant') != 'assistant'):
                raise ValueError('Expected one completion message')
            usage = deepcopy(data.get('usage'))
            self._verify_qwen_reasoning_contract(usage, required=reasoning_required)
            msg = normalize(deepcopy(choices[0]['message']), self.dialect)
        except (ValueError, TypeError, KeyError, IndexError, ModelError) as exc:
            detail = str(exc) if isinstance(exc, ModelError) else 'Malformed or refused completion; no calls released'
            raise self._rejected_completion(data, detail) from exc
        msg.update(_usage=usage, _finish_reason=choices[0].get('finish_reason'),
                   _model=data.get('model', self.model), _aether_reported_model=data.get('model'))
        msg.pop('_aether_terminal_sha256', None)
        if terminal_sha is not None:
            msg['_aether_terminal_sha256'] = terminal_sha
        return msg

    def chat(self, messages: list[dict], tools: list[dict] | None = None, *, expected_body_sha256: str | None = None, dispatch_attempt=None, configured_attempt=None, prepared_snapshot=None, deployment_binding=None, cancellation_token=None) -> dict:
        """One completions call. Returns the raw response message dict
        (choices[0].message) plus usage under '_usage', the finish reason
        under '_finish_reason', and the model the server actually reports
        having used under '_model' (never assume this equals self.model —
        that's the configured request, this is the telemetry, S00073.6/S00073.7
        ruling: telemetry-is-ground-truth extends to which model answered)."""
        if prepared_snapshot is not None:
            from aether.backends.inference_contract import PreparedRequestSnapshot, ContractError, Cancelled
            if type(prepared_snapshot) is not PreparedRequestSnapshot or messages or tools:
                raise ContractError('Prepared dispatch takes the snapshot only, not replacement messages/tools')
            prepared_snapshot.verify(self, deployment_binding)
            if dispatch_attempt is not None:
                raise ContractError('v3 deployment refuses deferred legacy owned dispatch; use configured ownership')
            if cancellation_token is not None and cancellation_token.is_set():
                raise Cancelled(dispatch_possible=False)
            encoded_body = prepared_snapshot.body_bytes
            body = json.loads(encoded_body)
            request_url = prepared_snapshot.request_url
            request_headers = dict(prepared_snapshot._headers)
        else:
            if deployment_binding is not None or cancellation_token is not None:
                from aether.backends.inference_contract import ContractError
                raise ContractError('Binding/cancellation arguments require a prepared snapshot')
            body = self.request_body(messages, tools)
            encoded_body = json.dumps(body).encode("utf-8")
        if expected_body_sha256 is not None:
            if self.qwen_reasoning_mode != "off" or body.get('reasoning_effort') != 'none':
                raise ModelError('admitted renderer requires explicit reasoning-off control and telemetry guard')
            if hashlib.sha256(encoded_body).hexdigest() != expected_body_sha256:
                raise ModelError('HTTP body changed after complete-body admission; no request sent')
        timeout = self.timeout_secs
        if configured_attempt is not None:
            from aether.runtime.orchestration.configured_ownership import ConfiguredRequestAttempt
            if type(configured_attempt) is not ConfiguredRequestAttempt or dispatch_attempt is not None:
                raise ModelError('explicit configured attempt required; ownership modes cannot combine')
            configured_attempt.check_transport(self)
        if dispatch_attempt is not None:
            from aether.runtime.orchestration.runtime_dispatch import DispatchAttempt
            from aether.runtime.orchestration.generation_dispatch import GenerationRequestAttempt
            if type(dispatch_attempt) not in (DispatchAttempt, GenerationRequestAttempt) or expected_body_sha256 is None \
                    or self.endpoint != 'http://127.0.0.1:1234/v1' or self.api_key or self.extra_headers:
                raise ModelError('owned dispatch requires fixed unauthenticated loopback routing')
            if type(timeout) not in (int,float) or not 0 < timeout <= 1200:
                raise ModelError('owned dispatch requires bounded HTTP timeout')
            if timeout != dispatch_attempt.http_timeout_seconds:
                raise ModelError('owned dispatch HTTP timeout changed')
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    raise urllib.error.HTTPError(req.full_url, code, 'redirect refused', headers, fp)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            send = opener.open
        elif configured_attempt is not None or prepared_snapshot is not None or self.credential_ref is not None:
            class NoConfiguredRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    raise urllib.error.HTTPError(req.full_url, code, 'redirect refused', headers, fp)
            send = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoConfiguredRedirect()).open
        else:
            send = urllib.request.urlopen
        if prepared_snapshot is None:
            request_url = self.endpoint.rstrip("/") + "/chat/completions"
            request_headers = self._headers()
        req = urllib.request.Request(
            request_url,
            data=encoded_body,
            headers=request_headers,
            method="POST",
        )
        if prepared_snapshot is not None:
            prepared_snapshot.verify(self, deployment_binding)
            if cancellation_token is not None and cancellation_token.is_set():
                raise Cancelled(dispatch_possible=False)
        if dispatch_attempt is not None:
            dispatch_attempt.arm(req)  # Durable ambiguity immediately before the single send.
        if configured_attempt is not None:
            configured_attempt.arm(req)
        normal_completion = configured_attempt is not None and bool(configured_attempt.owner.get('normal_policy_sha256'))
        terminal_sha = None
        try:
            with send(req, timeout=timeout) as resp:
                if normal_completion:
                    from aether.runtime.review_closure.native_normal_completion import MAX_RESPONSE, retain_terminal, terminal_document, attempt_tool_contract
                    raw = resp.read(MAX_RESPONSE+1)
                    # HTTPResponse.read(size) may return short bytes without
                    # raising IncompleteRead. A valid JSON prefix is not enough.
                    if getattr(resp, 'length', None) not in (None, 0):
                        raise ModelError('Incomplete declared HTTP response body; normal completion refused')
                    if prepared_snapshot is not None:
                        data = self._decode_prepared_completion(raw)
                        try:
                            terminal_document(raw, resp.status,
                                allow_tools=bool(configured_attempt.owner.get('desktop_admission_sha256')),
                                tool_contract=attempt_tool_contract(configured_attempt))
                        except (ValueError, TypeError, KeyError, IndexError) as exc:
                            from aether.runtime.review_closure.planning_tool_protocol import retain_rejection
                            diagnostic = (retain_rejection(configured_attempt, raw, resp.status, data)
                                if getattr(configured_attempt, 'planning_tool_contract_sha256', None) is not None else None)
                            raise self._rejected_completion(data, 'Normal completion validation refused; no calls released',diagnostic_sha=diagnostic) from exc
                    data, terminal_sha = retain_terminal(configured_attempt, raw, resp.status)
                else:
                    raw = resp.read()
                    data = (self._decode_prepared_completion(raw) if prepared_snapshot is not None
                            else json.loads(raw.decode("utf-8")))
        except urllib.error.HTTPError as e:
            if self.credential_ref is not None:
                e.close()
                raise ModelError(f'HTTP {e.code}: the saved-key deployment rejected the request.') from None
            if prepared_snapshot is not None and cancellation_token is not None and cancellation_token.is_set():
                raise Cancelled(dispatch_possible=True) from e
            body_text = ""
            try:
                body_text = e.read().decode("utf-8", errors="replace")[:500]
            except OSError:
                pass
            raise ModelError(f"HTTP {e.code} from {self.endpoint}: {body_text or e.reason}") from e
        except urllib.error.URLError as e:
            if self.credential_ref is not None:
                raise ModelError('The saved-key deployment could not be reached.') from None
            if prepared_snapshot is not None and cancellation_token is not None and cancellation_token.is_set():
                raise Cancelled(dispatch_possible=True) from e
            raise ModelError(f"model endpoint unreachable at {self.endpoint}: {e}") from e
        except OSError as e:
            if self.credential_ref is not None:
                raise ModelError('The saved-key deployment connection was interrupted.') from None
            # ARA-09 live evidence (2026-08-22): stopping LM Studio mid-request can raise
            # http.client.RemoteDisconnected (an OSError subclass) that urllib does not wrap
            # into URLError. The whole endpoint-outage family must surface as ModelError so
            # interruption classification stays honest.
            if prepared_snapshot is not None and cancellation_token is not None and cancellation_token.is_set():
                raise Cancelled(dispatch_possible=True) from e
            raise ModelError(f"model endpoint unreachable at {self.endpoint}: {e}") from e
        if self.credential_ref is not None:
            from aether.keys.redaction import redact
            data = redact(data)
        if prepared_snapshot is not None:
            return self._prepared_completion(data, terminal_sha, reasoning_required=expected_body_sha256 is not None)
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError) as e:
            raise ModelError(f"malformed completion response: {data}") from e
        usage = data.get("usage", {})
        self._verify_qwen_reasoning_contract(usage, required=expected_body_sha256 is not None)
        msg = normalize(msg, self.dialect)
        msg["_usage"] = usage
        msg["_finish_reason"] = data["choices"][0].get("finish_reason")
        msg["_model"] = data.get("model", self.model)
        # Reserved transport provenance is never accepted from model-controlled text.
        msg.pop('_aether_terminal_sha256', None)
        if terminal_sha is not None:
            msg['_aether_terminal_sha256'] = terminal_sha
        return msg

    def _decode_prepared_completion(self, raw):
        try:
            return json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeError) as exc:
            raise self._rejected_completion(None, 'Unparseable completion body; no calls released') from exc

    def warmup(self, retries: int = 2) -> bool:
        """S00061 cold-start doctrine: cheap probe before the seal gate so a cold
        server's first garbled reply doesn't fail the gate."""
        for _ in range(retries + 1):
            try:
                self.chat([{"role": "user", "content": "Reply with exactly: READY"}])
                return True
            except ModelError:
                continue
        return False


DEEPSEEK_ENDPOINT = "https://api.deepseek.com/v1"
# 2026-07-25 (model-name deprecation, live G4 attempt): deepseek-chat / deepseek-reasoner were
# retired by the vendor as of 2026-07-24 15:59 UTC. The API now hard-rejects both names with
# HTTP 400 ("The supported API model names are deepseek-v4-pro or deepseek-v4-flash"). G4 first
# ran and closed against deepseek-v4-pro (same day). Switched to deepseek-v4-flash immediately
# after, by explicit product decision: Flash is faster, and the working hypothesis is that
# reasoning models front-load deliberation the Aether-side architecture should instead be doing
# via stored, retrievable learning --
# so the non-reasoning model gets first crack at exercising that path for real, not the
# already-strong reasoning model.
# 2026-09-10: switched to V4.1-Flash only. Official API id: deepseek-flash.
# https://www.deepseek.com/en/news/deepseek-v4-1-flash/
DEEPSEEK_DEFAULT_MODEL = "deepseek-flash"


@dataclass(frozen=True)
class ModelCapability:
    """Dated vendor capability attached to a model name, not an endpoint."""

    context_window: int
    max_output_tokens: int
    source_url: str
    checked_on: str

    def __post_init__(self) -> None:
        if self.context_window <= 0 or self.max_output_tokens <= 0:
            raise ValueError("model capability limits must be positive")
        if self.max_output_tokens > self.context_window - DEFAULT_OUTPUT_RESERVE:
            raise ValueError("model max output must leave the required context reserve")

    @property
    def source(self) -> str:
        return f"{self.source_url} (checked {self.checked_on})"


# Vendor-verified 2026-08-05: DeepSeek's official model table lists both V4 Flash and V4 Pro at
# 1M context and 384K maximum output. Keep this dated source beside the runtime values so a stale
# compatibility default cannot silently become authoritative again.
DEEPSEEK_V4_CAPABILITY_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing/"
DEEPSEEK_V4_CAPABILITY_CHECKED_ON = "2026-08-05"
DEEPSEEK_V4_CAPABILITY = ModelCapability(
    context_window=1_000_000,
    max_output_tokens=384_000,
    source_url=DEEPSEEK_V4_CAPABILITY_SOURCE,
    checked_on=DEEPSEEK_V4_CAPABILITY_CHECKED_ON,
)
# Verified 2026-09-10 against the official model table: V4.1-Flash retains
# 1M context / 384K output. Keep the older dated records for explicit legacy callers.
DEEPSEEK_V41_FLASH_CAPABILITY = ModelCapability(
    context_window=1_000_000,
    max_output_tokens=384_000,
    source_url="https://api-docs.deepseek.com/quick_start/pricing/",
    checked_on="2026-09-10",
)
MODEL_CAPABILITIES = {
    "deepseek-flash": DEEPSEEK_V41_FLASH_CAPABILITY,
    "deepseek-v4-flash": DEEPSEEK_V4_CAPABILITY,
    "deepseek-v4-pro": DEEPSEEK_V4_CAPABILITY,
}


def capability_for_model(model: str) -> ModelCapability | None:
    """Return a verified capability by exact model id (case-insensitive)."""
    return MODEL_CAPABILITIES.get(model.strip().lower())


# Compatibility name retained for callers; its authority is the model registry above.
DEEPSEEK_DEFAULT_WINDOW = DEEPSEEK_V41_FLASH_CAPABILITY.context_window


# Hosts whose current models refuse a temperature below 1: Anthropic's OpenAI-compatible endpoint (Claude 4.7 and
# later) and OpenAI (its GPT-5-era reasoning models). Michel, 2026-11-07: "low temperature or no temperature, both
# are fine with me". Every other connection keeps the temperature it always sent.
NO_TEMPERATURE_HOSTS = ("api.anthropic.com", "api.openai.com")


def sends_temperature(endpoint: str) -> bool:
    from urllib.parse import urlparse
    try:
        host = (urlparse(endpoint or "").hostname or "").lower()
    except ValueError:
        return True
    return host not in NO_TEMPERATURE_HOSTS


def select_dialect(endpoint: str, requested: str = "auto") -> Dialect:
    """Resolve a canonical dialect from an operator choice and endpoint."""
    choice = requested.strip().lower()
    if choice == "openai":
        return OPENAI_ONLY
    if choice == "deepseek":
        return DEEPSEEK
    if choice != "auto":
        raise ValueError(
            f"unknown dialect '{requested}' (expected auto, openai, or deepseek)"
        )
    if endpoint.rstrip("/") == DEEPSEEK_ENDPOINT.rstrip("/"):
        return DEEPSEEK
    return OPENAI_ONLY


def deepseek_client(api_key: str, model: str = DEEPSEEK_DEFAULT_MODEL,
                     context_window: int | None = None,
                     timeout_secs: int = 300,
                     thinking: str | None = None) -> LMStudio:
    """Deepseek speaks the same OpenAI-compatible shape LMStudio already
    implements — this is a parameterized instance, not a new class. Kimi K3
    (planned follow-on, S00073.7 Sprint_Goals) should be a sibling factory
    function here, same pattern, once its endpoint/defaults are known."""
    if not api_key:
        raise ModelError("deepseek_client requires a non-empty api_key")
    capability = capability_for_model(model)
    if context_window is None:
        if capability is None:
            raise ModelError(
                f"no verified context window for model '{model}'; supply context_window explicitly"
            )
        context_window = capability.context_window
    capability_source = (
        capability.source if capability and context_window == capability.context_window
        else "operator-configured context_window"
    )
    return LMStudio(endpoint=DEEPSEEK_ENDPOINT, model=model, context_window=context_window,
                     timeout_secs=timeout_secs, api_key=api_key, dialect=DEEPSEEK,
                     capability_source=capability_source, thinking=thinking)
