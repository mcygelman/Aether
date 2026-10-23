"""Owner workflows: inspect, revise, organize, reconcile, and reconstruct.

The caller supplies intent and an observed cartridge revision. The trusted host
supplies actor/session. Previous row patterns survive in history, not active tags.
"""
from __future__ import annotations

import json

from . import fabric, history, kinds, schema
from .errors import MemoryFabricError

READ_ACTIONS = ('inspect', 'history', 'reconstruct', 'reviews', 'profiles')
WRITE_ACTIONS = ('revise', 'organize', 'properties', 'refresh', 'link', 'unlink',
                 'bury', 'raise', 'merge', 'restore', 'respond', 'tune_profile')


def inspect(cart, note_id):
    note = fabric.get_note(cart, note_id)
    note['stored'] = fabric._require_note(cart, note_id)
    note['links'] = [dict(r) for r in cart.conn.execute(
        'SELECT * FROM links WHERE a=? OR b=? ORDER BY link_id', (note_id, note_id))]
    return {'note': note, 'revision': history.revision(cart)}


def _editable(cart, note_id):
    note = fabric._require_note(cart, note_id)
    if note['written_by'] != cart.agent_id:
        raise MemoryFabricError('outside-authored notes cannot be revised; write a linked response')
    return note


def _replace_tags(cart, note_id, tags):
    title = fabric._require_note(cart, note_id)['title']
    normalized = sorted({fabric.normalize_tag(t) for t in [title, *tags]})
    cart.conn.execute('DELETE FROM tags WHERE note_id=?', (note_id,))
    cart.conn.executemany('INSERT INTO tags VALUES (?,?)', [(note_id, t) for t in normalized])


def _revise(cart, note_id, args):
    original = _editable(cart, note_id)
    for key in ('title', 'body'):
        if key in args:
            value = args[key]
            if not isinstance(value, str) or (key == 'title' and not value.strip()):
                raise MemoryFabricError(f'{key} must be text, title must not be blank')
            cart.conn.execute(f'UPDATE notes SET {key}=? WHERE note_id=?', (value, note_id))
    if 'tags' in args or 'title' in args:
        tags = args.get('tags', [r[0] for r in cart.conn.execute(
            'SELECT tag FROM tags WHERE note_id=? AND tag<>?',
            (note_id, fabric.normalize_tag(original['title'])))])
        _replace_tags(cart, note_id, tags)


def execute(cart, args):
    action = args['action']
    required = {
        'inspect': ('id',), 'history': (), 'reconstruct': ('id', 'at_revision'), 'reviews': (),
        'revise': ('id',), 'organize': ('id', 'tags'), 'properties': ('id',),
        'refresh': ('id',), 'link': ('id', 'to_id'), 'unlink': ('id', 'to_id'),
        'bury': ('id',), 'raise': ('id',), 'merge': ('id', 'source_ids', 'body'),
        'restore': ('id', 'at_revision'), 'respond': ('review_id', 'body'),
        'profiles': (), 'tune_profile': ('kind', 'profile_values')}
    if action not in required:
        raise MemoryFabricError('unknown maintenance action')
    missing = [key for key in required[action] if key not in args or args[key] is None]
    if missing:
        raise MemoryFabricError(f'{action} needs {missing}')
    if action == 'revise' and not {'title', 'body', 'tags'} & args.keys():
        raise MemoryFabricError('revise needs title, body or tags')
    if action == 'properties' and not {'kind', 'importance', 'current_for_days', 'current_until',
                                     'delta', 'proprietary'} & args.keys():
        raise MemoryFabricError('properties needs at least one property change')
    note_id = args.get('id')
    if action == 'profiles':
        return {'profiles': cart.profiles(), 'revision': history.revision(cart)}
    if action == 'inspect':
        return inspect(cart, note_id)
    if action == 'history':
        entries = history.changes(cart, args.get('after', 0), args.get('limit', 20))
        return {'changes': entries, 'revision': history.revision(cart),
                'next_after': entries[-1]['revision'] if entries else args.get('after', 0)}
    if action == 'reconstruct':
        state = history.reconstruct(cart, args['at_revision'])
        note = next((n for n in state['notes'] if n['note_id'] == note_id), None)
        return {'historical': True, 'at_revision': args['at_revision'], 'note': note,
                'tags': [r['tag'] for r in state['tags'] if r['note_id'] == note_id],
                'links': [r for r in state['links'] if note_id in (r['a'], r['b'])]}
    if action == 'reviews':
        after, limit = args.get('after', 0), args.get('limit', 20)
        return {'reviews': [dict(r) for r in cart.conn.execute(
            'SELECT * FROM reviews WHERE review_id>? ORDER BY review_id LIMIT ?', (after, limit))],
            'responses': [dict(r) for r in cart.conn.execute(
                'SELECT * FROM review_responses WHERE review_id=? ORDER BY response_id LIMIT ?',
                (args.get('review_id', 0), limit))]}
    if action not in WRITE_ACTIONS:
        raise MemoryFabricError('unknown maintenance action')
    if not str(args.get('reason', '')).strip():
        raise MemoryFabricError('a change needs a reason')
    with cart.transaction(immediate=True, operation=action, description=args['reason']):
        history.require_revision(cart, args.get('expected_revision'))
        if action in ('revise', 'organize'):
            _revise(cart, note_id, args)
        elif action == 'properties':
            _editable(cart, note_id)
            if 'kind' in args:
                profile = kinds.resolve(args['kind'], [p['profile'] for p in cart.profiles()])
                cart.conn.execute('UPDATE notes SET profile=? WHERE note_id=?', (profile, note_id))
            if 'importance' in args:
                from .tools import importance_value
                value = importance_value(args['importance'], cart.setting('fog_importance_min'))
                cart.conn.execute('UPDATE notes SET importance_override=? WHERE note_id=?', (value, note_id))
            if 'current_for_days' in args or 'current_until' in args:
                from .tools import MemoryTools
                switch = MemoryTools._switch(args)
                fabric.set_switch(cart, note_id, switch.get('switch_at'), switch.get('switch_in_days'))
            if 'delta' in args:
                fabric.adjust_depth(cart, note_id, args['delta'], args.get('fade_days'))
            if 'proprietary' in args:
                cart.conn.execute('UPDATE notes SET proprietary=? WHERE note_id=?',
                                  (int(args['proprietary']), note_id))
        elif action == 'refresh':
            fabric.refresh(cart, [note_id])
        elif action == 'bury':
            fabric.bury(cart, note_id)
        elif action == 'raise':
            fabric.raise_note(cart, note_id)
        elif action in ('link', 'unlink'):
            other = args['to_id']
            fabric._require_note(cart, note_id)
            fabric._require_note(cart, other)
            relation = args.get('relation', 'context')
            if relation not in schema.RELATIONS:
                raise MemoryFabricError('unknown relation')
            # Replacement is deliberate; legacy link() instead reinforces an association.
            cart.conn.execute('DELETE FROM links WHERE relation=? AND '
                              '((a=? AND b=?) OR (a=? AND b=?))', (relation, note_id, other, other, note_id))
            if action == 'link':
                from .tools import STRENGTH_WORDS, MemoryTools
                from .clock import add_days
                switch = MemoryTools._switch(args)
                until = switch.get('switch_at')
                if 'switch_in_days' in switch:
                    until = add_days(cart.clock.now(), switch['switch_in_days'])
                fabric.link(cart, note_id, other, relation, STRENGTH_WORDS[args.get('strength', 'normal')],
                            until)
        elif action == 'merge':
            sources = list(dict.fromkeys(args['source_ids']))
            if not sources or note_id in sources or 'body' not in args:
                raise MemoryFabricError('merge needs other source_ids and the owner-authored resulting body')
            _revise(cart, note_id, args)
            for source in sources:
                _editable(cart, source)
                links = list(cart.conn.execute('SELECT * FROM links WHERE a=? OR b=?', (source, source)))
                for link in links:
                    other = link['b'] if link['a'] == source else link['a']
                    if other != note_id and other not in sources:
                        fabric.link(cart, note_id, other, link['relation'], link['strength'], link['switch_at'])
                cart.conn.execute('DELETE FROM links WHERE a=? OR b=?', (source, source))
                fabric.bury(cart, source)
        elif action == 'restore':
            _editable(cart, note_id)
            state = history.reconstruct(cart, args['at_revision'])
            previous = next((n for n in state['notes'] if n['note_id'] == note_id), None)
            if previous is None:
                raise MemoryFabricError('note did not exist at that revision; bury it instead')
            if previous['written_by'] != cart.agent_id:
                raise MemoryFabricError('cannot restore outside-authored content as an owner revision')
            columns = [k for k in previous if k != 'note_id']
            cart.conn.execute('UPDATE notes SET ' + ','.join(f'{k}=?' for k in columns) + ' WHERE note_id=?',
                              [previous[k] for k in columns] + [note_id])
            _replace_tags(cart, note_id, [r['tag'] for r in state['tags'] if r['note_id'] == note_id])
            cart.conn.execute('DELETE FROM links WHERE a=? OR b=?', (note_id, note_id))
            for link in state['links']:
                if note_id in (link['a'], link['b']):
                    keys = list(link)
                    cart.conn.execute('INSERT INTO links (' + ','.join(keys) + ') VALUES (' +
                                      ','.join('?' for _ in keys) + ')', list(link.values()))
        elif action == 'respond':
            if not cart.conn.execute('SELECT 1 FROM reviews WHERE review_id=?', (args['review_id'],)).fetchone():
                raise MemoryFabricError('unknown review')
            if not args.get('body', '').strip():
                raise MemoryFabricError('response needs body')
            cart.conn.execute('INSERT INTO review_responses(review_id,author,text,evidence_refs,created_at) '
                              'VALUES (?,?,?,?,?)', (args['review_id'], cart.actor, args['body'],
                               json.dumps(args.get('evidence_refs', [])), cart.clock.now()))
        elif action == 'tune_profile':
            profile = kinds.resolve(args['kind'], [p['profile'] for p in cart.profiles()])
            if not args['profile_values']:
                raise MemoryFabricError('tune_profile needs at least one profile value')
            cart.tune_profile(profile, cart.actor, reason=args['reason'], **args['profile_values'])
    return {'text': f'{action} recorded; prior structure retained in history.', 'id': note_id,
            'revision': history.revision(cart)}
