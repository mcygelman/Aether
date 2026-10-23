"""Host adapters for durable session opening and existing closure evidence."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .fabric import memory_fabric


def current_session(engine, source_session_id, *, source_system):
    """Follow recorded successors without creating an opening during a read."""
    memory_fabric()
    from memory_fabric.lifecycle import stable_id
    found = None
    while True:
        eid = stable_id(source_system, 'opened', 'opened:' + source_session_id)
        if not engine.cart.conn.execute('SELECT 1 FROM session_events WHERE event_id=?', (eid,)).fetchone():
            return found
        found = eid
        source_session_id = 'after:' + eid


def begin(engine, source_session_id, *, source_system, source_ref=None,
          previous_session_id=None, new_work=False):
    """Attach a known continuation; choose a predecessor only with unambiguous evidence."""
    memory_fabric()
    from memory_fabric.lifecycle import stable_id
    # Only an actual activation may create a successor. A publication retry must
    # remain attached to its original opening, even after that opening closed.
    while True:
        eid = stable_id(source_system, 'opened', 'opened:' + source_session_id)
        existing = engine.cart.conn.execute('SELECT * FROM session_events WHERE event_id=?', (eid,)).fetchone()
        if not existing:
            break
        closed = engine.cart.conn.execute("SELECT 1 FROM session_events WHERE session_id=? AND kind='closed'", (eid,)).fetchone()
        if not new_work or not closed:
            engine.logical_session_id = eid
            return eid
        previous_session_id = eid
        source_session_id = 'after:' + eid
    rows = engine.cart.conn.execute("SELECT o.event_id, c.event_seq AS closed_seq FROM session_events o "
        "LEFT JOIN session_events c ON c.session_id=o.event_id AND c.kind='closed' WHERE o.kind='opened'").fetchall()
    previous = previous_session_id
    if previous is None and rows and all(r['closed_seq'] is not None for r in rows):
        previous = max(rows, key=lambda r: r['closed_seq'])['event_id']
    return engine.begin_session(source_session_id, source_system=source_system,
                                source_ref=source_ref, previous_session_id=previous)


def publish(engine, session_id, closure_id, state_path, *, carry_text=None, failure=None,
            source_system='bridge', occurred_at=None, carry_note_id=None):
    """Called only after the source path has verified success (or reported failure)."""
    memory_fabric()
    from memory_fabric import lifecycle, records
    path = Path(state_path)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    kind = 'closure_failed' if failure else 'closed'
    if failure is None:
        state = json.loads(raw.decode('utf-8'))
        if state.get('status') != 'CLOSED' or state.get('closure_id') != closure_id:
            raise ValueError('closure evidence does not establish the requested successful close')
    request = dict(session_id=session_id, closure_id=closure_id, evidence_sha256=digest,
                   carry_text=carry_text, carry_note_id=carry_note_id, failure=failure, source_system=source_system)
    def write():
        note_id = carry_note_id
        if note_id and engine.note(note_id)['body'] != carry_text:
            raise ValueError('retained carry note differs from the authored closing words')
        if not note_id and carry_text is not None and failure is None:
            # The host copies the already authored words; it does not compose a new note.
            note_id = engine.remember('Carry from closure ' + closure_id, carry_text, 'offloaded_context',
                                      ['continuity', 'closure carry'], source_ref=str(path))
        return lifecycle.append(engine.cart, kind, session_id, source_system=source_system,
            source_event_id=kind + ':' + closure_id + (':' + digest if failure else ''),
            closure_id=closure_id, note_id=note_id, evidence_ref=str(path), evidence_sha256=digest,
            summary=str(failure) if failure else 'Existing closure path verified required persistence.',
            occurred_at=occurred_at, verified=failure is None,
            details={'carry_note_id': note_id, 'carry_sha256': hashlib.sha256(carry_text.encode('utf-8')).hexdigest()
                     if carry_text is not None else None,
                     'carry_revision': engine.revision + (1 if note_id and not carry_note_id else 0)})
    return records.mutate(engine.cart, 'closure_publication', request, write,
                          operation_id='closure-publication:' + source_system + ':' + kind + ':' + closure_id +
                          (':' + digest if failure else ''))


def reconcile_publications(engine, profile):
    """Replay only this host's recorded closure starts with retained publication input.

    No new closure is performed and no current SESSION_CURRENT is substituted for
    earlier prose. Failed or missing evidence remains available for inspection.
    """
    from memory_fabric.errors import MemoryFabricError
    root = (Path(profile) / 'logs' / 'closure_state').resolve()
    results = []
    rows = engine.cart.conn.execute("SELECT s.* FROM session_events s JOIN session_events o ON o.event_id=s.session_id "
        "WHERE s.kind='closure_started' AND s.source_system='bridge' AND o.source_ref=? "
        "AND NOT EXISTS (SELECT 1 FROM session_events c WHERE c.kind='closed' AND c.closure_id=s.closure_id "
        "AND c.source_system=s.source_system)", (str(profile),)).fetchall()
    for row in rows:
        cid = row['closure_id']
        state_path = root / (cid + '.json')
        packet_path = root / (cid + '.publication.json')
        if state_path.resolve().parent != root or packet_path.resolve().parent != root:
            continue
        try:
            state = json.loads(state_path.read_text(encoding='utf-8'))
            packet = json.loads(packet_path.read_text(encoding='utf-8'))
            if state.get('status') != 'CLOSED':
                continue
            if (packet.get('session_id') != row['session_id'] or
                    state.get('closure_id') != cid or
                    packet.get('plan_sha256') != state.get('plan_sha256') or
                    hashlib.sha256(packet['carry_text'].encode('utf-8')).hexdigest() != state.get('carry_sha256')):
                raise ValueError('retained publication input does not match verified closure')
            result = publish(engine, row['session_id'], cid, state_path,
                             carry_text=packet['carry_text'])
            results.append(result)
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, MemoryFabricError) as exc:
            results.append({'status': 'publication_pending', 'closure_id': cid, 'error': str(exc)})
    return results
