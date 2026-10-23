"""Authored note facets and ordered Threads. One transaction and receipt per intent."""
from __future__ import annotations

import hashlib
import json
import uuid

from . import fabric, history, maintenance
from .errors import MemoryFabricError
from .foundations_schema import FACETS


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def identity(prefix):
    return prefix + '-' + uuid.uuid4().hex


def mutate(cart, action, request, fn, *, operation_id=None, expected_revision=None):
    """Idempotent intent; only real authored deltas advance revision."""
    if cart.read_only:
        raise MemoryFabricError('this cartridge is read-only')
    digest = hashlib.sha256(canonical([action, request]).encode('utf-8')).hexdigest()
    with cart.transaction(immediate=True, operation=action, description=action.replace('_', ' ')):
        if operation_id:
            prior = cart.conn.execute('SELECT * FROM operation_receipts WHERE operation_id=?',
                                      (operation_id,)).fetchone()
            if prior:
                if prior['request_hash'] != digest or prior['actor'] != cart.actor:
                    raise MemoryFabricError('operation_id belongs to a different request or actor')
                return json.loads(prior['result'])
        if expected_revision is not None:
            history.require_revision(cart, expected_revision)
        mark = history.mark(cart.conn)
        result = fn()
        result['revision'] = history.revision(cart) + int(bool(history.delta(cart.conn, mark)))
        if operation_id:
            cart.conn.execute('INSERT INTO operation_receipts VALUES (?,?,?,?)',
                              (operation_id, digest, cart.actor, canonical(result)))
        return result


def table_spec(record_type):
    if record_type not in FACETS:
        raise MemoryFabricError(f'record_type must be one of {list(FACETS)}')
    return FACETS[record_type]


def facet(cart, record_type, record_id):
    key, _ = table_spec(record_type)
    row = cart.conn.execute(f'SELECT * FROM {record_type} WHERE {key}=?', (record_id,)).fetchone()
    if row is None:
        raise MemoryFabricError(f'unknown {record_type} record: {record_id}')
    return dict(row)


def inspect(cart, record_type, record_id, *, offset=0, limit=6000, at_revision=None):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100000:
        raise MemoryFabricError('body paging needs offset>=0 and limit 1..100000')
    with cart.read_snapshot():
        if at_revision is None:
            row = facet(cart, record_type, record_id)
            note = fabric._require_note(cart, record_id)
            tags = [r[0] for r in cart.conn.execute('SELECT tag FROM tags WHERE note_id=? ORDER BY tag', (record_id,))]
        else:
            key, _ = table_spec(record_type)
            snapshot = history.reconstruct(cart, at_revision)
            row = next((r for r in snapshot[record_type] if r[key] == record_id), None)
            note = next((r for r in snapshot['notes'] if r['note_id'] == record_id), None)
            if row is None or note is None:
                raise MemoryFabricError('record did not exist at the selected revision')
            tags = sorted(r['tag'] for r in snapshot['tags'] if r['note_id'] == record_id)
        total = len(note['body'])
        note['body'] = note['body'][offset:offset + limit]
        note['tags'] = tags
        return {'record_type': record_type, 'record': row, 'note': note, 'total_chars': total,
                'offset': offset, 'next_offset': offset + limit if offset + limit < total else None,
                'revision': history.revision(cart), 'at_revision': at_revision}


def listing(cart, record_type, *, include_archived=False, offset=0, limit=50):
    key, _ = table_spec(record_type)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
        raise MemoryFabricError('catalog paging needs offset>=0 and limit 1..200')
    with cart.read_snapshot():
        where = '' if include_archived else ' WHERE f.archived_at IS NULL'
        total = cart.conn.execute(f'SELECT count(*) FROM {record_type} f' + where).fetchone()[0]
        rows = cart.conn.execute(f'SELECT f.*, n.title, n.created_at, n.source_ref FROM {record_type} f '
                                 f'JOIN notes n ON n.note_id=f.{key}' + where +
                                 f' ORDER BY f.updated_at DESC,f.{key} LIMIT ? OFFSET ?', (limit, offset))
        return {'records': [dict(r) for r in rows], 'total': total, 'offset': offset,
                'next_offset': offset + limit if offset + limit < total else None}


def _fields(cart, record_type, record_id, fields, source_ref):
    _, defaults = table_spec(record_type)
    if not isinstance(fields, dict) or set(fields) - set(defaults):
        raise MemoryFabricError(f'allowed {record_type} fields: {list(defaults)}')
    for name, value in fields.items():
        if value is not None and not isinstance(value, str):
            raise MemoryFabricError(f'{name} must be text or null')
    if fields.get('content_mode') == 'external_reference' and not source_ref:
        raise MemoryFabricError('external references need a source_ref')
    if record_type == 'scaffolds':
        seen = {record_id}
        parent = fields.get('supersedes_scaffold_id')
        while parent:
            if parent in seen:
                raise MemoryFabricError('scaffold supersession cycle')
            seen.add(parent)
            parent = facet(cart, 'scaffolds', parent)['supersedes_scaffold_id']


def create(cart, record_type, *, title=None, body='', kind='project_fact', tags=(), fields=None,
           note_id=None, source_ref=None, category=None, stratum=None, links=(),
           operation_id=None, expected_revision=None, written_by=None):
    """written_by: the note's author; the owner unless a supplied resource names its own (a seed stays a seed)."""
    key, defaults = table_spec(record_type)
    request = dict(record_type=record_type, title=title, body=body, kind=kind, tags=list(tags),
                   fields=fields, note_id=note_id, source_ref=source_ref, category=category,
                   stratum=stratum, links=list(links))
    if written_by is not None:
        request['written_by'] = written_by
    def write():
        rid = note_id or identity('n')
        values = dict(defaults, **(fields or {}))
        existing = cart.conn.execute('SELECT * FROM notes WHERE note_id=?', (rid,)).fetchone()
        if existing:
            if title is not None or body or source_ref is not None or tags or links:
                raise MemoryFabricError('attaching a facet to an existing note accepts fields only')
            source = existing['source_ref']
        else:
            source = source_ref
            fabric.remember(cart, title, body, kind, tags, note_id=rid, written_by=written_by or cart.agent_id,
                            source_ref=source, category=category or ('Thread' if record_type == 'threads' else 'Information'),
                            stratum=stratum, links=links)
        _fields(cart, record_type, rid, values, source)
        values.update({key: rid, 'updated_at': cart.clock.now(), 'updated_by': cart.actor})
        names = list(values)
        cart.conn.execute(f'INSERT INTO {record_type} ({",".join(names)}) VALUES ({",".join("?" for _ in names)})',
                          [values[n] for n in names])
        return {'id': rid, 'record_type': record_type}
    return mutate(cart, 'record_create', request, write, operation_id=operation_id,
                  expected_revision=expected_revision)


def revise(cart, record_type, record_id, *, fields=None, note=None, archived=None,
           operation_id=None, expected_revision=None):
    key, defaults = table_spec(record_type)
    request = dict(record_type=record_type, record_id=record_id, fields=fields, note=note, archived=archived)
    def write():
        current = facet(cart, record_type, record_id)
        updates = fields or {}
        values = {k: current[k] for k in defaults}
        values.update(updates)
        source = fabric._require_note(cart, record_id)['source_ref']
        if note:
            if set(note) - {'title', 'body', 'tags', 'source_ref'}:
                raise MemoryFabricError('note changes allow title, body, tags, source_ref')
            if current.get('content_mode') == 'adopted_copy' and \
                    fabric._require_note(cart, record_id)['written_by'] != cart.agent_id:
                # The first edit of an adopted copy makes it the owner's words; until then a seed is its author's.
                cart.conn.execute('UPDATE notes SET written_by=? WHERE note_id=?', (cart.agent_id, record_id))
            maintenance._editable(cart, record_id)
            maintenance._revise(cart, record_id, note)
            if 'source_ref' in note:
                source = note['source_ref']
                if source is not None and not isinstance(source, str):
                    raise MemoryFabricError('source_ref must be text or null')
                cart.conn.execute('UPDATE notes SET source_ref=? WHERE note_id=?', (source, record_id))
        _fields(cart, record_type, record_id, values, source)
        values.update(updated_at=cart.clock.now(), updated_by=cart.actor)
        if archived is not None:
            if type(archived) is not bool:
                raise MemoryFabricError('archived must be boolean')
            values['archived_at'] = cart.clock.now() if archived else None
        cart.conn.execute(f'UPDATE {record_type} SET ' + ','.join(f'{k}=?' for k in values) + f' WHERE {key}=?',
                          [*values.values(), record_id])
        return {'id': record_id, 'record_type': record_type}
    return mutate(cart, 'record_revise', request, write, operation_id=operation_id,
                  expected_revision=expected_revision)


_OMITTED = object()


def thread_edit(cart, thread_id, *, element_id=None, note_id=None, parent_element_id=_OMITTED,
                position=_OMITTED, archived=_OMITTED, link=False, operation_id=None, expected_revision=None):
    request = dict(thread_id=thread_id, element_id=element_id, note_id=note_id,
                   parent_element_id=parent_element_id, position=position, archived=archived, link=link)
    request = {k: v for k, v in request.items() if v is not _OMITTED}
    def write():
        facet(cart, 'threads', thread_id)
        eid = element_id or identity('element')
        row = cart.conn.execute('SELECT * FROM thread_elements WHERE element_id=?', (eid,)).fetchone()
        if row and row['thread_id'] != thread_id:
            raise MemoryFabricError('element belongs to another Thread')
        nid = note_id or (row['note_id'] if row else None)
        fabric._require_note(cart, nid)
        chosen_parent = (row['parent_element_id'] if row else None) if parent_element_id is _OMITTED else parent_element_id
        chosen_position = (row['position'] if row else 0) if position is _OMITTED else position
        chosen_archived = (bool(row['archived_at']) if row else False) if archived is _OMITTED else archived
        if type(chosen_archived) is not bool:
            raise MemoryFabricError('archived must be a boolean')
        if type(chosen_position) is not int or chosen_position < 0:
            raise MemoryFabricError('position must be a nonnegative integer')
        seen, parent = {eid}, chosen_parent
        while parent:
            if parent in seen:
                raise MemoryFabricError('Thread ancestry cycle')
            seen.add(parent)
            r = cart.conn.execute('SELECT * FROM thread_elements WHERE element_id=? AND thread_id=?',
                                  (parent, thread_id)).fetchone()
            if not r:
                raise MemoryFabricError('parent must belong to this Thread')
            parent = r['parent_element_id']
        now = cart.clock.now()
        if row:
            cart.conn.execute('UPDATE thread_elements SET parent_element_id=?,note_id=?,position=?,updated_at=?, '
                              'updated_by=?,archived_at=? WHERE element_id=?',
                              (chosen_parent, nid, chosen_position, now, cart.actor,
                               (row['archived_at'] or now) if chosen_archived else None, eid))
        else:
            cart.conn.execute('INSERT INTO thread_elements VALUES (?,?,?,?,?,?,?,?,?,?)',
                              (eid, thread_id, chosen_parent, nid, chosen_position, now, cart.actor, now,
                               cart.actor, now if chosen_archived else None))
        if link and nid != thread_id:
            fabric.link(cart, thread_id, nid, 'context')
        return {'element_id': eid, 'thread_id': thread_id}
    return mutate(cart, 'thread_edit', request, write, operation_id=operation_id,
                  expected_revision=expected_revision)


def thread_read(cart, thread_id, *, leaf_element_id=None, spine=False, include_archived=False):
    with cart.read_snapshot():
        facet(cart, 'threads', thread_id)
        rows = [dict(r) for r in cart.conn.execute('SELECT e.*,n.title FROM thread_elements e JOIN notes n '
                'ON n.note_id=e.note_id WHERE thread_id=? ORDER BY position,created_at,element_id', (thread_id,))]
        nodes = {r['element_id']: r for r in rows}
        visible = [r for r in rows if include_archived or not r['archived_at']]
        if not spine:
            return {'thread_id': thread_id, 'elements': visible}
        if leaf_element_id is None:
            parents = {r['parent_element_id'] for r in visible}
            leaves = [r for r in visible if r['element_id'] not in parents]
            if len(leaves) != 1:
                return {'thread_id': thread_id, 'branch_choices': leaves, 'elements': []}
            leaf_element_id = leaves[0]['element_id']
        if leaf_element_id not in nodes:
            raise MemoryFabricError('unknown leaf in this Thread')
        result, seen, current = [], set(), leaf_element_id
        while current:
            if current in seen or current not in nodes:
                raise MemoryFabricError('invalid Thread ancestry')
            seen.add(current)
            result.append(nodes[current])
            current = nodes[current]['parent_element_id']
        return {'thread_id': thread_id, 'leaf_element_id': leaf_element_id, 'elements': result[::-1]}


def associations(cart, note_id):
    with cart.read_snapshot():
        fabric._require_note(cart, note_id)
        return {'note_id': note_id, 'links': [dict(r) for r in cart.conn.execute(
            'SELECT * FROM temp.link_state WHERE a=? OR b=? ORDER BY link_id', (note_id, note_id))]}
