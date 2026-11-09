"""Retain observed usage even when a model call raises; never expose credentials."""
import time


class AccountedClient:
    def __init__(self, client):
        self.client = client
        self.cost = dict(model_calls=0, prompt_tokens=0, completion_tokens=0, failed_calls=0, seconds=0.0)

    def __getattr__(self, name):
        return getattr(self.client, name)

    @property
    def max_tokens(self):
        return getattr(self.client, 'max_tokens', None)

    @max_tokens.setter
    def max_tokens(self, value):
        # The shared loop temporarily adjusts this for each call, then restores it.
        # Forward writes as well as reads so the actual request uses that allowance.
        self.client.max_tokens = value

    def chat(self, messages, tools=None):
        self.cost['model_calls'] += 1
        started = time.perf_counter()
        try:
            reply = self.client.chat(messages, tools=tools)
        except Exception:
            self.cost['failed_calls'] += 1
            raise
        finally:
            self.cost['seconds'] += time.perf_counter() - started
        usage = reply.get('_usage') or {}
        self.cost['prompt_tokens'] += int(usage.get('prompt_tokens') or 0)
        self.cost['completion_tokens'] += int(usage.get('completion_tokens') or 0)
        return reply


def failure_cost(meter, *, nested=None, delegation=None):
    result = dict(meter.cost) if meter else {}
    for usage in (nested or {}, getattr(delegation, 'usage', {})):
        for key in ('model_calls', 'prompt_tokens', 'completion_tokens'):
            result[key] = result.get(key, 0) + int(usage.get(key, 0))
    if result.get('failed_calls'):
        result['usage_note'] = 'Failed-call usage may be unavailable; reported tokens are observed usage only.'
    return result
