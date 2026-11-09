"""Publish exact authored closure to Memory Fabric and its Welcome lifecycle.

Never invent reflection, mark a failed filing complete, or replace tool text with
the conversational final answer. Stable receipts make lost replies retryable.
"""
import hashlib
from pathlib import Path

from aether.backends.dialect import has_backend_markup
from aether.memory.foundations import begin, current_session, publish
from aether.tasker._files import file_lock, read_json, write_json
from . import memory

SOURCE = 'tasker-heavyweight'


class ClosingMarkupError(ValueError):
    """Transport evidence cannot serve as authored closure, even on recovery."""
    def __init__(self, text, *, retained=False):
        self.text, self.retained = text, retained
        super().__init__('Retained closing evidence contains backend markup; it was preserved for inspection.'
                         if retained else 'Closing words contain backend markup; no closing was filed.')


def _authored(text, *, retained=False):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('closing words must contain your reflection')
    if has_backend_markup(text, include_quoted=True):
        raise ClosingMarkupError(text, retained=retained)


def activate(home, session_ref):
    # Recovery is advisory. Invalid or unavailable evidence must not turn the
    # Timer's bounded closure-correction wakes into transport retries.
    from aether.memory.fabric import memory_fabric
    memory_fabric()
    from memory_fabric.errors import MemoryFabricError
    try:
        recover(home, session_ref)
    except (OSError, ValueError, KeyError, TypeError, MemoryFabricError):
        pass  # retain the evidence; the existing receipt checks still report incomplete
    with memory._open_write(home, session_ref) as engine:
        return begin(engine, session_ref, source_system=SOURCE, source_ref=session_ref)


def welcome(home, session_ref):
    from aether.memory.fabric import memory_fabric
    memory_fabric()
    from memory_fabric.dashboard import render_text
    with memory._open_read(home) as engine:
        sid = current_session(engine, session_ref, source_system=SOURCE)
        dashboard = engine.dashboard(session_id=sid)
        invalid = set()
        for element in dashboard.get('elements', []):
            value = element.get('value')
            if not isinstance(value, dict) or not value.get('note_id') or not value.get('closure_at'):
                continue
            # Inspect the full retained version, not just the 800-character excerpt.
            if value.get('at_revision') is not None:
                from memory_fabric.history import reconstruct
                saved = reconstruct(engine.cart, value['at_revision'])
                text = next(n['body'] for n in saved['notes'] if n['note_id'] == value['note_id'])
            else:
                text = engine.note(value['note_id'])['body']
            if has_backend_markup(text, include_quoted=True):
                invalid.add(value['session_id'])
                element.update(status='unavailable', value=None,
                    reason='Recorded closing carry contains backend markup, not verified closing words. '
                           'Original note ' + value['note_id'] + ' and closure history remain preserved for inspection.')
        for element in dashboard.get('elements', []):
            value = element.get('value')
            if not isinstance(value, dict):
                continue
            for outcome in (value, value.get('latest_verified_closure') or {}):
                if outcome.get('session_id') in invalid and 'state' in outcome:
                    outcome.update(state='RECORDED CLOSED; CARRY INVALID',
                                   reason='The stored closing carry contains backend markup; history is unchanged.')
        return render_text(dashboard, budget_tokens=1800)


def _receipt(home, session_ref):
    key = hashlib.sha256(session_ref.encode('utf-8')).hexdigest()[:24]
    return key, Path(home) / 'logs' / 'tasker_closure' / (key + '.json')


def _retained(home, session_ref, key, receipt, state):
    if (not isinstance(state, dict) or state.get('status') != 'CLOSED' or state.get('closure_id') != key
            or state.get('source_ref') != session_ref):
        raise ValueError('retained closure does not belong to this session')
    with memory._open_read(home) as engine:
        sid = current_session(engine, session_ref, source_system=SOURCE)
        if sid != state.get('session_id'):
            raise ValueError('retained closure session does not match its opening')
        text = engine.note(state['note_id'])['body']
        if hashlib.sha256(text.encode('utf-8')).hexdigest() != state.get('text_sha256'):
            raise ValueError('retained closing words failed exact readback')
        _authored(text, retained=True)
    with memory._open_write(home, session_ref) as engine:
        event = publish(engine, sid, key, receipt, carry_text=text,
                        carry_note_id=state['note_id'], source_system=SOURCE)
    return dict(note_id=state['note_id'], text=text, receipt=str(receipt), publication=event,
                notes=[('carry', state['note_id'], 'closing words')])


def recover(home, session_ref):
    """Replay publication from the existing verified receipt, never another reflection."""
    key, receipt = _receipt(home, session_ref)
    if not receipt.exists():
        return None
    with file_lock(receipt.with_suffix('.lock')):
        return _retained(home, session_ref, key, receipt, read_json(receipt))


def close(home, session_ref, text, carry_note_id=None):
    _authored(text)
    home = Path(home)
    key, receipt = _receipt(home, session_ref)
    with file_lock(receipt.with_suffix('.lock')):
        old = read_json(receipt, {})
        if receipt.exists() and (not isinstance(old, dict) or not old):
            raise ValueError('retained closure receipt is malformed; preserve it for inspection')
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        if old and old.get('text_sha256') != digest:
            raise ValueError('this session already filed different closing words; inspect the retained closure')
        if old:
            if carry_note_id is not None and carry_note_id != old.get('note_id'):
                raise ValueError('this session already filed a different carry note')
            return _retained(home, session_ref, key, receipt, old)
        with memory._open_write(home, session_ref) as engine:
            sid = begin(engine, session_ref, source_system=SOURCE, source_ref=session_ref)
        result = memory.file_closing(home, text, commission_id=session_ref, carry_note_id=carry_note_id)
        note_id = next(n[1] for n in result['notes'] if n[2] == 'closing words')
        with memory._open_read(home) as engine:
            if engine.note(note_id)['body'] != text:
                raise ValueError('closing words did not read back exactly')
        state = dict(status='CLOSED', closure_id=key, session_id=sid,
                     note_id=note_id, text_sha256=digest, source_ref=session_ref)
        if not old:
            write_json(receipt, state)
        with memory._open_write(home, session_ref) as engine:
            event = publish(engine, sid, key, receipt, carry_text=text,
                            carry_note_id=note_id, source_system=SOURCE)
        return dict(note_id=note_id, receipt=str(receipt), publication=event, notes=result['notes'])
