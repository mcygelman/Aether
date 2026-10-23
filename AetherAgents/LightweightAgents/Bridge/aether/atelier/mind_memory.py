"""A mind's memory: its Memory Fabric cartridge, in its home (Fable, 2026-10-19; Michel: "wire cartridges into the
atelier minds now: file closures as lessons and warnings, wake with recall headers instead of a dump").

  <home>/memory/mind.sqlite          the cartridge (one mind, one file; the engine is AetherMemory/memory_fabric)
  <home>/memory/.cartridge.json      {"path": "mind.sqlite", "owner": "<participant id>"}; the path is relative to memory/

What is filed at closure: the mind's closing words, read for their sections ("What I did", "What I learned", "What my
future self should carry"): the did-paragraph becomes a project fact that sinks after the project; each learned bullet a
standing lesson; each carry bullet a safety warning when it warns (never, don't, avoid, trap...) and a lesson otherwise;
the whole text one fact, linked to the lessons so recall from one reaches the others. Words without sections become one
lesson. Nothing is deleted, ever; the engine's decay does the forgetting.

What a wake shows: recall headers chosen by the commission's words (title, kind, strength, one line), never the bodies.
The mind reads what it chooses with load_memory; loading is what keeps a memory alive. A mind without a cartridge keeps
the older folder memory (carry, experiences, index) untouched.

The side log (Michel, 2026-10-19: "a log of changes and actions, just a pointer with minimal tags and text"): one row per
action, in the cartridge file, table `sidelog`, written by the turn from what it already sees (a file written, tests run, a
memory read, a note posted, an item lent, a closure filed) and by the mind itself when it says so (log_action). A row is
when, which commission, what kind of action, a pointer (a file's hash, a note's id, a memory's id), a few tags, one line.
Never the content. Since schema 3 (Opus, 2026-10-19) the log is the engine's own box, memory_fabric.sidelog: sealed like the
mind's memories, not captured into their history, append-only. This module calls the box and keeps its own function names.
A cartridge written by the schema-2 engine is carried forward the engine's way the first time the atelier opens it
(carry_forward); the log the atelier kept in it comes along, every row.

Decisions (Fable's design and Michel's three decisions, 2026-10-19 evening; built by Opus): a ruling is its own kind of
memory, the `decision` profile. Only the operator files one (a "Decision:" or "Decided:" line in the operator's note,
filed by the place into every mind of the commission) or the lead recording the operator's words (its file_decision
tool). A decision binds its project (the commission id without its last one-letter or numeric segment:
harbor-ledger-e -> harbor-ledger), or every project when the operator's words say "standing". The wake shows the
decisions that bind here whole, in their own block, never ranked against warnings. A decision is linked `conceptual`
to the warnings and lessons on its subject, so recalling the lesson shows "(a decision applies: id)"; a later decision
on the same subject and scope is linked `derived` to the earlier one, which is buried and shows "(replaced by id)".
A mind's words that cite a decision ("Operator decision", "the operator decided", "Decided:") are matched against what
is filed and loaded, and the atelier says in the room what the citation rests on, or that it rests on nothing.
Nothing here refuses a mind, holds a note or changes its words.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

POINTER = 'memory/.cartridge.json'
CARTRIDGE = 'mind.sqlite'
_STOP = set('the and for with that this from into your you our are was were will would have has had not but can its '
            'about than then them they there their what when where which while who whom why how all any each few more '
            'most other some such only own same too very just also been being does did done per via one two three'.split())
_SECTION = re.compile(r'^[ \t]*(?:\*\*([A-Za-z][^\n*]{2,70}?)\*\*|#{1,4}[ \t]*([A-Za-z][^\n#]{2,70}))[ \t]*', re.M)   # any bold or # heading, classified by its words
_HEADS = (('did', re.compile(r'\b(did|done|built|delivered|made|achieved)\b', re.I)),
          ('skip', re.compile(r'\b(skip|avoid|not again|don\'?t repeat|waste)', re.I)),
          ('carry', re.compile(r'\b(carry|future self|next self|remember|keep in mind|next time)', re.I)),
          ('learned', re.compile(r'\b(learn|lesson|insight|takeaway|found out)', re.I)))
_CLAUSE = re.compile(r'\b(?:lesson|carry)\s*:\s*([^\n]+)', re.I)
_WARNING = re.compile(r"\b(never|don'?t|do not|avoid|trap|careful|beware|must not|refuse|danger|gotcha|wrong)\b", re.I)
_BULLET = re.compile(r'^\s*(?:[-*•]|\d+[.)])\s+(.*)$')


def words(text: str, limit: int = 8) -> list:
    """Tags from plain words: the commission's letter, a note, a title. Lexical, like the engine's recall."""
    found = [w for w in re.findall(r'[a-z][a-z0-9_\-]{3,}', (text or '').lower()) if w not in _STOP]
    return list(dict.fromkeys(found))[:limit]


# -- the cartridge file --------------------------------------------------------------------------------------------
def pointer(home: Path) -> dict | None:
    path = Path(home) / POINTER
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except ValueError:
        return None
    cart = Path(data.get('path') or CARTRIDGE)
    if not cart.is_absolute():
        cart = path.parent / cart
    return dict(data, path=str(cart))


def create(home: Path, agent_id: str, name: str | None = None) -> dict:
    """A new, empty cartridge for a mind, and the pointer to it. Returns the pointer."""
    from aether.memory.fabric import memory_fabric
    home = Path(home)
    cart = home / 'memory' / CARTRIDGE
    cart.parent.mkdir(parents=True, exist_ok=True)
    if not cart.exists():
        memory_fabric().MemoryEngine.create(str(cart), agent_id, name=name).close()
    (home / POINTER).write_text(json.dumps(dict(path=CARTRIDGE, owner=agent_id), indent=1), encoding='utf-8')
    return pointer(home)


def _open_write(home: Path, session: str):
    from aether.memory.fabric import open_owner_engine
    carry_forward(home)
    p = pointer(home)
    return open_owner_engine(p['path'], owner_id=p['owner'], session_id=session)


def _open_read(home: Path):
    from aether.memory.fabric import open_engine
    try:
        return open_engine(pointer(home)['path'], read_only=True)
    except Exception as exc:
        if 'a migration is required' in str(exc):
            raise ValueError(str(exc) + '; this mind migrates at its next writable activation; preview changes nothing') from exc
        raise


_CURRENT: set = set()   # cartridges already at the engine's schema in this process


def carry_forward(home: Path) -> dict | None:
    """A cartridge written by an older engine is carried to this engine's schema the engine's own way (migrations.migrate:
    the seal checked over the tables its version sealed, a full backup, one transaction, a reseal) the first time the
    atelier opens it, and the side log says so. It is not a question put to the mind and not a refusal: the memories are
    unchanged, the backup under memory/backups/ is the old file as found, and refusing the open would leave a mind that
    cannot wake. A seal that did not match is reported in the log row, not held against the mind (decision 33).
    Returns what was done, or None when the cartridge is already current."""
    import importlib
    from datetime import datetime, timezone
    from aether.memory.fabric import memory_fabric, open_owner_engine
    p = pointer(home)
    if p is None or p['path'] in _CURRENT:
        return None
    memory_fabric()
    migrations = importlib.import_module('memory_fabric.migrations')
    schema = importlib.import_module('memory_fabric.schema')
    version = migrations.cartridge_version(p['path'])
    if version >= schema.SCHEMA_VERSION:
        _CURRENT.add(p['path'])
        return None
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    folder = Path(p['path']).parent / 'backups' / f'schema{version}-{stamp}'
    folder.mkdir(parents=True, exist_ok=True)
    report = migrations.migrate(p['path'], backup_dir=str(folder), allow_tampered=True)
    _CURRENT.add(p['path'])
    backup = Path(report.backup_path).relative_to(Path(p['path']).parent).as_posix()
    line = (f'my memory was carried from schema {report.from_version} to {report.to_version} by the engine; '
            f'the old file is kept at memory/{backup}')
    line += dict(tampered='; its seal did not match before the move (edited outside the engine)',
                 unsealed='; it had no seal before the move',
                 unclean='; its last session had ended without closing, so its seal could not be checked').get(report.seal_status, '')
    engine = open_owner_engine(p['path'], owner_id=p['owner'], session_id='carry-forward')
    try:
        engine.log([dict(kind='migrated', pointer=backup, tags=['memory', 'schema', 'migration'], line=line, by='Aether')])
    finally:
        engine.close()
    return dict(from_version=report.from_version, to_version=report.to_version, backup=report.backup_path,
                sealed=report.seal_status == 'sealed', seal_status=report.seal_status)


# -- closing words -> notes --------------------------------------------------------------------------------------
def parse_closing(text: str) -> dict:
    """The sections of a closing note, by their usual headings. Missing sections are empty."""
    out = dict(did='', learned=[], carry=[], skip=[])
    marks = [m for m in _SECTION.finditer(text or '') if _classify(m.group(1) or m.group(2) or '')]
    for i, m in enumerate(marks):
        key = _classify(m.group(1) or m.group(2) or '')
        body = text[m.end():marks[i + 1].start() if i + 1 < len(marks) else len(text)]
        bullets = [b.group(1).strip() for b in (_BULLET.match(line) for line in body.splitlines())
                   if b and len(re.sub(r'[^A-Za-z]', '', b.group(1))) >= 12]      # a label-only bullet ("Budget:") is not a lesson
        if key == 'did':
            out['did'] = (out['did'] + ' ' + re.sub(r'\s+', ' ', body).strip()).strip()[:2000]
        else:
            out[key] += bullets or ([re.sub(r'\s+', ' ', body).strip()[:1500]] if body.strip() else [])
    return out


def _classify(heading: str) -> str | None:
    """Which section a heading opens, by its words; None for a heading that is none of them (a title, a greeting)."""
    for key, pattern in _HEADS:
        if pattern.search(heading):
            return key
    return None


def _sig(text: str) -> set:
    """The content words of a title, stemmed by truncation: what two phrasings of one lesson share."""
    return {w[:6] for w in re.findall(r'[a-z][a-z0-9]{2,}', (text or '').lower()) if w not in _STOP}


SAME_KINDS = ('standing_lesson', 'safety_warning')     # lessons repeat in other words; facts and closings stay exact


def find_same(engine, title: str, kind: str, owner: str) -> str | None:
    """The mind's own note that says this already, or None. Exact title for every kind; for a lesson or a warning also
    the same meaning in other words: the shorter title's content words four-fifths contained in the longer's (at least
    three words). Never a note written by someone else (a seed, an import): the mind's own experience is its own."""
    rows = engine.cart.conn.execute('SELECT note_id, title FROM notes WHERE profile = ? AND written_by = ?', (kind, owner)).fetchall()
    wanted = title.strip().lower()
    for nid, t in rows:
        if (t or '').strip().lower() == wanted:
            return nid
    if kind not in SAME_KINDS:
        return None
    mine = _sig(title)
    if len(mine) < 3:
        return None
    best, best_score = None, 0.0
    for nid, t in rows:
        other = _sig(t)
        if len(other) < 3:
            continue
        small, big = (mine, other) if len(mine) <= len(other) else (other, mine)
        contain = len(small & big) / len(small)
        if contain >= 0.8 and contain > best_score:
            best, best_score = nid, contain
    return best


def _title(line: str, limit: int = 90) -> str:
    plain = re.sub(r'[*`#>]+', '', line).strip().lstrip('.;:- ')
    first = re.split(r'(?<=[.;:])\s', plain, maxsplit=1)[0]
    if len(re.sub(r'[^A-Za-z]', '', first)) < 12:            # a short label ("Deliverable:"): take the whole line instead
        first = plain
    return (first if len(first) <= limit else plain[:limit - 1].rstrip() + '…') or 'untitled'


def repeats(rows: list) -> list:
    """What the side log says was done more than once, in the host's words (the engine's sidelog box)."""
    import importlib
    from aether.memory.fabric import memory_fabric
    memory_fabric()
    return importlib.import_module('memory_fabric.sidelog').repeats(rows)


def skips(home: Path, limit: int = 4) -> list:
    """The standing skips: task obligations tagged skip, strongest first."""
    engine = _open_read(home)
    try:
        engine.cart.sync_clock()
        rows = engine.cart.conn.execute(
            "SELECT s.note_id, s.title, s.strength FROM temp.note_state s JOIN tags t ON t.note_id = s.note_id "
            "WHERE t.tag = 'skip' AND s.profile = 'task_obligation' AND s.strength >= ? ORDER BY s.strength DESC LIMIT ?",
            (engine.cart.setting('surface_threshold'), limit)).fetchall()
        return [dict(id=r[0], title=r[1], strength=round(r[2], 2)) for r in rows]
    finally:
        engine.close()


def file_closing(home: Path, text: str, *, commission_id: str, tags: list | None = None) -> dict:
    """File the closing words into the cartridge as notes. Returns what was written: [(kind, note id, title)]."""
    slug = re.sub(r'[^A-Za-z0-9]+', '-', commission_id).strip('-').lower()
    base = [f'commission-{slug}'] + [t for t in (tags or []) if t]
    parts = parse_closing(text)
    written = []
    engine = _open_write(home, f'closure-{commission_id}')

    owner = pointer(home)['owner']

    def remember(title, body, kind, tags, label=None, **options):
        """The same lesson said again, in these words or others, is reinforced, not filed twice: the existing note is
        refreshed and the new phrasing's words become its tags, so recall reaches it either way. Reports what it did."""
        same = find_same(engine, title, kind, owner)
        if same:
            engine.load([same])
            new_tags = [t for t in tags if t and not t.startswith('commission-')][:8]
            if new_tags:
                try:
                    engine.add_tags(same, new_tags)
                except Exception:
                    pass
            written.append(('refreshed', same, title))
            return same
        nid = engine.remember(title, body, kind, tags, **options)
        written.append((kind, nid, label or title))
        return nid
    import hashlib
    from memory_fabric import records
    operation = 'atelier-closing:' + hashlib.sha256((commission_id + '\0' + text).encode('utf-8')).hexdigest()
    def write():
        head = (text.strip().splitlines() or [commission_id])[0]
        summary = engine.remember(_title(f'Closing words, {commission_id}: {head}', 120), text.strip(), 'project_fact',
                                  base + ['closing-words'] + words(head), switch_in_days=14)
        written.append(('project_fact', summary, 'closing words'))
        done = engine.log_recent(commission_id, limit=400)
        trail = ('\n\nFrom the side log (' + str(len(done)) + ' actions, newest first):\n' +
                 '\n'.join(f"- {r['at'][:16]} {r['kind']}: {r['line']}" for r in done[:20])) if done else ''
        if parts['did'] or trail:
            remember(_title(f'What I did in {commission_id}', 120), (parts['did'] or 'See the side log.') + trail, 'project_fact',
                     base + ['did'] + words(parts['did'], 6), label='what I did', switch_in_days=7, links=[(summary, 'context')])
        for line in parts['learned']:
            remember(_title(line), line, 'standing_lesson', base + ['learned'] + words(line, 6), links=[(summary, 'context')])
        for line in parts['carry']:
            kind = 'safety_warning' if _WARNING.search(line) else 'standing_lesson'
            remember(_title(line), line, kind, base + ['carry'] + words(line, 6), links=[(summary, 'context')])
        for line in parts['skip']:                        # what the next self should not do again: an obligation, dated
            remember('Skip: ' + _title(line, 100), line, 'task_obligation', base + ['skip'] + words(line, 6), switch_in_days=45,
                     category='Task', links=[(summary, 'context')])
        for line in repeats(done):                        # and what the log says it did twice, in the host's words
            remember('Skip: ' + _title(line, 100), line + f' (counted from the side log of {commission_id})', 'task_obligation',
                     base + ['skip', 'repeat'] + words(line, 6), switch_in_days=45, category='Task', links=[(summary, 'context')],
                     written_by='Aether', source_ref='sidelog')
        if not parts['did'] and not parts['learned'] and not parts['carry'] and not parts['skip']:
            clause = _CLAUSE.search(text)
            title = _title(clause.group(1) if clause else head)
            remember(title, text.strip(), 'standing_lesson', base + ['carry'] + words(text, 8), links=[(summary, 'context')])
        return dict(cartridge=pointer(home)['path'], notes=written)
    try:
        result = records.mutate(engine.cart, 'atelier_closing', {'commission_id': commission_id, 'text': text},
                                write, operation_id=operation)
    finally:
        engine.close()
    return result


KIND_WORDS = dict(lesson='standing_lesson', warning='safety_warning', fact='project_fact', todo='task_obligation')


def file_now(home: Path, kind: str, title: str, body: str, tags: list, *, commission_id: str | None) -> dict:
    """A memory the mind files during a commission, in its own words, now. The same lesson said again reinforces."""
    profile = KIND_WORDS.get((kind or '').strip().lower(), 'standing_lesson')
    title = _title(title or body or '', 110)
    if len(re.sub(r'[^A-Za-z]', '', title)) < 6:
        return dict(text='A memory needs a title with words in it.')
    engine = _open_write(home, f'file-now-{commission_id or "turn"}')
    try:
        owner = pointer(home)['owner']
        slug = re.sub(r'[^A-Za-z0-9]+', '-', commission_id or '').strip('-').lower()
        base = ([f'commission-{slug}'] if slug else []) + [t for t in (tags or []) if t][:6] + words(title, 6)
        same = find_same(engine, title, profile, owner)
        if same:
            engine.load([same]); engine.add_tags(same, [t for t in base if not t.startswith('commission-')][:8])
            return dict(id=same, kind=profile, title=title, text=f'you already hold this ({profile} {same}); it is refreshed and tagged')
        options = dict(category='Task') if profile == 'task_obligation' else (dict(category='Alert') if profile == 'safety_warning' else {})
        if profile in ('project_fact', 'task_obligation'):
            options['switch_in_days'] = 14 if profile == 'project_fact' else 30
        nid = engine.remember(title, (body or title).strip(), profile, base, **options)
        return dict(id=nid, kind=profile, title=title, text=f'filed {profile} {nid}: {title}')
    finally:
        engine.close()


def render_page(home: Path) -> Path | None:
    """The mind's viewer page (the fabric's own, read-only) beside its cartridge: memory/mind.html."""
    from aether.memory.fabric import memory_fabric
    memory_fabric()                                   # puts the engine on the path; its boxes load by name
    import importlib
    viewer = importlib.import_module('memory_fabric.viewer')
    out = Path(home) / 'memory' / 'mind.html'
    engine = _open_read(home)
    try:
        viewer.write_html(engine, str(out), include_bodies=True)
    finally:
        engine.close()
    return out


# -- the wake: headers, not bodies ----------------------------------------------------------------------------------
def recall(home: Path, tags: list, limit: int = 8, budget_tokens: int = 900) -> list:
    """Headers by tag, through the engine's recall (links, decay, fog), then by discovery (full-text search with
    stemming over titles, bodies and tags) for what the tags did not reach: a lesson phrased unlike the letter still
    surfaces when its words share a stem or its body says it. Looking changes nothing."""
    engine = _open_read(home)
    try:
        result = engine.recall([t for t in tags if t], budget_tokens=budget_tokens, max_headers=limit)
        by_tag = [dict(id=h.note_id, title=h.title, kind=h.profile, strength=round(h.strength, 2), one_line=h.one_line, via=h.via)
                  for h in result.headers]
        found: list = []
        if any(tags):
            try:
                hits = engine.discover(' '.join(t for t in tags if t)[:2000], limit=12).get('candidates') or []
            except Exception:                        # discovery is a help, never a stop (FTS unavailable, a bad term)
                hits = []
            have = {h['id'] for h in by_tag}
            for c in hits:
                if c['id'] in have or c.get('historical'):
                    continue
                state = engine.cart.conn.execute('SELECT profile, strength FROM temp.note_state WHERE note_id = ?', (c['id'],)).fetchone()
                if not state:
                    continue
                first = next((line.strip() for line in (c.get('excerpt') or '').splitlines() if line.strip()), '')[:140]
                found.append(dict(id=c['id'], title=c['title'], kind=state[0], strength=round(state[1], 2), one_line=first, via='discovery'))
                have.add(c['id'])
        reserve = min(len(found), max(1, limit // 4)) if found else 0     # discovery keeps a quarter of the slots when it has something new
        out = by_tag[:max(0, limit - reserve)] + found[:reserve]
        if len(out) < limit:
            out += [h for h in by_tag if h not in out][:limit - len(out)]
        if len(out) < limit:
            out += [h for h in found if h not in out][:limit - len(out)]
        if not out:                                      # nothing by tag: the strongest notes, so a new mind sees its birth note and a
            engine.cart.sync_clock()                     # returning one its last closing (strength is the engine's, decay included)
            rows = engine.cart.conn.execute('SELECT note_id, title, profile, strength FROM temp.note_state ORDER BY strength DESC, refreshed_at DESC LIMIT ?',
                                            (min(limit, 5),)).fetchall()
            out = [dict(id=r[0], title=r[1], kind=r[2], strength=round(r[3], 2), one_line='(strongest; no tag matched)', via='strongest') for r in rows]
        _mark_decisions(engine.cart.conn, out)
        return out
    finally:
        engine.close()


def load(home: Path, note_ids: list, session: str = 'turn') -> list:
    """The bodies the mind chose. Loading refreshes them and strengthens the links among them: pulling up."""
    engine = _open_write(home, session)
    try:
        notes = engine.load([i for i in note_ids if i][:12])
        return [dict(id=n.get('note_id') or n.get('id'), title=n.get('title'), kind=n.get('profile'), body=(n.get('body') or '')[:6000],
                     tags=n.get('tags')) for n in notes]
    finally:
        engine.close()


def wake_block(home: Path, tags: list, limit: int = 8) -> str:
    return render_headers(recall(home, tags, limit=limit))


def render_skips(rows: list) -> str:
    if not rows:
        return ''
    return 'YOUR SKIPS (what your past selves said not to do again):\n' + '\n'.join(f"- [{r['id']}] {r['title'][6:] if r['title'].startswith('Skip: ') else r['title']}" for r in rows)


def render_headers(heads: list) -> str:
    if not heads:
        return 'YOUR MEMORY: empty (nothing filed yet).'
    lines = [f"- [{h['id']}] {h['title']} ({h['kind']}" + (f", strength {h['strength']}" if h['strength'] is not None else '') + f"): {h['one_line']}"
             + (f" (a decision applies: {', '.join(h['decisions'])})" if h.get('decisions') else '')
             + (f" (replaced by {h['replaced_by']})" if h.get('replaced_by') else '') for h in heads]
    return ('YOUR MEMORY (headers chosen by this commission\'s words; read one with load_memory, look further with recall_memory):\n'
            + '\n'.join(lines))


# -- decisions -----------------------------------------------------------------------------------------------------
# A claim that a ruling exists, not a mention of rulings in general ("the lead records operator decisions" is design talk;
# the second-machine run of 10-19 flagged three such lines before this was narrowed).
DECISION_PHRASES = re.compile(r"\boperator'?s? decision\s*[:(]|\b(?:by|per|under) the operator'?s decision\b|"
                              r"\bthe operator (?:has |had )?decided\b|\bdecision \(operator|\bdecided:", re.I)
_PROJECT_TAIL = re.compile(r'-(?:[a-z]|\d+)$')
_FIELD = re.compile(r'^(Decided by|Decided at|Subject|Scope|Source): (.*)$', re.M)
ONLY_THEY_FILE = 'only the operator or the lead files a decision; say it in the room instead.'


def _stems(text: str) -> set:
    """Content words cut to five letters, three-letter words kept ("cap", "day"): enough to see that a ruling and a
    lesson speak of the same thing in different forms (cap ~ capped, total ~ totals). Lexical, not meaning."""
    return {w[:5] for w in re.findall(r'[a-z][a-z0-9_]{2,}', (text or '').lower()) if w not in _STOP}


def project_of(commission_id: str | None) -> str:
    """A commission's project: its id without a last one-letter or numeric segment (harbor-ledger-e -> harbor-ledger)."""
    cid = (commission_id or '').strip().lower()
    return _PROJECT_TAIL.sub('', cid) or cid


def scope_for(words_text: str, commission_id: str | None) -> str:
    """Michel: project-only unless he declares it standing."""
    return 'standing' if re.search(r'\bstanding\b', words_text or '', re.I) else f'project:{project_of(commission_id)}'


def subject_of(words_text: str, given: str | None = None) -> str:
    """A subject tag: the one given, else a finding's label (F2), a backticked name, or the first two content words."""
    raw = (given or '').strip()
    if not raw:
        m = re.search(r'\b([A-Z]\d{1,3})\b', words_text or '') or re.search(r'`([^`\s]{2,40})`', words_text or '')
        raw = m.group(1) if m else '-'.join(words(words_text, 2))
    return re.sub(r'[^a-z0-9_]+', '-', raw.lower()).strip('-')[:40] or 'decision'


def _decision(row) -> dict:
    fields = dict(_FIELD.findall(row[2] or ''))
    said = re.search(r'In their words: "(.*)"\s*$', row[2] or '', re.S)
    return dict(id=row[0], title=row[1], decided_by=fields.get('Decided by', ''), decided_at=fields.get('Decided at', ''),
                subject=fields.get('Subject', ''), scope=fields.get('Scope', ''), source=fields.get('Source', ''),
                words=(said.group(1) if said else '').strip(), created=row[3])


def _all_decisions(conn) -> list:
    rows = conn.execute("SELECT note_id, title, body, created_at FROM notes WHERE profile = 'decision' ORDER BY created_at, rowid").fetchall()
    found = [dict(_decision(r), seq=i) for i, r in enumerate(rows)]       # filing order: same-second rulings stay ordered
    replaced = {}
    for d in found:                                         # a derived link between two decisions: the older is replaced
        for other in conn.execute("SELECT CASE WHEN a = ? THEN b ELSE a END FROM links WHERE relation = 'derived' AND (a = ? OR b = ?)",
                                  (d['id'], d['id'], d['id'])).fetchall():
            match = next((o for o in found if o['id'] == other[0]), None)
            if match and match['subject'] == d['subject'] and match['seq'] > d['seq']:
                replaced[d['id']] = match['id']
    for d in found:
        d['replaced_by'] = replaced.get(d['id'])
    return found


def _mark_decisions(conn, heads: list) -> None:
    """Headers learn which decisions apply to them (a conceptual link to a decision) and whether they are replaced."""
    decisions = {d['id']: d for d in _all_decisions(conn)}
    for h in heads:
        hid = h.get('id')
        if not hid:
            continue
        if hid in decisions:
            h['replaced_by'] = decisions[hid]['replaced_by']
            continue
        linked = [r[0] for r in conn.execute("SELECT CASE WHEN a = ? THEN b ELSE a END FROM links WHERE relation = 'conceptual' AND (a = ? OR b = ?)",
                                             (hid, hid, hid)).fetchall()]
        applies = [i for i in linked if i in decisions and not decisions[i]['replaced_by']]
        if applies:
            h['decisions'] = applies


def file_decision(home: Path, words_text: str, *, subject: str | None, scope: str, decided_by: str, source_ref: str,
                  decided_at: str | None = None, commission_id: str | None = None) -> dict:
    """File one ruling into this mind's cartridge, once per source (a daybook note and line). Links it to the warnings
    and lessons on its subject (conceptual), and replaces an earlier decision on the same subject and scope (derived,
    the earlier one buried). Returns what it did."""
    from datetime import datetime, timezone
    said = re.sub(r'\s+', ' ', (words_text or '').strip())
    if len(re.sub(r'[^A-Za-z]', '', said)) < 4:
        return dict(id=None, text='a decision needs words')
    engine = _open_write(home, f'decision-{commission_id or "record"}')
    try:
        conn = engine.cart.conn
        have = conn.execute("SELECT note_id FROM notes WHERE profile = 'decision' AND source_ref = ?", (source_ref,)).fetchone()
        if have:
            return dict(id=have[0], already=True)
        subj = subject_of(said, subject)
        when = decided_at or datetime.now(timezone.utc).isoformat(timespec='seconds')
        title = f'{decided_by} decided: ' + _title(said, 100)          # _title cuts at a colon; the ruling is the part that matters
        body = (f'Decided by: {decided_by}\nDecided at: {when}\nSubject: {subj}\nScope: {scope}\nSource: {source_ref}\n'
                f'In their words: "{said}"')
        tags = ['decision', subj, 'scope-' + re.sub(r'[^a-z0-9]+', '-', scope.lower())] + words(said, 6)
        nid = engine.remember(title, body, 'decision', tags, written_by=decided_by, source_ref=source_ref)
        mine = _stems(said)
        linked = []
        for oid, otitle, obody in conn.execute("SELECT note_id, title, body FROM notes WHERE profile IN ('safety_warning', 'standing_lesson') "
                                               "AND note_id != ?", (nid,)).fetchall():
            otags = {r[0] for r in conn.execute('SELECT tag FROM tags WHERE note_id = ?', (oid,)).fetchall()}
            if subj in otags or len(mine & _stems(otitle)) >= 2:          # the lesson's own words, by stem: cap ~ capped, total ~ totals
                engine.link(nid, oid, 'conceptual'); linked.append(oid)
        replaced = []
        for d in _all_decisions(conn):
            if d['id'] != nid and d['subject'] == subj and d['scope'] == scope and not d['replaced_by']:
                engine.link(nid, d['id'], 'derived'); engine.bury(d['id']); replaced.append(d['id'])
        engine.log([dict(kind='decision-filed', pointer=nid, tags=['decision', subj], line=f'filed: {title}'[:200], by='Aether')],
                   commission=commission_id)
        return dict(id=nid, title=title, subject=subj, scope=scope, linked=linked, replaces=replaced)
    finally:
        engine.close()


def decisions(home: Path, commission_id: str | None, words_list=()) -> list:
    """The decisions that bind here: this project's, and the standing ones whose subject or words meet the commission's
    words. Replaced ones are left out (they are still in memory, buried)."""
    engine = _open_read(home)
    try:
        found = [d for d in _all_decisions(engine.cart.conn) if not d['replaced_by']]
    finally:
        engine.close()
    here = f'project:{project_of(commission_id)}'
    wanted = {w.lower() for w in words_list if w}
    return [d for d in found if d['scope'] == here or (d['scope'] == 'standing' and (
            d['subject'] in wanted or wanted & set(words(d['words'], 20))))]


def render_decisions(rows: list) -> str:
    if not rows:
        return ''
    return ('DECISIONS THAT BIND HERE (filed by the operator, or by the lead recording the operator; shown whole; if you '
            'disagree, say so in the room):\n' + '\n'.join(
                f"- [{d['id']}] {d['title']} ({d['scope']}, {d['decided_at'][:10]})\n  In their words: \"{d['words']}\"" for d in rows))


def cited_decisions(home: Path, texts: list, *, shown=(), loaded=()) -> list:
    """Sentences in a mind's words that cite a decision, each with what it rests on in the record: a filed decision
    whose subject it names or whose words it shares (three content words), preferring those shown or loaded this turn."""
    sentences = []
    for text in texts:
        for s in re.split(r'(?<=[.!?])\s+|\n+', text or ''):
            if DECISION_PHRASES.search(s) and s.strip() not in sentences:
                sentences.append(s.strip())
    if not sentences:
        return []
    engine = _open_read(home)
    try:
        found = [d for d in _all_decisions(engine.cart.conn) if not d['replaced_by']]
    finally:
        engine.close()
    seen = set(shown) | set(loaded)
    out = []
    for s in sentences[:6]:
        mine = set(words(s, 40)) | {w.lower() for w in re.findall(r'\b[A-Z]\d{1,3}\b', s)}
        rests = [d for d in found if d['subject'] in mine or len(mine & set(words(d['words'], 40))) >= 3]
        rests.sort(key=lambda d: d['id'] not in seen)
        out.append(dict(sentence=s[:300], rests_on=[dict(id=d['id'], decided_by=d['decided_by'], decided_at=d['decided_at'][:10],
                                                         words=d['words'][:240], seen=d['id'] in seen) for d in rests[:3]]))
    return out


def render_citation(mind_name: str, cited: dict) -> str:
    said = cited['sentence'][:200]
    if not cited['rests_on']:
        return f'{mind_name} cites a decision that rests on nothing in the record: "{said}"'
    d = cited['rests_on'][0]
    return (f'{mind_name} cites a decision: "{said}" It rests on [{d["id"]}] {d["decided_by"]}, {d["decided_at"]}, '
            f'whose words are: "{d["words"][:200]}"')


# -- the side log --------------------------------------------------------------------------------------------------
def log_actions(home: Path, rows: list, *, commission_id: str | None, session: str = 'turn') -> int:
    """Append rows to the side log: each (kind, pointer, tags, line, by). Returns how many were written."""
    rows = [r for r in rows if r and r.get('kind') and r.get('line')]
    if not rows:
        return 0
    engine = _open_write(home, session)
    try:
        return engine.log(rows, commission=commission_id)
    finally:
        engine.close()


def log_recent(home: Path, commission_id: str | None = None, limit: int = 8) -> list:
    """The last actions, newest first; one commission's when given."""
    engine = _open_read(home)
    try:
        return engine.log_recent(commission_id, limit=limit)
    finally:
        engine.close()


def log_search(home: Path, words: list, limit: int = 10) -> list:
    """Rows whose line, tags or pointer carry any of the words; ranked by how many, newest first."""
    engine = _open_read(home)
    try:
        return engine.log_search(words, limit=limit)
    finally:
        engine.close()


def render_log(rows: list, title: str = 'YOUR LOG') -> str:
    if not rows:
        return ''
    lines = [f"- {r['at'][5:16].replace('T', ' ')} {r['kind']}: {r['line']}" + (f"  -> {r['pointer']}" if r['pointer'] else '') for r in rows]
    return f'{title} (newest first; look further with recall_log):\n' + '\n'.join(lines)


# -- the staff record: reviews by others, outcomes verified by others ------------------------------------------------
SPECIALTY = dict(member='implementation', reviewer='review', lead='leading', librarian='keeping')


def file_record(home: Path, facts: dict, mind: dict, commission_id: str) -> dict:
    """At closure, what others said and verified goes into the mind's own record (Michel, 10-19): every reviewer's
    verdicts become one review in the cartridge of every other mind of the commission (text: PASS/FAIL per subject;
    rating 1..5 from the share that passed); the operator's acceptance becomes an outcome verified by the operator
    (success; partial when a FAIL verdict stands unanswered), in the mind's specialty by kind, difficulty 2 for one
    sprint and 3 for more. The engine refuses a review or an outcome from the mind itself. Filed once per commission."""
    out = dict(reviews=[], outcome=None)
    mind_id, kind = mind.get('id'), mind.get('kind', 'member')
    engine = _open_write(home, f'record-{commission_id}')
    try:
        have = {(r['reviewer'], r['project']) for r in engine.reviews()}
        by_reviewer: dict = {}
        for v in facts.get('verdicts') or []:
            who = v.get('by') or {}
            if who.get('id') in (None, mind_id):
                continue
            by_reviewer.setdefault((who.get('name') or who['id'], who.get('kind', 'reviewer')), []).append(v)
        for (reviewer, role), verdicts in by_reviewer.items():
            if (reviewer, commission_id) in have:
                continue
            last: dict = {}
            for v in verdicts:                      # the last word on each subject counts
                last[(v.get('subject') or '').strip().lower()] = v
            passed = sum(1 for v in last.values() if str(v.get('verdict', '')).upper() == 'PASS')
            rating = round(1.0 + 4.0 * passed / max(1, len(last)), 2)
            text = f'{reviewer} on {commission_id}: {passed} of {len(last)} PASS.\n' + '\n'.join(
                f"- {str(v.get('verdict', '')).upper()} {v.get('subject', '')}: {(v.get('why') or '')[:200]}" for v in last.values())
            rid = engine.add_review(reviewer, role, text, rating=rating, project=commission_id)
            out['reviews'].append(dict(id=rid, reviewer=reviewer, rating=rating, verdicts=len(last)))
        acceptance = facts.get('acceptance')
        if acceptance:
            specialty = SPECIALTY.get(kind, kind)
            done = engine.cart.conn.execute('SELECT 1 FROM outcomes WHERE specialty = ? AND task_ref = ? LIMIT 1', (specialty, commission_id)).fetchone()
            if not done:
                last_by_subject: dict = {}
                for v in facts.get('verdicts') or []:
                    last_by_subject[(v.get('subject') or '').strip().lower()] = str(v.get('verdict', '')).upper()
                standing_fail = any(verdict == 'FAIL' for verdict in last_by_subject.values())
                sprints = len((facts.get('plan') or {}).get('sprints') or [])
                who = acceptance.get('by') or {}
                level = engine.record_outcome(specialty, commission_id, 'partial' if standing_fail else 'success',
                                              3.0 if sprints > 1 else 2.0, who.get('name') or who.get('id') or 'Operator')
                out['outcome'] = dict(specialty=specialty, result='partial' if standing_fail else 'success', level=level)
    finally:
        engine.close()
    return out


def record_outcome(home: Path, *, specialty: str, task_ref: str, result: str, difficulty: float, verified_by: str) -> int:
    """An outcome verified outside the room (an oracle, a test bench, a person). Returns the level afterwards."""
    engine = _open_write(home, f'outcome-{task_ref}')
    try:
        return engine.record_outcome(specialty, task_ref, result, float(difficulty), verified_by)
    finally:
        engine.close()


def card_line(home: Path) -> str:
    """One line from the trading card: levels, tasks, reviews."""
    engine = _open_read(home)
    try:
        c = engine.card()
    finally:
        engine.close()
    levels = ', '.join(f"{s['specialty']} level {s['level']}" for s in c.get('specialties') or []) or 'no level yet'
    st = c.get('stats') or {}
    return (f"YOUR CARD: {levels}; {st.get('verified_tasks', 0)} verified task(s), {st.get('successes', 0)} success(es); "
            f"{st.get('reviews', 0)} review(s)" + (f", rated {st['average_rating']} of 5" if st.get('average_rating') else ''))


# -- move-in: an existing home's experiences become notes ---------------------------------------------------------
def move_in(home: Path, agent_id: str, name: str | None = None) -> dict:
    """Give a home a cartridge and file every experience it already holds, oldest first. Idempotent on the cartridge;
    experiences already filed (by file name tag) are skipped."""
    home = Path(home)
    if pointer(home) is None:
        create(home, agent_id, name)
    folder = home / 'memory' / 'archive' / 'experiences'
    files = sorted(folder.glob('EXP_*.md')) if folder.is_dir() else []
    filed, skipped = [], []
    view = _open_read(home)
    try:
        have = {r[0] for r in view.cart.conn.execute('SELECT tag FROM tags').fetchall()}
    finally:
        view.close()
    for path in files:
        tag = 'file-' + path.stem.lower()
        if tag in have:
            skipped.append(path.name); continue
        text = path.read_text(encoding='utf-8', errors='replace')
        m = re.search(r'^#\s*Commission\s+(.+)$', text, re.M)
        cid = (m.group(1).strip() if m else path.stem)
        body = text.split('\n\n', 2)[-1] if '\n\n' in text else text
        filed.append(dict(file=path.name, **file_closing(home, body, commission_id=cid, tags=[tag])))
    return dict(cartridge=pointer(home)['path'], filed=filed, skipped=skipped)



# -- shared foundations: logical sessions, workspace tools and Welcome -------------------------------
def _session_for(engine, commission_id):
    row = engine.cart.conn.execute("SELECT event_id FROM session_events WHERE kind='opened' AND source_ref=? ORDER BY event_seq DESC LIMIT 1",
                                   ('atelier:' + commission_id,)).fetchone()
    return row[0] if row else None


def begin_work(home: Path, commission_id: str):
    """Once at actual activation, not during preview hydration or each API call."""
    from aether.memory.foundations import begin
    with _open_write(home, 'atelier:' + commission_id) as engine:
        reconcile_closures(engine, home)
        sid = _session_for(engine, commission_id)
        closed = sid and engine.cart.conn.execute("SELECT 1 FROM session_events WHERE session_id=? AND kind='closed'", (sid,)).fetchone()
        if sid and not closed:
            return sid
        return begin(engine, commission_id + ':' + (sid or 'first'), source_system='atelier',
                     source_ref='atelier:' + commission_id, previous_session_id=sid)


def reconcile_closures(engine, home):
    """Publish verified retained Atelier receipts, without running closure again."""
    import hashlib
    import sqlite3
    from aether.memory.foundations import publish
    from memory_fabric.errors import MemoryFabricError
    results = []
    for path in sorted((Path(home) / 'logs/atelier_closure').glob('*.json')):
        try:
            state = json.loads(path.read_text(encoding='utf-8'))
            cid, sid = state['closure_id'], state['session_id']
            if state.get('status') != 'CLOSED':
                continue
            if engine.cart.conn.execute("SELECT 1 FROM session_events WHERE kind='closed' AND source_system='atelier' AND closure_id=?", (cid,)).fetchone():
                continue
            if not engine.cart.conn.execute("SELECT 1 FROM session_events WHERE event_id=? AND kind='opened' AND source_system='atelier'", (sid,)).fetchone():
                continue
            nid = state['written']['cartridge_note']
            text = engine.note(nid)['body']
            if hashlib.sha256(text.encode('utf-8')).hexdigest() != state.get('carry_sha256'):
                raise ValueError('retained closing note no longer matches verified closure')
            results.append(publish(engine, sid, cid, path, source_system='atelier',
                                   carry_text=text, carry_note_id=nid))
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, MemoryFabricError) as exc:
            results.append(dict(status='publication_pending', evidence_ref=str(path), error=str(exc)))
    return results


def workspace_call(home: Path, commission_id: str, action: str, arguments=None):
    from aether.memory.fabric import memory_fabric
    memory_fabric()
    from memory_fabric.workspace import READ_ACTIONS
    with (_open_read(home) if action in READ_ACTIONS or action == 'query' else _open_write(home, 'atelier:' + commission_id)) as engine:
        engine.logical_session_id = _session_for(engine, commission_id)
        if action == 'query':
            return engine.query(**(arguments or {}))
        return engine.workspace(action, arguments)


def welcome(home: Path, commission_id: str):
    from aether.memory.fabric import memory_fabric
    memory_fabric()
    from memory_fabric.dashboard import render_text
    result = workspace_call(home, commission_id, 'dashboard')
    return render_text(result)
