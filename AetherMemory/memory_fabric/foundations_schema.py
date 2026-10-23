"""Schema 4: note facets, ordered threads, layouts and a sealed lifecycle journal.

Only authored state participates in memory row history. Host lifecycle facts have
their own immutable sequence, so recording a turn does not stale a memory edit.
"""

FACETS = {
    'projects': ('project_id', {'external_project_id': None, 'project_source_ref': None,
                               'source_system': None}),
    'scaffolds': ('scaffold_id', {'supersedes_scaffold_id': None, 'continuation_note_id': None,
                                'source_version': None, 'source_sha256': None}),
    'guides': ('guide_id', {'purpose': '', 'content_mode': 'authored', 'source_version': None,
                          'source_sha256': None, 'origin_author': None, 'builtin_key': None}),
    'teachframes': ('teachframe_id', {'applicability': '', 'content_mode': 'authored',
                                    'format_name': 'text', 'format_version': None,
                                    'source_version': None, 'source_sha256': None,
                                    'origin_author': None, 'builtin_key': None}),
    'threads': ('thread_id', {'state': 'open'}),
}
AUTHORED_TABLES = tuple(FACETS) + ('thread_elements', 'dashboards', 'dashboard_elements')
TABLES = AUTHORED_TABLES + ('session_events',)
DDL = []
for table, (key, fields) in FACETS.items():
    columns = [f'{key} TEXT PRIMARY KEY REFERENCES notes(note_id)']
    for name, default in fields.items():
        column = f'{name} TEXT' + (' NOT NULL' if default is not None else '')
        if name == 'supersedes_scaffold_id':
            column += ' REFERENCES scaffolds(scaffold_id)'
        if name == 'continuation_note_id':
            column += ' REFERENCES notes(note_id)'
        if name == 'builtin_key':
            column += ' UNIQUE'
        columns.append(column)
    columns += ['updated_at TEXT NOT NULL', 'updated_by TEXT NOT NULL', 'archived_at TEXT']
    if table == 'projects':
        columns += ['CHECK ((source_system IS NULL) = (external_project_id IS NULL))',
                    'UNIQUE(source_system,external_project_id)']
    if 'content_mode' in fields:
        columns += ["CHECK(content_mode IN ('authored','adopted_copy','external_reference'))"]
    if table == 'threads':
        columns += ["CHECK(state IN ('open','paused','concluded'))"]
    DDL.append(f'CREATE TABLE {table} (' + ','.join(columns) + ')')
    DDL.append(f'CREATE INDEX {table}_catalog ON {table}(archived_at,updated_at)')

DDL += [
    """CREATE TABLE thread_elements (
        element_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(thread_id),
        parent_element_id TEXT, note_id TEXT NOT NULL REFERENCES notes(note_id),
        position INTEGER NOT NULL CHECK(position>=0), created_at TEXT NOT NULL,
        created_by TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL,
        archived_at TEXT, UNIQUE(thread_id,element_id),
        FOREIGN KEY(thread_id,parent_element_id) REFERENCES thread_elements(thread_id,element_id),
        CHECK(parent_element_id IS NULL OR parent_element_id!=element_id))""",
    'CREATE INDEX thread_siblings ON thread_elements(thread_id,parent_element_id,position,created_at,element_id)',
    'CREATE INDEX thread_note ON thread_elements(note_id)',
    """CREATE TABLE dashboards (
        dashboard_id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
        description TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
        updated_at TEXT NOT NULL, updated_by TEXT NOT NULL, archived_at TEXT)""",
    """CREATE TABLE dashboard_elements (
        element_id TEXT PRIMARY KEY, dashboard_id TEXT NOT NULL REFERENCES dashboards(dashboard_id),
        name TEXT NOT NULL, display_title TEXT NOT NULL, position INTEGER NOT NULL CHECK(position>=0),
        source_kind TEXT NOT NULL CHECK(source_kind IN ('literal','note','provider')),
        element_text TEXT, note_id TEXT REFERENCES notes(note_id), provider_key TEXT,
        parameters_json TEXT NOT NULL DEFAULT '{}', enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
        created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL,
        UNIQUE(dashboard_id,name),
        CHECK((source_kind='literal' AND element_text IS NOT NULL AND note_id IS NULL AND provider_key IS NULL)
           OR (source_kind='note' AND note_id IS NOT NULL AND element_text IS NULL AND provider_key IS NULL)
           OR (source_kind='provider' AND provider_key IS NOT NULL AND element_text IS NULL AND note_id IS NULL)))""",
    'CREATE INDEX dashboard_order ON dashboard_elements(dashboard_id,position,created_at,element_id)',
    """CREATE TABLE session_events (
        event_seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
        session_id TEXT NOT NULL REFERENCES session_events(event_id), kind TEXT NOT NULL
          CHECK(kind IN ('opened','closure_started','closure_failed','closed','interrupted','recovery',
                        'project_work','scaffold_started','scaffold_ended')),
        occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL, source_system TEXT NOT NULL,
        source_event_id TEXT NOT NULL, source_ref TEXT,
        previous_session_id TEXT REFERENCES session_events(event_id), closure_id TEXT,
        run_id TEXT REFERENCES session_events(event_id), project_id TEXT REFERENCES projects(project_id),
        scaffold_id TEXT REFERENCES scaffolds(scaffold_id), note_id TEXT REFERENCES notes(note_id),
        scaffold_sha256 TEXT, snapshot_revision INTEGER CHECK(snapshot_revision>=0),
        outcome TEXT CHECK(outcome IN ('completed','incomplete','failed','cancelled','unknown')),
        evidence_ref TEXT, evidence_sha256 TEXT, summary TEXT NOT NULL, details_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE(source_system,source_event_id),
        CHECK(kind!='opened' OR session_id=event_id),
        CHECK(kind='opened' OR previous_session_id IS NULL),
        CHECK(previous_session_id IS NULL OR previous_session_id!=session_id),
        CHECK(kind NOT IN ('closed','closure_failed') OR closure_id IS NOT NULL),
        CHECK(kind!='project_work' OR project_id IS NOT NULL),
        CHECK((kind='scaffold_started' AND run_id=event_id AND scaffold_id IS NOT NULL
                   AND scaffold_sha256 IS NOT NULL AND snapshot_revision IS NOT NULL AND outcome IS NULL)
           OR (kind='scaffold_ended' AND run_id IS NOT NULL AND scaffold_id IS NOT NULL AND outcome IS NOT NULL
                   AND scaffold_sha256 IS NULL AND snapshot_revision IS NULL)
           OR (kind NOT IN ('scaffold_started','scaffold_ended') AND run_id IS NULL
                   AND scaffold_sha256 IS NULL AND snapshot_revision IS NULL AND outcome IS NULL)))""",
    "CREATE UNIQUE INDEX session_open ON session_events(session_id) WHERE kind='opened'",
    "CREATE UNIQUE INDEX session_closed ON session_events(session_id) WHERE kind='closed'",
    "CREATE UNIQUE INDEX run_ended ON session_events(run_id) WHERE kind='scaffold_ended'",
    'CREATE INDEX session_sequence ON session_events(session_id,event_seq)',
    'CREATE INDEX session_kind ON session_events(kind,occurred_at,event_seq)',
    'CREATE INDEX session_project ON session_events(project_id,occurred_at,event_seq)',
    'CREATE INDEX session_scaffold ON session_events(scaffold_id,occurred_at,event_seq)',
    'CREATE INDEX session_run ON session_events(run_id)',
]
