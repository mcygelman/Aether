"""SQLite FTS5 discovery across titles, bodies and tags, including explicit history.

Porter stemming and prefix matching handle partial recollections. Alternate words
are supplied by the owner, not invented synonyms or semantic understanding. The
index is temporary: cartridges remain data only, and reference mounts stay intact.
"""
from __future__ import annotations

import json
import re
import sqlite3

from . import history
from .errors import MemoryFabricError


def search(cart, query='', *, alternatives=(), include_buried=False, include_history=False,
           category=None, limit=12, offset=0, max_chars=12000, match='any'):
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise MemoryFabricError('discovery limit must be 1..100 and offset >= 0')
    if not isinstance(query, str) or len(query) > 2000 or len(alternatives) > 12:
        raise MemoryFabricError('use a query up to 2000 characters and at most 12 alternate phrases')
    if match not in ('any', 'all'):
        raise MemoryFabricError('match must be any or all')
    cart.sync_clock()
    # One SQLite read snapshot binds index content, strengths, history and revision.
    conn = cart.conn
    conn.execute('SAVEPOINT discovery_read')
    try:
        current = history.revision(cart)
        rows = []
        for row in conn.execute('SELECT * FROM temp.note_state ORDER BY refreshed_at DESC, note_id'):
            note = dict(row)
            note['tags'] = ' '.join(r[0] for r in conn.execute('SELECT tag FROM tags WHERE note_id=?', (note['note_id'],)))
            note['historical'] = False
            note['revision'] = current
            rows.append(note)
        if include_history:
            old_notes = {r['note_id']: dict(r) for r in conn.execute('SELECT * FROM notes')}
            old_tags = {}
            for tag in conn.execute('SELECT * FROM tags'):
                old_tags.setdefault(tag['note_id'], set()).add(tag['tag'])
            seen = set()
            for change in conn.execute('SELECT * FROM memory_changes ORDER BY revision DESC'):
                delta = history.payload(cart, change['revision'])
                rows.append({'note_id': f'change:{change["revision"]}',
                    'title': f'Change: {change["operation"]}', 'body': change['description'],
                    'tags': change['operation'], 'historical': True,
                    'revision': change['revision'], 'strength': None, 'category': 'Change',
                    'record_type': 'change', 'history_id': change['history_id']})
                affected = set()
                for phase in ('after', 'before'):
                    for note in delta.get('notes', {}).get(phase, []):
                        affected.add(note['note_id'])
                        if phase == 'after':
                            old_notes.pop(note['note_id'], None)
                        else:
                            old_notes[note['note_id']] = note
                    for tag in delta.get('tags', {}).get(phase, []):
                        affected.add(tag['note_id'])
                        tags = old_tags.setdefault(tag['note_id'], set())
                        if phase == 'after':
                            tags.discard(tag['tag'])
                        else:
                            tags.add(tag['tag'])
                for note_id in sorted(affected):
                    note = old_notes.get(note_id)
                    if note is None:
                        continue
                    tags = ' '.join(sorted(old_tags.get(note_id, ())))
                    key = (note_id, note['title'], note['body'], tags)
                    if key not in seen:
                        rows.append({**note, 'tags': tags, 'historical': True,
                                     'revision': change['revision'] - 1, 'strength': None})
                        seen.add(key)
        conn.execute('DROP TABLE IF EXISTS temp.discovery_index')
        method = 'fts5 porter stemming and token prefixes; lexical, not semantic'
        all_terms = re.findall(r'\w+', ' '.join([query, *alternatives]), flags=re.UNICODE)
        terms = all_terms[:40]
        try:
            conn.execute("CREATE VIRTUAL TABLE temp.discovery_index USING fts5(title,body,tags, tokenize='porter unicode61')")
            conn.executemany('INSERT INTO temp.discovery_index(rowid,title,body,tags) VALUES (?,?,?,?)',
                             [(i+1, n['title'], n['body'], n['tags']) for i, n in enumerate(rows)])
            if terms:
                expression = (' OR ' if match == 'any' else ' AND ').join(
                    '"' + t.replace('"', '""') + '"*' for t in terms)
                ranked = [r[0]-1 for r in conn.execute('SELECT rowid FROM temp.discovery_index '
                    'WHERE discovery_index MATCH ? ORDER BY bm25(discovery_index,4.0,1.0,3.0),rowid', (expression,))]
            else:
                ranked = list(range(len(rows)))
        except sqlite3.OperationalError as exc:
            if 'no such module' not in str(exc):
                raise
            method = 'literal token substring fallback; FTS5 unavailable; not semantic'
            ranked = [i for i, n in enumerate(rows) if not terms or
                      (any if match == 'any' else all)(t.casefold() in
                        (n['title']+' '+n['body']+' '+n['tags']).casefold() for t in terms)]
        excluded_buried = excluded_category = 0
        candidates = []
        for index in ranked:
            n = rows[index]
            buried = n['strength'] is not None and n['strength'] < cart.setting('surface_threshold')
            if buried and not include_buried:
                excluded_buried += 1
                continue
            if category and n['category'] != category:
                excluded_category += 1
                continue
            candidates.append({'id': n['note_id'], 'title': n['title'][:160],
                'excerpt': n['body'][:200], 'category': n['category'], 'buried': buried,
                'historical': n['historical'], 'revision': n['revision'],
                'record_type': n.get('record_type', 'note')})
        result = {'ok': True, 'method': method, 'revision': current, 'candidates': [],
                  'match': match, 'terms_used': len(terms), 'omitted_query_terms': max(0,len(all_terms)-40),
                  'matched': len(candidates), 'excluded_buried': excluded_buried,
                  'excluded_category': excluded_category, 'history_searched': include_history,
                  'next_offset': offset, 'more_available': len(candidates)-min(offset,len(candidates)),
                  'text': 'Broaden with alternate words or include_buried/include_history; narrow by category or match=all. '
                          'Use inspect for current notes, reconstruct for historical notes, '
                          'history(after=revision-1,limit=1) for change records.'}
        for item in candidates[offset:offset+limit]:
            result['candidates'].append(item)
            result['next_offset'] += 1
            result['more_available'] -= 1
            if len(json.dumps(result, ensure_ascii=False)) > max_chars:
                result['candidates'].pop()
                result['next_offset'] -= 1
                result['more_available'] += 1
                break
        if len(json.dumps(result, ensure_ascii=False)) > max_chars:
            raise MemoryFabricError('discovery envelope exceeds result budget; increase allowance')
        return result
    finally:
        conn.execute('RELEASE discovery_read')
