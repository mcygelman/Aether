"""Sealed lifecycle facts, outside authored-memory revision history.

Closing is a trusted host operation after the existing closure path verifies its
outputs. This module never generates personal closure prose or owns processes.
"""
from __future__ import annotations

import hashlib
import json
import uuid

from . import fabric, history, records
from .errors import MemoryFabricError

KINDS = ('opened', 'closure_started', 'closure_failed', 'closed', 'interrupted', 'recovery',
         'project_work', 'scaffold_started', 'scaffold_ended')
OPTIONAL = ('source_ref', 'previous_session_id', 'closure_id', 'run_id', 'project_id',
            'scaffold_id', 'note_id', 'scaffold_sha256', 'snapshot_revision', 'outcome',
            'evidence_ref', 'evidence_sha256')


def stable_id(source, kind, identity):
    return 'event-' + uuid.uuid5(uuid.NAMESPACE_URL, records.canonical([source, kind, identity])).hex


def event(cart, event_id):
    row = cart.conn.execute('SELECT * FROM session_events WHERE event_id=?', (event_id,)).fetchone()
    if not row:
        raise MemoryFabricError(f'unknown lifecycle event: {event_id}')
    result = dict(row)
    result['details'] = json.loads(result.pop('details_json'))
    return result


def append(cart, kind, session_id, *, source_system, source_event_id, summary='',
           occurred_at=None, details=None, verified=False, **fields):
    if kind not in KINDS or set(fields) - set(OPTIONAL):
        raise MemoryFabricError('invalid lifecycle kind or fields')
    if not source_system or not source_event_id:
        raise MemoryFabricError('lifecycle events require stable source identities')
    if details is not None and not isinstance(details, dict):
        raise MemoryFabricError('event details must be a data object')
    eid = stable_id(source_system, kind, source_event_id)
    sid = eid if kind == 'opened' else session_id
    request = dict(kind=kind, session_id=sid, source_system=source_system,
                   source_event_id=source_event_id, summary=summary, occurred_at=occurred_at,
                   details=details or {}, fields=fields)
    def write():
        if kind != 'opened':
            opening = event(cart, sid)
            if opening['kind'] != 'opened':
                raise MemoryFabricError('session_id must reference an opened event')
        previous = fields.get('previous_session_id')
        if previous and (previous == sid or event(cart, previous)['kind'] != 'opened'):
            raise MemoryFabricError('predecessor must be another session opening')
        if kind == 'closed' and (not verified or not fields.get('evidence_ref') or not fields.get('evidence_sha256')):
            raise MemoryFabricError('closed requires host-verified durable closure evidence')
        if kind in ('closed', 'scaffold_ended'):
            key, identity = ('session_id', sid) if kind == 'closed' else ('run_id', fields.get('run_id'))
            terminal = cart.conn.execute(f'SELECT event_id FROM session_events WHERE kind=? AND {key}=?',
                                        (kind, identity)).fetchone()
            if terminal:
                raise MemoryFabricError(f'{identity} already ended at {terminal[0]}; '
                                        'inspect that event or replay the original operation ID')
        if kind == 'scaffold_started':
            if not fields.get('scaffold_sha256') or fields.get('snapshot_revision') is None:
                raise MemoryFabricError('scaffold start requires content fingerprint and snapshot revision')
            fields['run_id'] = eid
            records.facet(cart, 'scaffolds', fields.get('scaffold_id'))
        if kind == 'scaffold_ended':
            start = event(cart, fields.get('run_id'))
            if start['kind'] != 'scaffold_started' or start['session_id'] != sid or start['scaffold_id'] != fields.get('scaffold_id'):
                raise MemoryFabricError('outcome must reference its own session and scaffold attempt')
        now = cart.clock.now()
        values = dict(event_id=eid, session_id=sid, kind=kind,
                      occurred_at=fabric._iso(occurred_at or now, 'occurred_at'), recorded_at=now,
                      source_system=source_system, source_event_id=source_event_id, summary=summary,
                      details_json=records.canonical(details or {}), **fields)
        cart.conn.execute('INSERT INTO session_events (' + ','.join(values) + ') VALUES (' +
                          ','.join('?' for _ in values) + ')', list(values.values()))
        return {'event': event(cart, eid)}
    # The request is captured before calculated run fields are added. A lost reply
    # can be replayed without inventing a second event or depending on today's time.
    return records.mutate(cart, 'lifecycle_' + kind, request, write,
                          operation_id='lifecycle:' + eid)


def open_session(cart, source_session_id, *, source_system='aether', previous_session_id=None,
                 source_ref=None):
    return append(cart, 'opened', None, source_system=source_system,
                  source_event_id='opened:' + source_session_id, summary='Logical session opened',
                  previous_session_id=previous_session_id, source_ref=source_ref)['event']['event_id']


def scaffold_fingerprint(cart, scaffold_id):
    facet = records.facet(cart, 'scaffolds', scaffold_id)
    note = fabric._require_note(cart, scaffold_id)
    value = {k: note[k] for k in ('title', 'body', 'source_ref')}
    value.update({k: facet[k] for k in ('source_version', 'source_sha256')})
    digest = hashlib.sha256(records.canonical(value).encode('utf-8')).hexdigest()
    return {'sha256': digest, 'format': 'scaffold-content-v1', 'content': value,
            'external_edition_known': not note['source_ref'] or bool(facet['source_version'] or facet['source_sha256'])}


def start_scaffold(cart, session_id, scaffold_id, *, operation_id, source_system='aether',
                   source_ref=None):
    # Outer receipt must be checked before recomputing a possibly edited scaffold.
    request = dict(session_id=session_id, scaffold_id=scaffold_id, source_system=source_system,
                   source_ref=source_ref)
    def write():
        fp = scaffold_fingerprint(cart, scaffold_id)
        if not fp['external_edition_known']:
            raise MemoryFabricError('identify the external instruction edition before recording this attempt')
        return append(cart, 'scaffold_started', session_id, source_system=source_system,
                      source_event_id='scaffold-start:' + operation_id, scaffold_id=scaffold_id,
                      scaffold_sha256=fp['sha256'], snapshot_revision=history.revision(cart),
                      source_ref=source_ref, summary='Scaffold attempt started',
                      details={'fingerprint_format': fp['format'], 'source': {
                          k: fp['content'][k] for k in ('source_ref', 'source_version', 'source_sha256')}})
    return records.mutate(cart, 'scaffold_start', request, write, operation_id='scaffold-start:' + operation_id)


def end_scaffold(cart, run_id, outcome, *, operation_id, note_id=None, evidence_ref=None,
                 evidence_sha256=None, source_system='aether'):
    start = event(cart, run_id)
    return append(cart, 'scaffold_ended', start['session_id'], source_system=source_system,
                  source_event_id='scaffold-end:' + operation_id, run_id=run_id,
                  scaffold_id=start['scaffold_id'], outcome=outcome, note_id=note_id,
                  evidence_ref=evidence_ref, evidence_sha256=evidence_sha256,
                  summary='Scaffold attempt ' + outcome)


def events(cart, *, session_id=None, after=0, limit=100):
    if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 500:
        raise MemoryFabricError('event paging needs after>=0 and limit 1..500')
    where, args = 'event_seq>?', [after]
    if session_id:
        where += ' AND session_id=?'
        args.append(session_id)
    rows = cart.conn.execute('SELECT event_id FROM session_events WHERE ' + where +
                             ' ORDER BY event_seq LIMIT ?', [*args, limit + 1]).fetchall()
    return {'events': [event(cart, r[0]) for r in rows[:limit]], 'more': len(rows) > limit,
            'next_after': event(cart, rows[limit - 1][0])['event_seq'] if len(rows) >= limit else None}


def closure_outcome(cart, session_id):
    rows = [dict(r) for r in cart.conn.execute('SELECT * FROM session_events WHERE session_id=? ORDER BY event_seq',
                                              (session_id,))]
    kinds = {r['kind'] for r in rows}
    state = ('PASS' if 'closed' in kinds else 'FAILED' if 'closure_failed' in kinds
             else 'INTERRUPTED' if 'interrupted' in kinds else 'UNKNOWN')
    last = next((r for r in reversed(rows) if r['kind'] == 'closed'), rows[-1])
    contradictory = state == 'PASS' and any(r['kind'] in ('closure_failed', 'interrupted') and
        (r['occurred_at'], r['event_seq']) > (last['occurred_at'], last['event_seq']) for r in rows)
    if contradictory:
        state = 'UNKNOWN'
    return {'session_id': session_id, 'state': state, 'recovered': state == 'PASS' and 'closure_failed' in kinds,
        'evidence_ref': last['evidence_ref'], 'evidence_sha256': last['evidence_sha256'],
        'occurred_at': last['occurred_at'], 'event_id': last['event_id'],
        'reason': 'Conflicting terminal evidence requires inspection.' if contradictory else
                  'Historical verified outcome; external detail may be unavailable.' if state == 'PASS'
                  else 'An absent close does not by itself prove interruption.'}


HISTORY_SQL = 'SELECT * FROM session_events ORDER BY event_seq DESC'


def previous_closure(cart, current_session_id=None):
    current = event(cart, current_session_id) if current_session_id else None
    previous = current.get('previous_session_id') if current else None
    if current and current['kind'] != 'opened':
        raise MemoryFabricError('current session must be an opened event')
    latest = cart.conn.execute("SELECT session_id FROM session_events WHERE kind='closed' AND session_id!=? "
                              'ORDER BY occurred_at DESC,event_seq DESC LIMIT 1', (current_session_id or '',)).fetchone()
    latest = closure_outcome(cart, latest[0]) if latest else None
    # Excerpts orient; the journal remains complete and directly queryable.
    where = "o.kind='opened' AND o.event_id!=?"
    args = (current_session_id or '',)
    unresolved_where = where + " AND NOT EXISTS (SELECT 1 FROM session_events c WHERE c.session_id=o.event_id AND c.kind='closed')"
    def summary(condition):
        count = cart.conn.execute('SELECT count(*) FROM session_events o WHERE ' + condition, args).fetchone()[0]
        recent = [r[0] for r in cart.conn.execute('SELECT o.event_id FROM session_events o WHERE ' + condition +
                                                 ' ORDER BY o.event_seq DESC LIMIT 3', args)]
        return count, recent
    candidate_count, candidates = summary(where)
    unresolved_count, unresolved = summary(unresolved_where)
    context = {'latest_verified_closure': latest, 'unresolved_sessions': unresolved,
               'unresolved_count': unresolved_count, 'recent_reference_limit': 3,
               'history_read': {'tool': 'memory_query', 'sql': HISTORY_SQL}}
    if previous is None:
        if not candidate_count:
            return {'status': 'empty', 'value': None}
        return {'status': 'unknown', 'value': {'state': 'UNKNOWN', 'candidates': candidates, 'candidate_count': candidate_count,
                **context,
                'reason': 'No trustworthy predecessor association was supplied.'}}
    outcome = closure_outcome(cart, previous)
    return {'status': 'ok' if outcome['state'] != 'UNKNOWN' else 'unknown',
            'value': dict(outcome, **context)}


def scaffold_status(cart):
    starts = {r['event_id']: dict(r) for r in cart.conn.execute("SELECT * FROM session_events WHERE kind='scaffold_started'")}
    ends = [dict(r) for r in cart.conn.execute("SELECT * FROM session_events WHERE kind='scaffold_ended' ORDER BY occurred_at,event_seq")]
    complete = [(starts[e['run_id']], e) for e in ends if e['outcome'] == 'completed' and e['run_id'] in starts]
    latest = None
    if complete:
        st, en = complete[-1]
        latest = {'scaffold_id': st['scaffold_id'], 'run_id': st['run_id'], 'sha256': st['scaffold_sha256'],
                  'snapshot_revision': st['snapshot_revision'], 'completed_at': en['occurred_at'],
                  'title': fabric._require_note(cart, st['scaffold_id'])['title'],
                  'full_read': {'action': 'inspect', 'record_type': 'scaffolds', 'record_id': st['scaffold_id'],
                                'at_revision': st['snapshot_revision']},
                  'current_content_differs': scaffold_fingerprint(cart, st['scaffold_id'])['sha256'] != st['scaffold_sha256']}
    completed = {(st['scaffold_id'], st['scaffold_sha256']) for st, en in complete}
    superseded = {r[0] for r in cart.conn.execute('SELECT supersedes_scaffold_id FROM scaffolds WHERE supersedes_scaffold_id IS NOT NULL')}
    # Relevant note edits only; loading/links do not make an old scaffold newest.
    edited = {}
    for row in cart.conn.execute('SELECT c.revision,c.at,h.payload FROM memory_changes c JOIN history_data h USING(history_id) ORDER BY c.revision'):
        changes = json.loads(row['payload']).get('notes', {})
        old = {n['note_id']: n for n in changes.get('before', [])}
        for n in changes.get('after', []):
            if any(n.get(k) != old.get(n['note_id'], {}).get(k) for k in ('title', 'body', 'source_ref')):
                edited[n['note_id']] = row['at']
    pending, uncertain = [], []
    for row in cart.conn.execute('SELECT s.*,n.title,n.created_at FROM scaffolds s JOIN notes n ON n.note_id=s.scaffold_id WHERE s.archived_at IS NULL'):
        rid = row['scaffold_id']
        if rid in superseded:
            continue
        fp = scaffold_fingerprint(cart, rid)
        item = {'scaffold_id': rid, 'title': row['title'], 'sha256': fp['sha256'],
                'continuation_note_id': row['continuation_note_id'],
                'authored_at': max(row['updated_at'], edited.get(rid, row['created_at'])),
                'attempts': [{'run_id': st['run_id'], 'outcome': next((e['outcome'] for e in ends if e['run_id'] == st['run_id']), 'unfinished')}
                             for st in starts.values() if st['scaffold_id'] == rid and st['scaffold_sha256'] == fp['sha256']]}
        if not fp['external_edition_known']:
            uncertain.append(item)
        elif (rid, fp['sha256']) not in completed:
            pending.append(item)
    pending.sort(key=lambda r: (r['authored_at'], r['scaffold_id']), reverse=True)
    return {'executed': latest, 'unexecuted': pending[0] if pending else None, 'uncertain': uncertain}
