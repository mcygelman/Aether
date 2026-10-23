"""Budget-aware usage diagnostics. Counts never establish content identity."""
from aether.paths import LEGACY_PACKAGE_DIR
from dataclasses import dataclass
import json
import math
from pathlib import Path
from aether.config.context_configuration import ContextConfigurationError,integer,digest

DEFAULT_PATH=(LEGACY_PACKAGE_DIR / 'token_count_policy.defaults.json')
KEYS=('nearLimitRemainingFraction','allowedUndercountTokens','allowedUndercountFraction')

@dataclass(frozen=True)
class TokenCountPolicy:
    near_limit_remaining_fraction: float
    allowed_undercount_tokens: int
    allowed_undercount_fraction: float

    def __post_init__(self):
        integer(self.allowed_undercount_tokens,'allowedUndercountTokens')
        for key,value in ((KEYS[0],self.near_limit_remaining_fraction),(KEYS[2],self.allowed_undercount_fraction)):
            if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=1:
                raise ContextConfigurationError(f'{key} must be a finite fraction from0to1')

    @classmethod
    def from_mapping(cls,raw):
        if type(raw) is not dict or set(raw)!=set(KEYS):
            raise ContextConfigurationError('tokenCountPolicy requires exactly '+', '.join(KEYS))
        return cls(*(raw[k] for k in KEYS))

    def to_mapping(self):
        return dict(zip(KEYS,(self.near_limit_remaining_fraction,self.allowed_undercount_tokens,self.allowed_undercount_fraction)))

    def assess(self,*,measured,reported,window,output,safety):
        for key,value in (('measured',measured),('output',output),('safety',safety)):integer(value,key)
        integer(window,'window',minimum=1)
        capacity=window-output-safety
        if capacity<0:raise ContextConfigurationError('reserves exceed context window')
        known=type(reported) is int and reported>=0
        headroom=capacity-max(measured,reported if known else measured)
        threshold=math.ceil(capacity*self.near_limit_remaining_fraction)
        tolerance=max(self.allowed_undercount_tokens,math.ceil(measured*self.allowed_undercount_fraction))
        near=headroom<=threshold
        delta=reported-measured if known else None
        if headroom<0:decision='stop_budget_exceeded'
        elif not known:decision='stop_usage_unknown_near_limit' if near else 'diagnostic_usage_unknown'
        elif delta<=0:decision='matched' if delta==0 else 'diagnostic_overcount'
        elif near and delta>tolerance:decision='stop_undercount_near_limit'
        elif near:decision='tolerated_undercount_near_limit'
        else:decision='diagnostic_undercount'
        return dict(schema='token-count-assessment-v1',policy=self.to_mapping(),policy_sha256=digest(self.to_mapping()),
            measured_prompt_tokens=measured,reported_prompt_tokens=reported if known else None,
            usage_status='reported' if known else 'unknown',provider_minus_measured_tokens=delta,
            usable_input_tokens=capacity,remaining_input_tokens=headroom,near_limit=near,
            near_limit_headroom_tokens=threshold,allowed_undercount_tokens=tolerance,
            decision=decision,allowed=not decision.startswith('stop_'),content_identity='not_inferred')

def load_default():
    # Same strict decoder as other human-editable settings, imported lazily.
    from aether.config.model_settings_store import parse_json
    return TokenCountPolicy.from_mapping(parse_json(DEFAULT_PATH.read_bytes()))

def assess_admission(admission,response):
    policy=TokenCountPolicy.from_mapping(admission['token_count_policy'])
    if digest(policy.to_mapping())!=admission.get('token_count_policy_sha256'):
        raise ValueError('admitted token-count policy changed')
    usage=response.get('_usage')
    reported=usage.get('prompt_tokens') if type(usage) is dict else None
    return policy.assess(measured=admission['prompt_tokens'],reported=reported,window=admission['context_window'],
        output=admission['output_tokens'],safety=admission['safety_tokens'])

def validate_admitted_policy(admission):
    policy=TokenCountPolicy.from_mapping(admission['token_count_policy'])
    if digest(policy.to_mapping())!=admission.get('token_count_policy_sha256'):
        raise ValueError('admitted token-count policy changed')
    return policy
