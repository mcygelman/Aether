"""Discoverable agent operations for the memory foundations and Welcome Dashboard."""
from __future__ import annotations

import json

from . import dashboard, lifecycle, query, records
from .errors import MemoryFabricError

READ_ACTIONS = ('schema', 'list', 'inspect', 'associations', 'thread_read', 'dashboard', 'layout', 'events')
WRITE_ACTIONS = ('create', 'revise', 'thread_edit', 'dashboard_edit', 'project_work', 'scaffold_start', 'scaffold_end')
ACTIONS = READ_ACTIONS + WRITE_ACTIONS


def describe(cart, table=None):
    result = query.describe(cart, table)
    if table is not None:
        return result
    result['workspace_actions'] = {
        'list': {'record_type': 'projects|scaffolds|guides|teachframes|threads', 'options': 'offset,limit,include_archived'},
        'inspect': {'record_type': '...', 'record_id': 'note ID', 'options': 'offset,limit (body characters),at_revision'},
        'create': {'record_type': '...', 'title': '...', 'body': '...', 'kind': 'project_fact',
                   'fields': 'typed metadata object', 'tags': [], 'operation_id': 'stable retry ID'},
        'revise': {'record_type': '...', 'record_id': '...', 'fields': {}, 'note': {}, 'archived': False,
                   'operation_id': 'stable retry ID', 'expected_revision': 'optional observed revision'},
        'associations': {'note_id': '...'},
        'thread_edit': {'thread_id': '...', 'element_id': 'optional existing ID', 'note_id': '...',
                        'parent_element_id': None, 'position': 0, 'archived': False, 'operation_id': '...'},
        'thread_read': {'thread_id': '...', 'spine': True, 'leaf_element_id': 'optional'},
        'dashboard': {'name': 'welcome'}, 'layout': {'name': 'welcome'},
        'dashboard_edit': {'name': 'welcome', 'elements': 'complete desired element array from layout',
                           'reset': 'optional true: restore builtin Welcome, without other edits', 'operation_id': '...'},
        'events': {'after': 0, 'limit': 100},
        'project_work': {'project_id': '...', 'operation_id': '...'},
        'scaffold_start': {'scaffold_id': '...', 'operation_id': '...'},
        'scaffold_end': {'run_id': '...', 'outcome': 'completed|incomplete|failed|cancelled|unknown', 'operation_id': '...'},
    }
    result['facet_fields'] = {name: fields for name, (_, fields) in records.FACETS.items()}
    result['providers'] = dict(dashboard.PROVIDERS)
    return result


def execute(engine, action, arguments=None):
    args = dict(arguments or {})
    cart = engine.cart
    if action not in ACTIONS:
        raise MemoryFabricError(f'workspace action must be one of {ACTIONS}')
    if action in WRITE_ACTIONS and cart.read_only:
        raise MemoryFabricError('reference cartridge is read-only')
    if action == 'schema':
        return describe(cart, **args)
    if action == 'dashboard':
        return dashboard.retrieve(cart, session_id=getattr(engine, 'logical_session_id', None), **args)
    if action == 'layout':
        with cart.read_snapshot():
            return dashboard.layout(cart, **args)
    if action == 'events':
        return lifecycle.events(cart, **args)
    if action in ('project_work', 'scaffold_start'):
        sid = getattr(engine, 'logical_session_id', None)
        if not sid:
            raise MemoryFabricError('host has not attached a logical working session')
        if action == 'scaffold_start':
            return lifecycle.start_scaffold(cart, sid, **args)
        operation = args.pop('operation_id', None)
        if not isinstance(operation, str) or not operation.strip():
            raise MemoryFabricError('project_work requires a stable operation_id')
        return lifecycle.append(cart, 'project_work', sid, source_system='agent',
                                source_event_id=operation, summary='Agent selected project context', **args)
    if action == 'scaffold_end':
        start = lifecycle.event(cart, args.get('run_id'))
        if start['session_id'] != getattr(engine, 'logical_session_id', None):
            raise MemoryFabricError('scaffold attempt belongs to a different logical session')
        return lifecycle.end_scaffold(cart, **args)
    functions = {'list': records.listing, 'inspect': records.inspect, 'associations': records.associations,
                 'create': records.create, 'revise': records.revise, 'thread_edit': records.thread_edit,
                 'thread_read': records.thread_read, 'dashboard_edit': dashboard.edit}
    return functions[action](cart, **args)


ECOSYSTEM = '''Aether is an ecosystem for persistent agents. Follow the current operator request and Aether First Principles: pave useful roads, preserve independent judgment, and keep identity and memory attributable.
Your Welcome Dashboard shows prior closure evidence, both recent scaffold references, your last project, and available resources. A listed scaffold is context, not permission to execute it. None means a successful empty lookup; unknown or unavailable means evidence is missing.
Use memory_workspace(action="schema") to discover the actual tables and operations. Use action="list" with record_type, then action="inspect" with record_id and body offset/limit. Projects, Scaffolds, Guides, TeachFrames and Threads are ordinary notes with extra structure; their shared IDs participate in weighted links. Threads retain branch order independently of decay.
memory_recall(tags=[],seed_note_ids=[project_id]) explores associations without changing weights. memory_load(ids=[...]) deliberately refreshes notes and reinforces existing co-loaded links. Dashboard refresh, SQL and inspection do not reinforce. Choose what to load.
memory_query accepts your own parameterized SELECT or CTE, plus offset/limit. Writes go through engine operations with stable operation_id values for retries. Keep your own prose and source attribution. Existing categories and strata describe content; profiles determine decay. External references may not travel with your cartridge.
At closure, use the host's established closure path. A closed database connection is not personal closure. A missing closing event does not prove that a worker died. Consult existing evidence and the operator when judgment is needed; this guide creates no compulsory tour or health ritual.'''


SEED_AUTHOR = 'Aether'
TEACHFRAME = ('Aether orientation TeachFrame\n\nSituation: You arrive with a current request and a Welcome Dashboard.\n'
              'Practice: Identify the current request, distinguish empty data from missing evidence, select a resource, '
              'and choose which associated memories to load.\n'
              'Example: A previous project remains visible during an unrelated request. Keep its context available '
              'while doing the present work. An open prior session needs evidence, not an assumed failure.\n\n' + ECOSYSTEM)
SEED_BODIES = {'guides': ECOSYSTEM, 'teachframes': TEACHFRAME}


def _seed_was_adapted(cart, table, note_id):
    """Retained edits count even when an owner later restores the supplied body.

    Loading/refreshing a seed is not authorship. Inspect only histories mentioning
    this seed, and distinguish content/tag/facet edits from strength bookkeeping.
    """
    key, fields = records.table_spec(table)
    for row in cart.conn.execute('SELECT h.payload,c.operation FROM history_data h JOIN memory_changes c USING(history_id) WHERE instr(h.payload, ?) > 0',
                                 (json.dumps(note_id),)):
        changes = json.loads(row[0])
        notes = changes.get('notes', {})
        before = next((n for n in notes.get('before', []) if n['note_id'] == note_id), None)
        after = next((n for n in notes.get('after', []) if n['note_id'] == note_id), None)
        if before and after:
            if any(before.get(k) != after.get(k) for k in ('title', 'body', 'source_ref')):
                return True
            if row['operation'] == 'record_revise' and before['written_by'] == SEED_AUTHOR and after['written_by'] == cart.agent_id:
                return True
        # Initial creation includes tags; adding/removing tags later is an adaptation.
        if not (after and not before):
            tags = changes.get('tags', {})
            old = {t['tag'] for t in tags.get('before', []) if t['note_id'] == note_id}
            new = {t['tag'] for t in tags.get('after', []) if t['note_id'] == note_id}
            if old != new:
                return True
        facets = changes.get(table, {})
        old = next((f for f in facets.get('before', []) if f[key] == note_id), None)
        new = next((f for f in facets.get('after', []) if f[key] == note_id), None)
        if old and new and any(old.get(k) != new.get(k) for k in (*fields, 'archived_at')):
            return True
    return False


def seed(cart):
    """Add missing supplied resources; never overwrite or resurrect owner edits.

    A seed is Aether's note, not the mind's (Fable, 2026-10-22): the near-duplicate rule and every memory
    score count a mind's lessons by author. A seed the first schema-4 engine filed under the mind's name is
    given back to Aether here, on a writable open, only while its words are still Aether's; an edited copy is
    the mind's adaptation and keeps its author."""
    for table in ('guides', 'teachframes'):
        key = 'guide_id' if table == 'guides' else 'teachframe_id'
        row = cart.conn.execute(f'SELECT {key} AS id FROM {table} WHERE builtin_key=?', ('aether_ecosystem',)).fetchone()
        if row:
            note = cart.conn.execute('SELECT written_by, body FROM notes WHERE note_id=?', (row['id'],)).fetchone()
            if (note and note['written_by'] == cart.agent_id and note['body'] == SEED_BODIES[table]
                    and not _seed_was_adapted(cart, table, row['id'])):
                cart.conn.execute('UPDATE notes SET written_by=? WHERE note_id=?', (SEED_AUTHOR, row['id']))
            continue
        fields = {'builtin_key': 'aether_ecosystem', 'origin_author': SEED_AUTHOR, 'content_mode': 'adopted_copy',
                  'source_version': 'memory-foundations-v1'}
        if table == 'guides':
            fields['purpose'] = 'Discover Aether memory and orientation tools.'
        else:
            fields.update(applicability='Cold arrival and deliberate memory use', format_name='text', format_version='1')
        records.create(cart, table, title='Aether Ecosystem ' + ('Guide' if table == 'guides' else 'TeachFrame'),
                       body=SEED_BODIES[table], kind='standing_lesson', tags=['aether ecosystem', 'memory usage'],
                       fields=fields, source_ref='aether:memory-foundations/ecosystem/v1',
                       operation_id='builtin:memory-foundations:' + table + ':v1', written_by=SEED_AUTHOR)
