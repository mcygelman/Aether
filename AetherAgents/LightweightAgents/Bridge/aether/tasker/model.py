"""The model side of a Tasker Process turn, without the atelier's room.

Copied (the minimum, behaviour unchanged) from aether/atelier/turn.py, because importing anything under
aether.atelier loads its whole package, facts, daybook and postmaster included, and the Tasker Process must not
depend on them (contract §1, §7). turn.py stays as it is. Both turn runners (staff here, Overseer in aether/overseer)
use this module:

    Deployment                    a profile from runtime_profiles.json → the production model client
    context guard                 context_room, request_estimate, make_room, ContextCapacityError
    test_command / test_environment   how run_tests runs a project's tests, with no credential in the child
    tool_loop(client, messages, schemas, call_tool, ...)   tool rounds, then the mind's final words

Never: read a mind's words to decide anything, retry a model call (the Timer owns retries), or pass the server's
environment to a child process.
"""
from __future__ import annotations

import importlib.util
import hashlib
import json
import math
import os
import sys
import time
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import meter
from .recovery import outcome
from .closure_recovery import outcome as closure_outcome

MAX_TOOL_ROUNDS = 40
MAX_TOOL_RESULT = 24_000
CONTEXT_MARGIN = 0.20
SET_ASIDE_KEEP = 300
OUTPUT_FLOOR = 8192          # reply room every request keeps; the rest of the ceiling is granted per call
ROOM_NOTE = ('[Room for your reply in this call: about {n} tokens, because your context is filling. Keep this reply '
             'short; split a large file into several smaller files.]')
BAD_JSON = ('not run: the arguments you sent for {name} are not valid JSON ({why}, at character {at} of {size}), so '
            'nothing ran. Send the call again with its arguments as one JSON object; every bracket and brace closes '
            'exactly once. The end of what you sent: …{tail}')
CUT = ('Your last reply was cut off at the output limit before it finished, so nothing in it was carried out (no '
       'tool call ran). Continue in smaller steps: keep each reply short, and split a large file into several '
       'smaller files, one write_file each (write_file replaces a file, so one file cannot be written in parts).')


class SetupError(RuntimeError):
    """The turn could not start: no deployment, no credential, a placeholder model. No mind can fix it; the Timer
    sends it to the operator line."""


FALLBACK_MAX_TOKENS = 4096
# Michel, 10-30 night (S1): a hosted model's reply is budgeted at 64K, declared and shown at setup. The largest reply
# seen live was 12.7K; a runaway now stops at 64K. The registry still says what the model can do; a profile's own
# maxOutputTokens still wins. Local models keep their registry/profile/fallback limit and the per-call reply room.
HOSTED_REPLY_BUDGET = 65_536
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0")


def _hosted(endpoint: str) -> bool:
    from urllib.parse import urlsplit
    try:
        host = (urlsplit(endpoint or "").hostname or "").lower()
    except ValueError:
        return False
    return bool(host) and host not in LOCAL_HOSTS and not host.endswith(".localhost")


def limit_sources(model: str, profile: dict) -> dict:
    """A deployment's output ceiling and context window, and where each comes from. The profile's own values win
    ('profile'); otherwise the model registry's dated, verified limits ('registry',
    aether.backends.model.MODEL_CAPABILITIES); otherwise 4096 output ('fallback'), which is not the model's
    capability. M7 (10-30) ran deepseek-v4-flash at a hand-written 8192 against a verified 384K and cut a reply."""
    explicit = profile.get('maxOutputTokens') or profile.get('max_tokens')
    window = profile.get('contextWindow')
    try:
        from aether.backends.model import capability_for_model
        known = capability_for_model(model) if model else None
    except Exception:
        known = None
    if explicit:
        max_tokens, source = int(explicit), 'profile'
    elif known and _hosted(profile.get('endpoint', '')) and known.max_output_tokens > HOSTED_REPLY_BUDGET:
        max_tokens, source = HOSTED_REPLY_BUDGET, 'budget'
    elif known:
        max_tokens, source = known.max_output_tokens, 'registry'
    else:
        max_tokens, source = FALLBACK_MAX_TOKENS, 'fallback'
    return dict(
        model=model,
        max_tokens=max_tokens,
        max_tokens_source=source,
        context_window=int(window) if window else (known.context_window if known else None),
        window_source='profile' if window else ('registry' if known else 'unknown'),
        registry_max_tokens=known.max_output_tokens if known else None,
        registry_checked=known.checked_on if known else None)


def output_limits(model: str, profile: dict) -> tuple:
    """(max output tokens, context window) for a deployment; see limit_sources."""
    s = limit_sources(model, profile)
    return s['max_tokens'], s['context_window']


def limit_warnings(s: dict) -> list:
    """What the operator should see at setup: a fallback presented as a limit, or a profile below the registry."""
    out = []
    if s['max_tokens_source'] == 'fallback':
        out.append(f"{s['model'] or '(no model)'}: not in the model registry, so replies are limited to "
                   f"{FALLBACK_MAX_TOKENS} tokens. That is a fallback, not the model's capability; set maxOutputTokens "
                   f"in the profile if the model can give more.")
    if (s['max_tokens_source'] == 'profile' and s['registry_max_tokens']
            and s['max_tokens'] < s['registry_max_tokens']):
        out.append(f"{s['model']}: the profile limits replies to {s['max_tokens']} tokens; the registry says "
                   f"{s['registry_max_tokens']} (checked {s['registry_checked']}). Remove maxOutputTokens unless the "
                   f"lower limit is deliberate.")
    if s['window_source'] == 'unknown':
        out.append(f"{s['model'] or '(no model)'}: no context window known; the context guard is off for it.")
    return out


def describe_deployment(profiles_path, name: str) -> dict:
    """limit_sources and limit_warnings for a named deployment, read without resolving any credential."""
    raw = json.loads(Path(profiles_path).read_text(encoding='utf-8'))
    profiles = raw.get('profiles') or raw.get('document', {}).get('profiles') or {}
    if name not in profiles:
        raise SetupError(f'no deployment named {name!r} in {profiles_path}')
    p = profiles[name]
    s = limit_sources(p.get('requestedApiIdentifier') or p.get('model') or '', p)
    return dict(s, warnings=limit_warnings(s))


@dataclass
class Deployment:
    model: str
    endpoint: str
    api_key: str = field(default='', repr=False)
    max_tokens: int = 4096
    timeout_secs: int = 600
    temperature: float = 0.2
    dialect: str = 'auto'
    context_window: Optional[int] = None
    thinking: Optional[str] = None
    credential_ref: Optional[dict] = field(default=None, repr=False)
    credential_bridge: Optional[Path] = field(default=None, repr=False)
    limits: Optional[dict] = field(default=None, repr=False)

    @classmethod
    def load(cls, profiles_path: Path, name: str, *, bridge: Optional[Path] = None) -> 'Deployment':
        raw = json.loads(Path(profiles_path).read_text(encoding='utf-8'))
        profiles = raw.get('profiles') or raw.get('document', {}).get('profiles') or {}
        if name not in profiles:
            raise SetupError(f'no deployment named {name!r} in {profiles_path}')
        return cls.from_profile(profiles[name], bridge=bridge)

    @classmethod
    def from_profile(cls, p: dict, *, bridge: Optional[Path] = None) -> 'Deployment':
        key = ''
        reference = p.get('credential_ref')
        vault_reference = isinstance(reference, dict) and reference.get('kind') == 'vault'
        if reference and not vault_reference:
            from aether.keys.provider_credentials import resolve_reference
            key = resolve_reference(p['credential_ref'], bridge=bridge)
        model = p.get('requestedApiIdentifier') or p.get('model') or ''
        limits = limit_sources(model, p)
        max_tokens, window = limits['max_tokens'], limits['context_window']
        return cls(model=model, endpoint=p['endpoint'], api_key=key, max_tokens=max_tokens, limits=limits,
                   timeout_secs=int(p.get('timeoutSeconds') or p.get('timeoutSecs') or 600),
                   temperature=float(p.get('temperature', 0.2)), dialect=p.get('dialect') or 'auto',
                   context_window=window, thinking=p.get('thinking'),
                   credential_ref=deepcopy(reference) if vault_reference else None, credential_bridge=bridge)

    def client(self):
        from aether.backends.model import LMStudio, select_dialect
        if not self.model or self.model == 'not-configured':
            raise SetupError('the deployment names no model')
        kwargs = dict(endpoint=self.endpoint, model=self.model, timeout_secs=self.timeout_secs, api_key=self.api_key,
                      max_tokens=self.max_tokens, dialect=select_dialect(self.endpoint, self.dialect or 'auto'))
        if self.context_window:
            kwargs['context_window'] = int(self.context_window)
        if self.thinking:
            kwargs['thinking'] = self.thinking
        if self.credential_ref:
            kwargs.update(credential_ref=self.credential_ref, credential_bridge=self.credential_bridge)
        try:
            c = LMStudio(**kwargs)
        except TypeError:
            if self.credential_ref:
                raise
            c = LMStudio(endpoint=self.endpoint, model=self.model, timeout_secs=self.timeout_secs, api_key=self.api_key)
        try:
            c.temperature = self.temperature
        except Exception:
            pass
        try:
            c.aether_limits = dict(self.limits or {})   # where the ceiling and window came from, for the turn record
        except Exception:
            pass
        return c


# ---- the context guard ---------------------------------------------------------------------------------------------

def _tokens(value) -> int:
    if not value:
        return 0
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    return len(value) // 4 + 1


def context_room(client) -> Optional[dict]:
    """How much input a request may carry. Only a floor of reply room is reserved (OUTPUT_FLOOR, or the ceiling if
    smaller); the rest of the output ceiling is granted per call from what the input leaves (output_allowance).
    Reserving the whole ceiling on every call would shrink input for a one-line reply (Praxis, 10-30)."""
    window = int(getattr(client, 'context_window', 0) or 0)
    if window <= 0:
        return None
    ceiling = int(getattr(client, 'max_tokens', 0) or 0) or FALLBACK_MAX_TOKENS
    reserve, margin = min(ceiling, OUTPUT_FLOOR), int(window * CONTEXT_MARGIN)
    return dict(window=window, ceiling=ceiling, reserve=reserve, margin=margin,
                room=max(0, window - reserve - margin))


def output_allowance(guard: Optional[dict], estimated_input: int) -> Optional[int]:
    """This call's reply room: min(output ceiling, window - estimated input - margin). None when no window is known.
    The input estimate is about four characters per token, not a tokenizer count."""
    if not guard:
        return None
    return max(0, min(guard['ceiling'], guard['window'] - estimated_input - guard['margin']))


def request_estimate(messages: list, schemas, known: Optional[tuple] = None) -> int:
    raw = sum(_tokens(m.get('content')) + _tokens(m.get('tool_calls')) + 4 for m in messages) + _tokens(schemas)
    if known and len(known) == 3:
        return math.ceil(raw * max(1.0, known[1] / max(1, known[2])))
    if known and known[0] <= len(messages):
        return int(known[1]) + sum(_tokens(m.get('content')) + _tokens(m.get('tool_calls')) + 4 for m in messages[known[0]:])
    return raw


def make_room(messages: list, room: int, schemas, known, *, final: bool = False) -> list:
    """Make the next request fit by setting aside or cutting tool results only (never the identity or the wake)."""
    last_round = max((i for i, m in enumerate(messages) if m.get('role') == 'assistant' and m.get('tool_calls')), default=-1)
    done = []

    def over():
        return request_estimate(messages, schemas, known) - room

    def candidates(newest: bool):
        return [(len(m.get('content') or ''), i) for i, m in enumerate(messages)
                if m.get('role') == 'tool' and not m.get('_aside') and len(m.get('content') or '') > SET_ASIDE_KEEP + 120
                and ((i > last_round) if newest else (i < last_round))]

    def set_aside(i):
        size = messages[i].get('_size') or len(messages[i]['content'])
        messages[i] = dict(messages[i], _aside=True, _size=size,
                           content=f'[{size} characters set aside to make room in your context; re-read it if you '
                                   f'must. It began:]\n{messages[i]["content"][:SET_ASIDE_KEEP]}')
        done.append(dict(kind='set_aside', tool_call_id=messages[i].get('tool_call_id', ''), characters=size))

    while over() > 0 and candidates(False):
        set_aside(max(candidates(False))[1])
    while over() > 0 and candidates(True):
        size, i = max(candidates(True))
        if final:
            set_aside(i)
            continue
        whole = messages[i].get('_size') or size
        keep = max(SET_ASIDE_KEEP, size - (over() + 40) * 4)
        messages[i] = dict(messages[i], _size=whole, content=messages[i]['content'][:keep] +
                           f'\n[cut here at {keep} of {whole} characters to fit your context]')
        done.append(dict(kind='cut', tool_call_id=messages[i].get('tool_call_id', ''), characters=whole, kept=keep))
    return done


def _outbound(messages: list) -> list:
    return [{k: v for k, v in m.items() if not k.startswith('_')} for m in messages]


class ContextCapacityError(RuntimeError):
    """The final request would not fit; nothing oversized was sent."""

    def __init__(self, message, cost, transcript):
        super().__init__(message)
        self.cost, self.transcript = cost, transcript


# ---- tests in a child process --------------------------------------------------------------------------------------

UNITTEST = ("import sys, unittest; base, start, pattern = sys.argv[1:4]; sys.path.insert(0, base); "
            "unittest.main(module=None, argv=['unittest', 'discover', '-v', '-s', start, '-t', start, '-p', pattern])")
TEST_ENVIRONMENT = ('SYSTEMROOT', 'WINDIR', 'COMSPEC', 'SYSTEMDRIVE', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'USERPROFILE',
                    'HOMEDRIVE', 'HOMEPATH', 'APPDATA', 'LOCALAPPDATA', 'PYTHONUTF8', 'PYTHONIOENCODING')


def test_command(target: Path, root: Path) -> tuple:
    """From the project folder that holds the tests; pytest when this runtime has it, unittest otherwise."""
    root = Path(root).resolve()
    base = target if target.is_dir() else target.parent
    while base != root and base.name.lower().startswith('test'):
        base = base.parent
    if importlib.util.find_spec('pytest') is not None:
        return [sys.executable, '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--rootdir', str(base),
                str(target)], base, 'pytest'
    start, pattern = (str(target), 'test*.py') if target.is_dir() else (str(target.parent), target.name)
    return [sys.executable, '-B', '-c', UNITTEST, str(base), start, pattern], base, 'unittest'


def test_environment(environ=None) -> dict:
    """The operating system's bootstrap variables only: tests run code a model wrote, so no credential goes along."""
    environ = os.environ if environ is None else environ
    return {k: v for k, v in environ.items() if k.upper() in TEST_ENVIRONMENT}


# ---- the tool loop --------------------------------------------------------------------------------------------------

def _as_given(reply: dict, cut: bool = False) -> dict:
    """A reply for the transcript, as the model gave it: content, tool calls, reasoning and why it stopped."""
    kept = {k: deepcopy(v) for k, v in reply.items() if not k.startswith('_')}
    kept['role'] = 'assistant'
    kept['finish_reason'] = reply.get('_finish_reason')
    if cut:
        kept['cut'] = True
    return kept


class TurnLoopError(RuntimeError):
    """A failed turn with its observed evidence; the original exception remains the cause."""
    def __init__(self,error,cost,uncertain=False):
        super().__init__(f'{type(error).__name__}: {error}')
        self.cost,self.uncertain=cost,uncertain


def tool_loop(client, messages, schemas, call_tool, *, checkpoint=None, **options):
    progress={}
    def record(cost,phase,operation=None):
        progress.update(cost=cost,phase=phase)
        if checkpoint is not None:checkpoint(cost,phase,operation)
    try:
        return _tool_loop(client,messages,schemas,call_tool,record=record,**options)
    except ContextCapacityError as exc:
        exc.cost=progress.get('cost',exc.cost)
        raise
    except Exception as exc:
        cost=progress.get('cost',{})
        if progress.get('phase')=='model':
            cost['failed_calls']=cost.get('failed_calls',0)+1
            cost['usage_note']='Failed-call usage may be unavailable; retained tokens are observed usage.'
        raise TurnLoopError(exc,cost,progress.get('phase')=='tool') from exc


def _tool_loop(client, messages: list, schemas: list, call_tool: Callable[[str, dict], str], *,
              max_rounds: int = MAX_TOOL_ROUNDS, stopped: Optional[Callable[[], bool]] = None,
              ending: str = 'Give your final words now, without more tool calls.', delegation=None,
              argument_recovery=None, on_argument_event=None, record=None) -> tuple:
    """Tool rounds, then the mind's final words. Returns (text, cost, calls) where calls lists each tool call as
    {name, arguments, result} (the result shortened) for the turn record. cost['transcript'] is the whole
    conversation, every reply as the model gave it (a cut reply too, marked cut), for the Timer to save beside the
    turn record; cost['stopped'] says a stop ended the turn between two calls."""
    started = time.perf_counter()
    count = dict(calls=0, prompt=0, completion=0, seconds=0.0, stopped=False)
    guard = context_room(client)
    context = dict(window=guard['window'] if guard else None, room=guard['room'] if guard else None,
                   peak_estimate=0, made_room=[])
    known = None
    transcript = deepcopy(messages)
    log = []
    trace = []      # one row per model call: why it stopped, what it cost, how many tool calls it asked for
    noted_room = False
    known_tools = {s.get('function', {}).get('name') for s in schemas}
    json_halted = lambda: bool(argument_recovery and argument_recovery.violations)

    def account():
        return dict(model_calls=count['calls'], prompt_tokens=count['prompt'], completion_tokens=count['completion'],
                    seconds=round(count['seconds'], 1), turn_seconds=round(time.perf_counter() - started, 1),
                    context=context, ultralights=deepcopy(delegation.receipts) if delegation else [],
                    calls=deepcopy(trace), max_tokens=getattr(client, 'max_tokens', None),
                    limits=deepcopy(getattr(client, 'aether_limits', None)), stopped=count['stopped'],
                    transcript=deepcopy(transcript), tools=deepcopy(log))

    def call(with_tools):
        nonlocal known, noted_room
        selected = schemas if with_tools else None
        estimated = request_estimate(messages, selected, known)
        context['peak_estimate'] = max(context['peak_estimate'], estimated)
        if guard and estimated > guard['room']:
            context['made_room'] += make_room(messages, guard['room'], selected, known, final=not with_tools)
            estimated = request_estimate(messages, selected, known)
            if estimated > guard['room']:
                context['unsent_estimate'] = estimated
                return None
        allowance = output_allowance(guard, estimated)
        if allowance is not None and allowance < min(guard['ceiling'], 2 * OUTPUT_FLOOR) and not noted_room:
            noted_room = True                       # said once per turn, before a large reply is attempted
            note = dict(role='user', content=ROOM_NOTE.format(n=allowance))
            messages.append(note)
            transcript.append(deepcopy(note))
        raw = request_estimate(messages, selected)
        ceiling = getattr(client, 'max_tokens', None)
        per_call = allowance is not None and bool(ceiling) and allowance < ceiling
        t0 = time.perf_counter()
        record(account(),'model')
        if per_call:
            client.max_tokens = allowance
        try:
            reply = client.chat(_outbound(messages), tools=selected)
        finally:
            if per_call:
                client.max_tokens = ceiling
        count['calls'] += 1
        count['seconds'] += time.perf_counter() - t0
        usage = reply.get('_usage') or {}
        meter.report(usage.get('prompt_tokens'), usage.get('completion_tokens'))
        if usage.get('prompt_tokens'):
            ratio = max(1.0, known[1] / max(1, known[2])) if known else 1.0
            known = (len(messages), max(int(usage['prompt_tokens']), math.ceil(raw * ratio)), raw)
        count['prompt'] += int(usage.get('prompt_tokens') or 0)
        count['completion'] += int(usage.get('completion_tokens') or 0)
        trace.append(dict(finish_reason=reply.get('_finish_reason'), prompt_tokens=usage.get('prompt_tokens'),
                          completion_tokens=usage.get('completion_tokens'),
                          tool_calls=len(reply.get('tool_calls') or []), input_estimate=estimated,
                          output_allowance=allowance))
        record(account(),'between')
        return reply

    text, why = None, 'You have used the tool rounds for this turn. '
    for _ in range(max_rounds):
        if json_halted() or (stopped and stopped()):
            why, count['stopped'] = 'The sprint is stopping. ', True
            break
        reply = call(True)
        if reply is None:
            why = 'Your context is nearly full. '
            break
        if reply.get('_finish_reason') == 'length':
            # Cut at the output limit: a half-written tool call carries nothing out. Say so and go on (seen live in
            # M7: a whole file in one write_file, cut at 8192 tokens, ended two turns silently).
            log.append(dict(name='(reply cut)', arguments={}, result='cut at the output limit; nothing carried out'))
            transcript.append(_as_given(reply, cut=True))   # never sent back to the model, always kept (round 2)
            note = dict(role='user', content=CUT)
            messages.append(note)
            transcript.append(deepcopy(note))
            continue
        tool_calls = reply.get('tool_calls') or []
        message = {k: v for k, v in reply.items() if k in ('content', 'tool_calls')}
        message['role'] = 'assistant'
        messages.append(message)
        transcript.append(_as_given(reply))
        if not tool_calls:
            text = reply.get('content') or ''
            break
        for tc in tool_calls:
            if json_halted() or (stopped and stopped()):
                why, count['stopped'] = 'The sprint is stopping. ', True
                break
            fn = tc.get('function') or {}
            raw = fn.get('arguments', '{}')
            try:
                args, broken = json.loads(raw), None
            except (ValueError, TypeError) as exc:
                args, broken = {}, exc
            if broken is None and not isinstance(args, dict):
                args, broken = {}, ValueError('the arguments are not one JSON object')
            raw_text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
            argument_event = None
            if argument_recovery is not None:
                argument_event = argument_recovery.step(fn.get('name', ''), dict(valid=broken is None,
                    known=fn.get('name') in known_tools, sha256=hashlib.sha256(raw_text.encode('utf-8')).hexdigest(),
                    size=len(raw_text), error=getattr(broken, 'msg', str(broken)) if broken is not None else None,
                    position=getattr(broken, 'pos', None)))
                if argument_event is not None and on_argument_event is not None:
                    on_argument_event(argument_event)
            before = dict(delegation.usage) if delegation else {}
            # Arguments that are not valid JSON are said to be so, and the tool does not run (t3: they were read as
            # no arguments, the tool answered "missing a required argument", and the same call came back five times)
            dispatched = broken is None and not json_halted() and not (stopped and stopped())
            if json_halted():
                result = 'not run: ' + argument_recovery.violations[-1]['detail']
            elif broken is not None:
                result = BAD_JSON.format(name=fn.get('name', ''), why=getattr(broken, 'msg', str(broken)),
                                        at=getattr(broken, 'pos', '?'), size=len(raw_text), tail=raw_text[-80:])
                if argument_recovery is not None:
                    left = 2 - argument_recovery.pending['corrections']
                    result += (f' You have {left} JSON correction attempt(s) left for this request. Reissue the SAME '
                        'tool as one JSON object within the same task, paths, files and caps. Do not add retry=true '
                        'solely for JSON syntax: nothing executed. Preserve retry=true if this request was already '
                        'correcting an earlier executed-command or closure-format failure. No other tool until corrected.')
            elif dispatched:
                record(account(),'tool',dict(index=len(log),name=fn.get('name',''),arguments=args))
                result = call_tool(fn.get('name', ''), args)
            else:
                result = 'not run: the sprint is stopping.'
            if delegation:
                after = delegation.usage
                count['calls'] += after['model_calls'] - before['model_calls']
                count['prompt'] += after['prompt_tokens'] - before['prompt_tokens']
                count['completion'] += after['completion_tokens'] - before['completion_tokens']
                meter.report(after['prompt_tokens'] - before['prompt_tokens'],
                             after['completion_tokens'] - before['completion_tokens'])
            entry = dict(name=fn.get('name', ''), arguments=args, result=str(result)[:300])
            if argument_event is not None:
                entry['argument_outcome'] = argument_event
            if not dispatched:
                entry.update(dispatched=False, arguments={}, result='not run: malformed tool JSON or stopped; full feedback in home transcript.')
            facts = outcome(fn.get('name', ''), result) if dispatched else None
            if facts is not None:
                entry['command_outcome'] = facts
            closure = closure_outcome(fn.get('name', ''), result) if dispatched else None
            if closure is not None:
                entry['closure_outcome'] = closure
            log.append(entry)
            tool_message = dict(role='tool', tool_call_id=tc.get('id', fn.get('name', '')), content=str(result))
            messages.append(tool_message)
            transcript.append(deepcopy(tool_message))
            record(account(),'between')
    if json_halted() or (argument_recovery and argument_recovery.pending) or (stopped and stopped()):
        why, count['stopped'] = 'The sprint is stopping. ', True
    if text is None:
        messages.append(dict(role='user', content=why + ending))
        reply = call(False) if not count['stopped'] else dict(content='')
        if reply is None:
            raise ContextCapacityError('the final request would not fit this deployment\'s context; nothing was sent',
                                       account(), transcript)
        transcript.append(deepcopy(messages[-1]))
        if reply.get('_finish_reason') or reply.get('content') or reply.get('tool_calls'):
            transcript.append(_as_given(reply, cut=reply.get('_finish_reason') == 'length'))
        text = reply.get('content') or ''
    record(account(),'finished')
    return text, account(), log
