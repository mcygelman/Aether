"""Explicit deployment budgets shared by settings, admission and dispatch adapters.

No tier lookup, endpoint discovery or implicit operational defaults. Resolved snapshots
are immutable. Callers count the complete request and use the same snapshot at each
checkpoint; this module does not claim that a character estimate is a tokenizer count.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json

from aether.integrity.context_atom_contract import BudgetLedger
from aether.integrity.strict_json import ContextConfigurationError  # defined in Integrity since 2026-10-08


def integer(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ContextConfigurationError(f"{label} must be an integer >= {minimum}")
    return value


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class ContextBudget:
    working_context_tokens: int
    tool_result_reserve_tokens: int
    safety_margin_tokens: int
    minimum_input_tokens: int
    max_tool_result_chars: int

    @classmethod
    def from_mapping(cls, raw: object) -> 'ContextBudget':
        keys = ('workingContextTokens', 'toolResultReserveTokens', 'safetyMarginTokens',
                'minimumInputTokens', 'maxToolResultChars')
        if not isinstance(raw, dict) or set(raw) != set(keys):
            raise ContextConfigurationError(f"contextBudget must contain exactly {', '.join(keys)}")
        for key in keys:
            integer(raw[key], key, minimum=1 if key in
                    ('workingContextTokens', 'minimumInputTokens', 'maxToolResultChars') else 0)
        return cls(*(raw[key] for key in keys))

    def to_mapping(self) -> dict:
        return dict(zip(('workingContextTokens', 'toolResultReserveTokens', 'safetyMarginTokens',
                         'minimumInputTokens', 'maxToolResultChars'), asdict(self).values()))

    def validate_envelope(self, context_window: int, max_output_tokens: int) -> None:
        # Also validates direct dataclass construction, not just JSON parsing.
        self.from_mapping(self.to_mapping())
        integer(context_window, 'contextWindow', minimum=1)
        integer(max_output_tokens, 'maxOutputTokens', minimum=1)
        if self.working_context_tokens > context_window:
            raise ContextConfigurationError(
                f"workingContextTokens={self.working_context_tokens} exceeds declared "
                f"contextWindow={context_window}; choose a supported working budget")
        available = (self.working_context_tokens - max_output_tokens
                     - self.tool_result_reserve_tokens - self.safety_margin_tokens)
        if available < self.minimum_input_tokens:
            raise ContextConfigurationError(
                f"Budget leaves {available} input tokens; minimumInputTokens requires "
                f"{self.minimum_input_tokens}. Increase a supported working budget or reduce reserves.")


@dataclass(frozen=True)
class ResolvedContext:
    profile_name: str
    profile_digest: str
    capability_source: str
    declared_context_tokens: int
    max_output_tokens: int
    budget: ContextBudget
    provenance: tuple[tuple[str, str], ...]
    verified_context_limit: int | None = None
    verified_output_limit: int | None = None
    verified_limit_source: str | None = None
    token_count_policy: object | None = None

    def ledger(self, *, bootstrap_cost_tokens: int = 0) -> BudgetLedger:
        """Bootstrap is only context absent from the subsequently selected/measured input.

        For a complete request preview leave bootstrap at zero to avoid counting
        identity/wrappers twice. Governor callers may account for excluded bootstrap here.
        minimumInputTokens validates the envelope; it is not an additional reserve.
        """
        integer(bootstrap_cost_tokens, 'bootstrap_cost_tokens')
        ledger = BudgetLedger(self.budget.working_context_tokens, bootstrap_cost_tokens,
                              self.max_output_tokens, self.budget.tool_result_reserve_tokens,
                              self.budget.safety_margin_tokens)
        if ledger.usable_input_budget < 0:
            raise ContextConfigurationError('Bootstrap and reserves exceed the working context')
        return ledger

    @property
    def revision(self) -> str:
        return digest(asdict(self))

    def preview(self, request_tokens: int, *, measurement: str) -> dict:
        integer(request_tokens, 'request_tokens')
        if measurement not in ('tokenizer', 'estimate'):
            raise ContextConfigurationError('measurement must be tokenizer or estimate')
        ledger = self.ledger()
        remaining = ledger.usable_input_budget - request_tokens
        return dict(profile=self.profile_name, revision=self.revision,
                    request_tokens=request_tokens, measurement=measurement,
                    fits=remaining >= 0, remaining_input_tokens=remaining,
                    required_context_tokens=self.budget.working_context_tokens - remaining,
                    budget=ledger.to_dict(), provenance=dict(self.provenance),
                    detail=('Request fits this configured envelope' if remaining >= 0 else
                            f'Request exceeds the working budget by {-remaining} tokens; '
                            'choose a supported larger budget, deployment, or narrower task'))


def resolve(profile, *, overrides: dict | None = None,
            verified_context_limit: int | None = None,
            verified_output_limit: int | None = None,
            verified_limit_source: str | None = None) -> ResolvedContext:
    """Resolve one explicit v2 profile plus visible per-run overrides, never environment.

    Existing launchers must opt into v2 consumption. No provider/network capability is
    inferred from a name or from this configuration. Verified observations are supplied
    by the adapter and retained separately from the profile's declaration.
    """
    profile.validate()
    from aether.config.token_count_policy import TokenCountPolicy,load_default
    policy=profile.token_count_policy or load_default()
    if profile.context_budget is None:
        raise ContextConfigurationError('Explicit contextBudget required; migrate this v1 profile first')
    values = {**profile.context_budget.to_mapping(), 'maxOutputTokens': profile.max_output_tokens}
    provenance = {key: f'profile:{profile.name}' for key in values}
    overrides = {} if overrides is None else overrides
    if not isinstance(overrides, dict) or set(overrides) - (set(values)|{'tokenCountPolicy'}):
        raise ContextConfigurationError('Unknown context override; use explicit budget fields only')
    for key, value in overrides.items():
        if key=='tokenCountPolicy':
            policy=TokenCountPolicy.from_mapping(value)
            continue
        values[key] = value
        provenance[key] = 'run override'
    output = integer(values.pop('maxOutputTokens'), 'maxOutputTokens', minimum=1)
    budget = ContextBudget.from_mapping(values)
    budget.validate_envelope(profile.context_window, output)
    if verified_limit_source is not None and (
            not isinstance(verified_limit_source, str) or not verified_limit_source.strip()):
        raise ContextConfigurationError('Verified limit source must be nonblank text when supplied')
    if (verified_context_limit is not None or verified_output_limit is not None) and verified_limit_source is None:
        raise ContextConfigurationError('Verified limits require a source')
    for name, limit, requested in (
        ('verified context limit', verified_context_limit, budget.working_context_tokens),
        ('verified output limit', verified_output_limit, output),
    ):
        if limit is not None:
            integer(limit, name, minimum=1)
            if requested > limit:
                raise ContextConfigurationError(f'Requested {requested} tokens exceeds {name} {limit}')
    provenance['tokenCountPolicy']=('run override' if 'tokenCountPolicy' in overrides else
        f'profile:{profile.name}' if profile.token_count_policy is not None else 'token_count_policy.defaults.json')
    return ResolvedContext(profile.name, digest(profile.to_mapping()), profile.capability_source,
                           profile.context_window, output, budget, tuple(sorted(provenance.items())),
                           verified_context_limit, verified_output_limit, verified_limit_source,policy)
