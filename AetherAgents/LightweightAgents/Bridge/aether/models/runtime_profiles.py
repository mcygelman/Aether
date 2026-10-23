"""Authoritative, human-editable runtime profiles for Aether model sessions.

ARA-05 separates a stable Aether launch role from the endpoint/model identifier that currently
serves it.  The registry is configuration, not capability inference: every envelope value is
explicit and malformed or unknown profiles fail before hydration or model work begins.
"""
from __future__ import annotations
from aether.paths import BRIDGE_ROOT

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from aether.config.context_configuration import ContextBudget, ContextConfigurationError, integer
from aether.config.token_count_policy import TokenCountPolicy

# Context kept free for the reply when a profile states no explicit budget. A profile rule,
# so it lives in the Model Catalog; Backends imports it from here.
DEFAULT_OUTPUT_RESERVE = 2_048


DEFAULT_REGISTRY_PATH = BRIDGE_ROOT / "runtime_profiles.json"
SUPPORTED_BACKENDS = ("lmstudio", "openai-compatible")
SUPPORTED_DIALECTS = ("auto", "openai", "deepseek")
SUPPORTED_IDENTIFIER_KINDS = ("logical-alias", "physical-provisional")
SUPPORTED_REASONING_MODES = ("off", "medium", "xhigh")


class RuntimeProfileError(ContextConfigurationError):
    """A registry or selected profile is absent, malformed, or unsafe to launch."""


@dataclass(frozen=True)
class RuntimeProfile:
    name: str
    description: str
    backend: str
    endpoint: str
    requested_api_identifier: str
    identifier_kind: str
    context_window: int
    max_output_tokens: int
    capability_source: str
    timeout_secs: int
    max_tool_calls: int
    dialect: str
    reasoning_mode: str | None = None
    context_budget: ContextBudget | None = None
    token_count_policy: TokenCountPolicy | None = None

    @classmethod
    def from_mapping(cls, name: str, value: object, *, schema_version: int = 1) -> "RuntimeProfile":
        if not isinstance(value, dict):
            raise RuntimeProfileError(f"runtime profile {name!r} must be an object")
        required = {
            "description", "backend", "endpoint", "requestedApiIdentifier",
            "identifierKind", "contextWindow", "maxOutputTokens", "capabilitySource",
            "timeoutSecs", "maxToolCalls", "dialect",
        }
        if schema_version == 2:
            required.add("contextBudget")
        elif schema_version != 1:
            raise RuntimeProfileError(f"unsupported runtime profile schemaVersion {schema_version!r}")
        missing = sorted(required - value.keys())
        optional = {"reasoningMode"}
        if schema_version==2:optional.add('tokenCountPolicy')
        extra = sorted(value.keys() - required - optional)
        if missing:
            raise RuntimeProfileError(
                f"runtime profile {name!r} is missing: {', '.join(missing)}"
            )
        if extra:
            raise RuntimeProfileError(
                f"runtime profile {name!r} has unknown fields: {', '.join(extra)}"
            )
        try:
            if schema_version == 2:
                for key in ('description', 'backend', 'endpoint', 'requestedApiIdentifier',
                            'identifierKind', 'capabilitySource', 'dialect'):
                    if not isinstance(value[key], str) or not value[key].strip():
                        raise ContextConfigurationError(f"{key} must be nonblank text")
            profile = cls(
                name=name,
                description=str(value["description"]).strip(),
                backend=str(value["backend"]).strip(),
                endpoint=str(value["endpoint"]).strip(),
                requested_api_identifier=str(value["requestedApiIdentifier"]).strip(),
                identifier_kind=str(value["identifierKind"]).strip(),
                context_window=integer(value["contextWindow"], "contextWindow", minimum=1),
                max_output_tokens=integer(value["maxOutputTokens"], "maxOutputTokens", minimum=1),
                capability_source=str(value["capabilitySource"]).strip(),
                timeout_secs=integer(value["timeoutSecs"], "timeoutSecs", minimum=1),
                max_tool_calls=integer(value["maxToolCalls"], "maxToolCalls", minimum=1),
                dialect=str(value["dialect"]).strip(),
                reasoning_mode=(
                    str(value["reasoningMode"]).strip()
                    if value.get("reasoningMode") is not None else None
                ),
                context_budget=(ContextBudget.from_mapping(value["contextBudget"])
                                if schema_version == 2 else None),
                token_count_policy=(TokenCountPolicy.from_mapping(value['tokenCountPolicy']) if 'tokenCountPolicy' in value else None),
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeProfileError(
                f"runtime profile {name!r} is invalid: {exc}"
            ) from exc
        profile.validate()
        return profile

    def validate(self) -> None:
        if self.token_count_policy is not None:
            if type(self.token_count_policy) is not TokenCountPolicy or self.context_budget is None:
                raise RuntimeProfileError('typed tokenCountPolicy requires an advanced budget profile')
            TokenCountPolicy.from_mapping(self.token_count_policy.to_mapping())
        if not isinstance(self.name, str) or not self.name.strip():
            raise RuntimeProfileError("runtime profile name must not be blank")
        for label, value in (
            ("description", self.description),
            ("requestedApiIdentifier", self.requested_api_identifier),
            ("capabilitySource", self.capability_source),
        ):
            if not isinstance(value, str) or not value.strip():
                raise RuntimeProfileError(f"runtime profile {self.name!r} has blank {label}")
        if self.backend not in SUPPORTED_BACKENDS:
            raise RuntimeProfileError(
                f"runtime profile {self.name!r} has unsupported backend {self.backend!r}"
            )
        parsed = urlparse(self.endpoint)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise RuntimeProfileError(
                f"runtime profile {self.name!r} endpoint must be an absolute HTTP(S) URL"
            )
        if self.identifier_kind not in SUPPORTED_IDENTIFIER_KINDS:
            raise RuntimeProfileError(
                f"runtime profile {self.name!r} has unsupported identifierKind "
                f"{self.identifier_kind!r}"
            )
        if self.dialect not in SUPPORTED_DIALECTS:
            raise RuntimeProfileError(
                f"runtime profile {self.name!r} has unsupported dialect {self.dialect!r}"
            )
        if self.reasoning_mode is not None and self.reasoning_mode not in SUPPORTED_REASONING_MODES:
            raise RuntimeProfileError(
                f"runtime profile {self.name!r} has unsupported reasoningMode "
                f"{self.reasoning_mode!r}; low is intentionally prohibited"
            )
        for label, value in (
            ("contextWindow", self.context_window),
            ("maxOutputTokens", self.max_output_tokens),
            ("timeoutSecs", self.timeout_secs),
            ("maxToolCalls", self.max_tool_calls),
        ):
            if type(value) is not int or value <= 0:
                raise RuntimeProfileError(
                    f"runtime profile {self.name!r} {label} must be greater than zero"
                )
        if self.context_budget is not None:
            try:
                self.context_budget.validate_envelope(self.context_window, self.max_output_tokens)
            except ContextConfigurationError as exc:
                raise RuntimeProfileError(f"runtime profile {self.name!r}: {exc}") from exc
        elif self.max_output_tokens > self.context_window - DEFAULT_OUTPUT_RESERVE:
            raise RuntimeProfileError(
                f"runtime profile {self.name!r} maxOutputTokens must leave "
                f"{DEFAULT_OUTPUT_RESERVE} tokens of context reserve"
            )

    def to_mapping(self) -> dict:
        value = dict(description=self.description, backend=self.backend, endpoint=self.endpoint,
                     requestedApiIdentifier=self.requested_api_identifier,
                     identifierKind=self.identifier_kind, contextWindow=self.context_window,
                     maxOutputTokens=self.max_output_tokens, capabilitySource=self.capability_source,
                     timeoutSecs=self.timeout_secs, maxToolCalls=self.max_tool_calls, dialect=self.dialect)
        if self.reasoning_mode is not None:
            value['reasoningMode'] = self.reasoning_mode
        if self.context_budget is not None:
            value['contextBudget'] = self.context_budget.to_mapping()
        if self.token_count_policy is not None:value['tokenCountPolicy']=self.token_count_policy.to_mapping()
        return value


def load_registry(path: str | Path = DEFAULT_REGISTRY_PATH, *,
                  budget_aware: bool = False) -> dict[str, RuntimeProfile]:
    registry_path = Path(path)
    try:
        raw = json.loads(registry_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeProfileError(f"runtime profile registry not found: {registry_path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeProfileError(
            f"runtime profile registry could not be read: {registry_path}: {exc}"
        ) from exc
    return validate_registry(raw, budget_aware=budget_aware)


def validate_registry(raw: object, *, budget_aware: bool = False,
                      deployment_aware: bool = False) -> dict[str, RuntimeProfile]:
    """Shared settings/JSON validator. Legacy consumers cannot silently ignore v2 budgets."""
    if isinstance(raw, dict) and raw.get('schemaVersion') == 3:
        if not deployment_aware:
            raise RuntimeProfileError('Runtime profile schemaVersion 3 requires a deployment-aware consumer')
        from aether.models.deployment_profiles import validate_deployments
        return validate_deployments(raw)
    if not isinstance(raw, dict) or set(raw) != {"schemaVersion", "profiles"}:
        raise RuntimeProfileError(
            "runtime profile registry must contain exactly schemaVersion and profiles"
        )
    if type(raw['schemaVersion']) is not int or raw["schemaVersion"] not in (1, 2):
        raise RuntimeProfileError(
            f"unsupported runtime profile schemaVersion {raw['schemaVersion']!r}"
        )
    if raw['schemaVersion'] == 2 and not budget_aware:
        raise RuntimeProfileError(
            'Runtime profile schemaVersion 2 requires a budget-aware consumer; '
            'this launcher has not integrated explicit context settings. Keep its v1 profile '
            'until the runner migration is complete.'
        )
    if not isinstance(raw["profiles"], dict) or not raw["profiles"]:
        raise RuntimeProfileError("runtime profile registry profiles must be a non-empty object")
    profiles = {
        name: RuntimeProfile.from_mapping(name, value, schema_version=raw['schemaVersion'])
        for name, value in raw["profiles"].items()
    }
    requested: dict[str, str] = {}
    for profile in profiles.values():
        if profile.identifier_kind != "logical-alias":
            continue
        key = profile.requested_api_identifier.casefold()
        if key in requested:
            raise RuntimeProfileError(
                "duplicate logical API identifier "
                f"{profile.requested_api_identifier!r} in profiles "
                f"{requested[key]!r} and {profile.name!r}"
            )
        requested[key] = profile.name
    return profiles


def load(name: str, path: str | Path = DEFAULT_REGISTRY_PATH, *,
         budget_aware: bool = False) -> RuntimeProfile:
    profiles = load_registry(path, budget_aware=budget_aware)
    try:
        return profiles[name]
    except KeyError as exc:
        choices = ", ".join(sorted(profiles))
        raise RuntimeProfileError(
            f"unknown runtime profile {name!r} (available: {choices})"
        ) from exc


def migrate_registry(raw: object, budgets: dict[str, dict]) -> dict:
    """Return a v2 candidate without saving it or inventing tier-based budget values."""
    profiles = validate_registry(raw)
    if not isinstance(budgets, dict) or set(budgets) != set(profiles):
        raise RuntimeProfileError('Migration requires explicit budgets for exactly the existing profiles')
    candidate = dict(schemaVersion=2, profiles={
        name: {**deepcopy(raw['profiles'][name]), 'contextBudget': deepcopy(budgets[name])}
        for name in profiles
    })
    validate_registry(candidate, budget_aware=True)
    return candidate
