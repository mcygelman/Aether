"""A shared read-only projection for agent and operator, with owner-editable layouts."""
from __future__ import annotations

import html
import json
import hashlib
import sqlite3
from urllib.parse import quote

from . import fabric, history, lifecycle, records, schema
from .errors import MemoryFabricError

# Continuity first, followed by all orientation catalogs. Every enabled provider
# remains visible at wake; the order does not make any card mandatory to act on.
PROVIDERS = [('previous_closure', 'Previous closure'), ('next_self_note', 'From your previous session'),
             ('last_project', 'Last Project Worked On'),
             ('last_unexecuted_scaffold', 'Last Unexecuted Scaffold'),
             ('last_executed_scaffold', 'Last Executed Scaffold'), ('guides', 'Available Guides'),
             ('teachframes', 'Available TeachFrames'),
             ('projects', 'Available Projects'), ('note_types', 'Available Note Types')]
CONTINUITY = ('previous_closure', 'next_self_note', 'last_project', 'last_unexecuted_scaffold', 'last_executed_scaffold')
CATALOGS = ('guides', 'teachframes', 'projects', 'note_types')


def layout(cart, name='welcome'):
    row = cart.conn.execute('SELECT * FROM dashboards WHERE name=?', (name,)).fetchone()
    if row:
        result = dict(row)
        result['origin'] = 'stored'
        result['elements'] = [dict(r) for r in cart.conn.execute('SELECT * FROM dashboard_elements '
            'WHERE dashboard_id=? ORDER BY position,created_at,element_id', (row['dashboard_id'],))]
        for element in result['elements']:
            element['parameters'] = json.loads(element.pop('parameters_json'))
        return result
    if name != 'welcome':
        raise MemoryFabricError('unknown dashboard: ' + name)
    return {'name': name, 'title': 'Welcome', 'description': 'Orientation and retrieval, as of the displayed time.',
            'dashboard_id': 'builtin:welcome', 'origin': 'builtin', 'version': 1, 'archived_at': None,
            'elements': [dict(element_id='builtin:' + key, name=key, display_title=title, position=i,
                              source_kind='provider', provider_key=key, note_id=None, element_text=None,
                              enabled=1, parameters={}) for i, (key, title) in enumerate(PROVIDERS)]}


def edit(cart, name='welcome', *, title=None, description=None, elements=None, archived=None, reset=False,
         operation_id=None, expected_revision=None):
    request = dict(name=name, title=title, description=description, elements=elements, archived=archived, reset=reset)
    def write():
        existing = cart.conn.execute('SELECT * FROM dashboards WHERE name=?', (name,)).fetchone()
        if reset:
            if name != 'welcome' or any(v is not None for v in (title, description, elements, archived)):
                raise MemoryFabricError('reset restores builtin Welcome; supply no other edits')
            if existing:
                cart.conn.execute('DELETE FROM dashboard_elements WHERE dashboard_id=?', (existing['dashboard_id'],))
                cart.conn.execute('DELETE FROM dashboards WHERE dashboard_id=?', (existing['dashboard_id'],))
            return {'name': name, 'origin': 'builtin'}
        old = layout(cart, name) if existing or name == 'welcome' else dict(title=name, description='', elements=[])
        did = existing['dashboard_id'] if existing else records.identity('dashboard')
        now = cart.clock.now()
        if existing:
            cart.conn.execute('UPDATE dashboards SET title=?,description=?,updated_at=?,updated_by=?,archived_at=? WHERE dashboard_id=?',
                (title if title is not None else old['title'], description if description is not None else old['description'],
                 now, cart.actor, (now if archived else None) if archived is not None else old.get('archived_at'), did))
        else:
            cart.conn.execute('INSERT INTO dashboards VALUES (?,?,?,?,?,?,?,?,?)',
                (did, name, title if title is not None else old['title'], description if description is not None else old['description'],
                 now, cart.actor, now, cart.actor, now if archived else None))
        chosen = elements if elements is not None else old['elements']
        if not isinstance(chosen, list):
            raise MemoryFabricError('elements must be an ordered array of element objects')
        # Replacing a layout is an authored edit captured by row history, not memory deletion.
        prior = {r['name']: dict(r) for r in cart.conn.execute('SELECT * FROM dashboard_elements WHERE dashboard_id=?', (did,))}
        cart.conn.execute('DELETE FROM dashboard_elements WHERE dashboard_id=?', (did,))
        for index, item in enumerate(chosen):
            if not isinstance(item, dict) or not item.get('name'):
                raise MemoryFabricError('each dashboard element needs a name and source_kind')
            previous = prior.get(item['name'], {})
            eid = previous.get('element_id') or records.identity('element')
            params = item.get('parameters', {})
            if not isinstance(params, dict):
                raise MemoryFabricError('element parameters must be a data object')
            cart.conn.execute('INSERT INTO dashboard_elements VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (eid, did, item['name'], item.get('display_title', item['name']), item.get('position', index),
                 item.get('source_kind'), item.get('element_text'), item.get('note_id'), item.get('provider_key'),
                 records.canonical(params), int(item.get('enabled', True)), previous.get('created_at', now),
                 previous.get('created_by', cart.actor), now, cart.actor))
        return {'dashboard_id': did, 'name': name}
    return records.mutate(cart, 'dashboard_edit', request, write, operation_id=operation_id,
                          expected_revision=expected_revision)


def note_excerpt(cart, note_id, limit=800):
    note = fabric._require_note(cart, note_id)
    return {'note_id': note_id, 'title': note['title'], 'text': note['body'][:limit],
            'total_chars': len(note['body']), 'returned_chars': min(limit, len(note['body'])),
            'created_at': note['created_at'], 'source_ref': note['source_ref'],
            'full_read': {'tool': 'memory_query', 'sql': 'SELECT body FROM notes WHERE note_id=:id',
                          'parameters': {'id': note_id}}}


def _result(value):
    return {'status': 'empty' if value is None else 'ok', 'value': value}


def provider(cart, key, params, session_id, cache=None):
    if key in ('projects', 'guides', 'teachframes'):
        found = records.listing(cart, key, limit=params.get('limit', 8), offset=params.get('offset', 0))
        found['list_action'] = {'tool': 'memory_workspace', 'action': 'list', 'record_type': key}
        return _result(found if found['total'] else None)
    if key == 'note_types':
        return _result({'kinds': [r[0] for r in cart.conn.execute('SELECT profile FROM profiles UNION SELECT profile FROM notes ORDER BY 1')],
                        'categories': list(schema.CATEGORIES), 'strata': list(schema.STRATA)})
    if key == 'previous_closure':
        return lifecycle.previous_closure(cart, session_id)
    if key == 'last_project':
        row = cart.conn.execute("SELECT e.project_id,n.title,e.occurred_at,e.session_id,e.event_id FROM session_events e "
            "JOIN notes n ON n.note_id=e.project_id WHERE e.kind='project_work' ORDER BY e.occurred_at DESC,e.event_seq DESC LIMIT 1").fetchone()
        return _result(dict(row) if row else None)
    if key in ('last_executed_scaffold', 'last_unexecuted_scaffold'):
        if cache is not None and 'scaffolds' not in cache:
            cache['scaffolds'] = lifecycle.scaffold_status(cart)
        status = cache['scaffolds'] if cache is not None else lifecycle.scaffold_status(cart)
        item = status['executed' if key == 'last_executed_scaffold' else 'unexecuted']
        result = _result(item)
        if key == 'last_unexecuted_scaffold' and status['uncertain']:
            result['uncertain_sources'] = status['uncertain']
            result['status'] = 'unknown'
        return result
    if key == 'next_self_note':
        if params.get('note_id'):
            return _result(dict(note_excerpt(cart, params['note_id']), selection='Explicit dashboard note selection'))
        if not session_id:
            return {'status': 'unknown', 'value': None, 'reason': 'Current session context is needed.'}
        opening = lifecycle.event(cart, session_id)
        previous = opening.get('previous_session_id')
        selection = 'Known previous session'
        if not previous:
            projection = lifecycle.previous_closure(cart, session_id)
            latest = (projection.get('value') or {}).get('latest_verified_closure')
            if not latest or latest['state'] != 'PASS':
                return projection
            previous = latest['session_id']
            selection = 'Latest verified closure; predecessor unknown'
        closed = cart.conn.execute("SELECT event_id FROM session_events WHERE session_id=? AND kind='closed'", (previous,)).fetchone()
        if not closed:
            return {'status': 'unknown', 'value': None, 'reason': 'No verified prior closure carry.'}
        evidence = lifecycle.event(cart, closed[0])
        nid = evidence['note_id'] or evidence['details'].get('carry_note_id')
        if nid:
            value = note_excerpt(cart, nid)
            expected = evidence['details'].get('carry_sha256')
            revision = evidence['details'].get('carry_revision')
            if expected and hashlib.sha256(fabric._require_note(cart, nid)['body'].encode('utf-8')).hexdigest() != expected:
                historic = history.reconstruct(cart, revision) if revision is not None else {}
                saved = next((n for n in historic.get('notes', []) if n['note_id'] == nid), None)
                if not saved or hashlib.sha256(saved['body'].encode('utf-8')).hexdigest() != expected:
                    return {'status': 'unavailable', 'value': None, 'reason': 'Retained carry does not match closure evidence.'}
                value.update(text=saved['body'][:800], total_chars=len(saved['body']), returned_chars=min(800, len(saved['body'])),
                             title=saved['title'], at_revision=revision,
                             full_read={'tool': 'memory_maintain', 'action': 'reconstruct', 'id': nid, 'at_revision': revision})
            value['closure_at'] = evidence['occurred_at']
            value.update(selection=selection, session_id=previous)
            return _result(value)
        # Readers never follow arbitrary paths supplied through a model-authored row.
        if evidence['details'].get('carry_ref'):
            return {'status': 'unavailable', 'value': {'source_ref': evidence['details']['carry_ref'],
                    'sha256': evidence['details'].get('carry_sha256')},
                    'reason': 'Host must resolve and verify this retained carry reference.'}
        return _result(None)
    raise MemoryFabricError('unavailable dashboard provider: ' + key)


def retrieve(cart, name='welcome', *, session_id=None):
    with cart.read_snapshot():
        config = layout(cart, name)
        now = cart.sync_clock()
        result = {'name': name, 'title': config['title'], 'origin': config['origin'],
                  'session_id': session_id,
                  'retrieved_at': now, 'revision': history.revision(cart),
                  'event_seq': cart.conn.execute('SELECT COALESCE(MAX(event_seq),0) FROM session_events').fetchone()[0],
                  'archived': bool(config['archived_at']), 'elements': []}
        if result['archived']:
            return result
        cache = {}
        for element in config['elements']:
            if not element['enabled']:
                continue
            try:
                if element['source_kind'] == 'provider':
                    content = provider(cart, element['provider_key'], element['parameters'], session_id, cache)
                elif element['source_kind'] == 'note':
                    content = _result(note_excerpt(cart, element['note_id']))
                else:
                    content = _result(element['element_text'])
            except (MemoryFabricError, ValueError, KeyError, sqlite3.Error) as exc:
                content = {'status': 'unavailable', 'value': None, 'error': str(exc)}
            title = element['display_title']
            if element.get('provider_key') == 'next_self_note' and isinstance(content.get('value'), dict) and \
                    content['value'].get('selection') == 'Latest verified closure; predecessor unknown':
                title = 'From latest verified closure (predecessor unknown)'
            result['elements'].append(dict(content, name=element['name'], title=title, retrieved_at=now))
        return result


def _stamp(value, *keys):
    return next((value[k] for k in keys if value.get(k)), '')


def _card_lines(element):
    """One card as concise prose; the second item is its existing carry excerpt."""
    title, status, value = element['title'], element['status'], element.get('value')
    why = element.get('reason') or element.get('error') or ''
    if status == 'empty':
        return [f'{title}: none'], None
    if value is None:
        return [f'{title}: {status}' + (f'; {why}' if why else '')], None
    if isinstance(value, str):
        return [f'{title}: {value}'], None
    if isinstance(value, dict) and 'records' in value:
        kind = value.get('list_action', {}).get('record_type', '')
        names = ', '.join(r['title'] + ' [' + str(next((r[k] for k in ('project_id', 'guide_id', 'teachframe_id') if k in r), '?')) + ']' for r in value['records'])
        return [f"{title}: {value['total']} ({names}); ask memory_workspace list record_type={kind}" +
                (f" offset={value['next_offset']} for next page" if value.get('next_offset') is not None else '')], None
    if isinstance(value, dict) and 'text' in value:
        head = f'{title}'
        if value.get('selection'):
            head += f" ({value['selection']}"
            head += f", closed {value['closure_at']})" if value.get('closure_at') else ')'
        head += f": {value.get('title', '')}; whole note {value['note_id']}"
        if value.get('at_revision') is not None:
            head += f" at revision {value['at_revision']} (memory_maintain reconstruct)"
        if value.get('total_chars', 0) > value.get('returned_chars', 0):
            head += f"; excerpt {value['returned_chars']}/{value['total_chars']} characters"
        return [head + ':'], value['text']
    if isinstance(value, dict) and 'state' in value:
        line = f"{title}: {value['state']}"
        if value.get('occurred_at'):
            line += f" at {value['occurred_at']}"
        if value.get('session_id'):
            line += f" (session {value['session_id']})"
        lines = [line]
        if 'unresolved_count' in value:
            unresolved = f"  {value['unresolved_count']} sessions without a recorded close"
            if value.get('unresolved_sessions'):
                unresolved += '; most recent: ' + ', '.join(value['unresolved_sessions'])
            lines.append(unresolved)
        if value['state'] != 'PASS' and (value.get('reason') or why):
            lines.append('  ' + (value.get('reason') or why))
        if value.get('candidate_count') is not None:
            lines.append(f"  {value['candidate_count']} possible prior sessions; recent: " + ', '.join(value.get('candidates', [])))
        latest = value.get('latest_verified_closure')
        if latest:
            lines.append(f"  Latest verified closure: {latest['state']}; session {latest['session_id']}; {latest.get('occurred_at', '')}")
        if value.get('history_read'):
            lines.append('  Full session history: memory_workspace(action="events", after=0, limit=100); continue after the last event_seq.')
        return lines, None
    if isinstance(value, dict) and 'title' in value:
        line = f"{title}: {value['title']}"
        rid = value.get('scaffold_id') or value.get('project_id') or value.get('note_id')
        if rid:
            line += f" [{rid}]"
        if value.get('snapshot_revision') is not None:
            line += f"; inspect at_revision={value['snapshot_revision']}"
        stamp = _stamp(value, 'completed_at', 'authored_at', 'occurred_at')
        if stamp:
            line += f' ({stamp})'
        if value.get('current_content_differs'):
            line += '; its content has changed since that attempt'
        if value.get('attempts'):
            line += '; attempts: ' + ', '.join(f"{a['run_id']} ({a['outcome']})" for a in value['attempts'])
        if status != 'ok' and why:
            line += f'; {why}'
        return [line], None
    if isinstance(value, dict) and 'kinds' in value:
        return [f"{title}: {', '.join(value['kinds'])}",
                '  Categories: ' + ', '.join(value.get('categories', [])) + '; strata: ' + ', '.join(value.get('strata', []))], None
    return [f'{title}: ' + json.dumps(value, ensure_ascii=False, sort_keys=True)], None


FOOTER = ('Follow the current request. These references do not resume work automatically. '
          'Inspection/refresh changes no memory strength; memory_load is an intentional pull. '
          'memory_workspace(action="dashboard") refreshes this view; action="schema" explains access.')


def render_text(result, budget_tokens=None):
    """Concise Welcome with every enabled card and its existing provider excerpt.

    The former budget argument is accepted for older callers but has no effect.
    Persisted welcome_budget_tokens settings likewise never hide orientation.
    Complete request capacity is handled by the host, not by dropping cards.
    """
    out = [result['title'] + ' — retrieved ' + result['retrieved_at']]
    for element in result['elements']:
        lines, carry = _card_lines(element)
        if element['status'] not in ('ok', 'empty') and element.get('value') is not None:
            out.append(f"{element['title']}: {element['status']}" +
                       (f"; {element.get('reason') or element.get('error')}" if element.get('reason') or element.get('error') else ''))
        out.extend(lines)
        if carry is not None:
            out.append('  ' + carry.replace('\n', '\n  '))
        if element.get('uncertain_sources'):
            out.append('  Uncertain scaffold sources: ' + ', '.join(
                f"{r['title']} [{r['scaffold_id']}]" for r in element['uncertain_sources']))
    out.append(FOOTER)
    return '\n'.join(out)


def _note_link(note_id, title, revision=None):
    suffix = '&revision=' + str(revision) if revision is not None else ''
    return '<a href="/note?id=' + quote(str(note_id), safe='') + suffix + '">' + html.escape(str(title)) + '</a>'


def _card_value(item):
    value = item.get('value')
    if item['status'] == 'empty':
        return '<p class="empty">None</p>'
    if value is None:
        return '<p>Evidence unavailable</p>'
    if isinstance(value, str):
        return '<p class="prose">' + html.escape(value) + '</p>'
    if isinstance(value, dict) and 'records' in value:
        rows = []
        for record in value['records']:
            rid = next((record[k] for k in ('project_id', 'guide_id', 'teachframe_id') if k in record), None)
            rows.append('<li>' + _note_link(rid, record['title']) + '</li>')
        paging = '<p>' + str(len(rows)) + ' of ' + str(value['total']) + ' records</p>'
        kind = value.get('list_action', {}).get('record_type')
        if kind:
            paging += '<p><a href="/catalog?type=' + quote(kind, safe='') + '">Browse all</a></p>'
        return '<ul>' + ''.join(rows) + '</ul>' + paging
    if isinstance(value, dict) and 'text' in value:
        return '<p>' + html.escape(value.get('selection', '')) + '</p><p class="prose">' + html.escape(value['text']) + '</p>' + _note_link(
            value['note_id'], 'Read full note', value.get('at_revision'))
    if isinstance(value, dict) and 'title' in value:
        rid = value.get('scaffold_id') or value.get('project_id')
        lead = _note_link(rid, value['title'], value.get('snapshot_revision')) if rid else html.escape(value['title'])
        stamp = value.get('completed_at') or value.get('authored_at') or value.get('occurred_at')
        return '<p>' + lead + '</p><p>' + html.escape(stamp or '') + '</p>' + _details(value)
    if isinstance(value, dict) and 'state' in value:
        summary = ''
        if 'unresolved_count' in value:
            summary = '<p>' + str(value['unresolved_count']) + ' sessions without a recorded close.</p>'
            if value['unresolved_sessions']:
                summary += '<p>Most recent references:</p><ul>' + ''.join(
                    '<li>' + html.escape(sid) + '</li>' for sid in value['unresolved_sessions']) + '</ul>'
            summary += '<p><a href="/history">Inspect full session history</a></p>'
        return '<p class="state">' + html.escape(value['state']) + '</p>' + summary + _details(value)
    if isinstance(value, dict) and 'kinds' in value:
        return '<p>' + html.escape(', '.join(value['kinds'])) + '</p>' + _details(value)
    return _details(value)


def _details(value):
    return '<details><summary>Evidence and references</summary><pre>' + html.escape(
        json.dumps(value, ensure_ascii=False, indent=2)) + '</pre></details>'


STYLE = ('body{font:16px system-ui;max-width:1100px;margin:32px auto;padding:0 18px;background:#101820;color:#eaf1f6}'
         'main{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;margin-top:24px}'
         'section{background:#1c2a36;padding:20px;border-radius:12px}h2{font-size:18px;margin-top:0}'
         'pre,.prose{white-space:pre-wrap;overflow-wrap:anywhere}small,.meta{color:#a9c9dc}'
         'a{color:#8edcce}li{margin:10px 0}button,input{font:inherit;padding:8px;border-radius:5px}'
         '.state{font-size:25px}.empty{color:#b9c8d0}details{font-size:13px;margin-top:16px}'
         'summary{cursor:pointer}input{max-width:90%;width:420px}article{line-height:1.6}')


def render_html(result, *, refresh_url=None):
    blocks = []
    for item in result['elements']:
        blocks.append('<section><h2>' + html.escape(item['title']) + '</h2><small>' + html.escape(item['status']) +
                      '</small>' + _card_value(item) + '<p>' + html.escape(item.get('reason', item.get('error', ''))) + '</p></section>')
    controls = ('<button onclick="location.reload()">Refresh</button><form method="get"><p><label>Session context '
                '<input name="session_id" value="' + html.escape(result.get('session_id') or '', quote=True) +
                '" placeholder="Logical session ID from the agent"></label> <button>View</button></p></form>') if refresh_url else ''
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +         '<title>Welcome Dashboard</title><style>' + STYLE + '</style><h1>' + html.escape(result['title']) +         '</h1><p class="meta">Retrieved ' + html.escape(result['retrieved_at']) + ' · Memory revision ' + str(result['revision']) +         '</p>' + controls + '<main>' + ''.join(blocks) + '</main><p>Read-only orientation. Follow the current request.</p></html>'
