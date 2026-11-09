"""Heavyweight cartridge access and authored closure, adapted from atelier/mind_memory.py.

Uses the Memory Fabric owner APIs and verbatim carry filing. Never imports
the Atelier room or writes markdown System 1/2 scaffolds. Exact closing text is
retained, including whitespace; runtime lifecycle publication lives separately.
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

_SECTION = re.compile(r'^[ \t]*(?:\*\*([A-Za-z][^\n*]{2,70}?)\*\*|#{1,4}[ \t]*([A-Za-z][^\n#]{2,70}))[ \t]*', re.M)

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

_CURRENT: set = set()

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

SAME_KINDS = ('standing_lesson', 'safety_warning')

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

def file_closing(home: Path, text: str, *, commission_id: str, tags: list | None = None,
                 carry_note_id: str | None = None) -> dict:
    """Retain exact carry only. The owner uses MemoryTools for reflective judgment.

    Keep the original operation key/request for text-only retries: an old committed
    filing is returned unchanged, including its historical extracted notes.
    """
    import hashlib
    engine = _open_write(home, f'closure-{commission_id}')
    from memory_fabric import records
    legacy = 'tasker-closing:' + hashlib.sha256((commission_id + '\0' + text).encode('utf-8')).hexdigest()
    operation = legacy if engine.cart.conn.execute(
        'SELECT 1 FROM operation_receipts WHERE operation_id=?', (legacy,)).fetchone() else (
            'tasker-owner-carry:' + hashlib.sha256(commission_id.encode('utf-8')).hexdigest())
    request = {'commission_id': commission_id, 'text': text}
    if carry_note_id is not None:
        request['carry_note_id'] = carry_note_id
    def write():
        if carry_note_id is not None:
            note = engine.maintain(action='inspect', id=carry_note_id)['note']
            if note['body'] != text or note['stored']['written_by'] != engine.agent_id:
                raise ValueError('carry_note_id must name your own note with the exact closing body')
            nid = carry_note_id
        else:
            nid = engine.remember('Closing words, ' + commission_id, text, 'offloaded_context',
                                  ['closing-words', 'continuity'] + list(tags or []), source_ref=commission_id)
        return dict(cartridge=pointer(home)['path'], notes=[('carry', nid, 'closing words')])
    try:
        return records.mutate(engine.cart, 'tasker_closing', request, write, operation_id=operation)
    finally:
        engine.close()


KIND_WORDS = dict(lesson='standing_lesson', warning='safety_warning', fact='project_fact', todo='task_obligation')

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

def render_headers(heads: list) -> str:
    if not heads:
        return 'YOUR MEMORY: empty (nothing filed yet).'
    lines = [f"- [{h['id']}] {h['title']} ({h['kind']}" + (f", strength {h['strength']}" if h['strength'] is not None else '') + f"): {h['one_line']}"
             + (f" (a decision applies: {', '.join(h['decisions'])})" if h.get('decisions') else '')
             + (f" (replaced by {h['replaced_by']})" if h.get('replaced_by') else '') for h in heads]
    return ('YOUR MEMORY (headers chosen by this commission\'s words; read one with load_memory, look further with recall_memory):\n'
            + '\n'.join(lines))

DECISION_PHRASES = re.compile(r"\boperator'?s? decision\s*[:(]|\b(?:by|per|under) the operator'?s decision\b|"
                              r"\bthe operator (?:has |had )?decided\b|\bdecision \(operator|\bdecided:", re.I)

_PROJECT_TAIL = re.compile(r'-(?:[a-z]|\d+)$')

_FIELD = re.compile(r'^(Decided by|Decided at|Subject|Scope|Source): (.*)$', re.M)

ONLY_THEY_FILE = 'only the operator or the lead files a decision; say it in the room instead.'

def project_of(commission_id: str | None) -> str:
    """A commission's project: its id without a last one-letter or numeric segment (harbor-ledger-e -> harbor-ledger)."""
    cid = (commission_id or '').strip().lower()
    return _PROJECT_TAIL.sub('', cid) or cid

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

SPECIALTY = dict(member='implementation', reviewer='review', lead='leading', librarian='keeping')
