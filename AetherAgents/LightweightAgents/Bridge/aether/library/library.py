"""A library: a collection with a catalog, tended by a librarian (a mind), reachable by mail.

Layout under <root>/libraries/<collection>/:
  catalog.json        the index: one entry per item (title, kind, summary in the librarian's words, tags, source, files
                      by name and sha256, lends, related). The catalog points at items; it never copies their text.
  items/<item id>/    the item's files as deposited
  deposits/<id>/      what minds handed in and the librarian has not filed yet (note.json beside the files)
  declined/<id>/      deposits the librarian would not file, with its reason in the ledger
  ledger.jsonl        every deposit, filing, declining, lending and index change, append-only, hash-chained

Nothing here decides for a mind. `search` ranks by plain word overlap and returns entries, not items; the librarian
reads, chooses, and says in its own words what it lent and what it left on the shelf. `verify` reports a broken
chain; it never blocks.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from aether.roadmaps.project_engagement_contract import digest, encoded

SCHEMA = 'aether-library-1.0.0'
KINDS = ('teachframe', 'example', 'critique', 'render', 'note', 'dataset', 'code', 'other')
LEDGER_KINDS = ('created', 'filed', 'deposited', 'declined', 'lent', 'edited', 'removed')
WORD_RE = re.compile(r'[a-z0-9][a-z0-9_\-]{1,}')


class LibraryError(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def words(text: str) -> set:
    return set(WORD_RE.findall((text or '').lower()))


def _who(value) -> dict:
    if isinstance(value, dict):
        return dict(id=str(value.get('id') or 'unknown'), kind=str(value.get('kind') or 'unknown'),
                    name=str(value.get('name') or value.get('id') or 'unknown'))
    return dict(id=str(value or 'unknown'), kind='unknown', name=str(value or 'unknown'))


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(f'.{os.getpid()}.tmp')
    tmp.write_text(text, encoding='utf-8')
    for attempt in range(20):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
    path.write_text(text, encoding='utf-8')
    try:
        tmp.unlink()
    except OSError:
        pass


def _slug(value: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', str(value).lower()).strip('-')
    return slug[:80] or 'item'


class Library:
    def __init__(self, root: Path, collection: str, *, title: str | None = None):
        self.collection = collection
        self.root = Path(root) / 'libraries' / collection
        self.items_dir, self.deposits_dir, self.declined_dir = self.root / 'items', self.root / 'deposits', self.root / 'declined'
        self.catalog_path, self.ledger_path = self.root / 'catalog.json', self.root / 'ledger.jsonl'
        for d in (self.root, self.items_dir, self.deposits_dir, self.declined_dir):
            d.mkdir(parents=True, exist_ok=True)
        if not self.catalog_path.exists():
            _write_atomic(self.catalog_path, json.dumps(dict(schema=SCHEMA, collection=collection, title=title or collection,
                                                             created=now(), entries={}), indent=1, ensure_ascii=False))
            self._ledger('created', _who('library'), None, dict(title=title or collection))

    # -- the catalog ----------------------------------------------------------------------------------------
    def catalog(self) -> dict:
        return json.loads(self.catalog_path.read_text(encoding='utf-8'))

    def entries(self) -> dict:
        return self.catalog()['entries']

    def entry(self, item_id: str) -> dict:
        try:
            return self.entries()[item_id]
        except KeyError:
            raise LibraryError(f'No item {item_id!r} in {self.collection}') from None

    def _save(self, catalog: dict) -> None:
        catalog['updated'] = now()
        _write_atomic(self.catalog_path, json.dumps(catalog, indent=1, ensure_ascii=False, sort_keys=True))

    def search(self, query: str, *, tags=(), kind: str | None = None, limit: int = 10) -> list:
        """Entries ranked by plain word overlap with the query over id, title, summary and tags. Entries, not items."""
        q = words(query)
        wanted = {t.lower() for t in tags}
        ranked = []
        for item_id, e in self.entries().items():
            if kind and e.get('kind') != kind:
                continue
            etags = {t.lower() for t in e.get('tags', ())}
            if wanted and not wanted <= etags:
                continue
            hay = words(' '.join([item_id, e.get('title', ''), e.get('summary', ''), ' '.join(e.get('tags', ()))]))
            score = len(q & hay) + 2 * len({t for t in q if t in etags}) + (3 if item_id.lower() in q else 0)
            if score or not q:
                ranked.append((score, e.get('lends', 0), item_id, e))
        ranked.sort(key=lambda r: (-r[0], -r[1], r[2]))
        return [dict(e, id=item_id, score=score) for score, _, item_id, e in ranked[:limit]]

    # -- items ----------------------------------------------------------------------------------------------
    def _store(self, folder: Path, files: dict) -> list:
        folder.mkdir(parents=True, exist_ok=True)
        out = []
        for name, data in files.items():
            if isinstance(data, (str, Path)) and Path(data).is_file():
                data = Path(data).read_bytes()
            elif isinstance(data, str):
                data = data.encode('utf-8')
            rel = Path(str(name).replace('\\', '/'))
            if rel.is_absolute() or '..' in rel.parts:
                raise LibraryError(f'A file name is relative and inside the item: {name!r}')
            target = folder / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            out.append(dict(name=rel.as_posix(), sha256=sha256_bytes(data), bytes=len(data)))
        return out

    def add(self, item_id: str | None, *, title: str, kind: str = 'note', summary: str = '', files: dict | None = None,
            tags=(), source: dict | None = None, related=(), by=None) -> dict:
        """File an item straight into the catalog (the librarian's own hand, or a seed)."""
        if kind not in KINDS:
            raise LibraryError(f'Unknown kind {kind!r}; one of {KINDS}')
        item_id = _slug(item_id or title)
        catalog = self.catalog()
        if item_id in catalog['entries']:
            raise LibraryError(f'{item_id!r} is already in {self.collection}; edit it or pick another id')
        stored = self._store(self.items_dir / item_id, files or {})
        entry = dict(title=title, kind=kind, summary=summary, tags=sorted({str(t) for t in tags}),
                     source=dict(source or {}), files=stored, lends=0, lent_to=[], related=sorted(set(related)),
                     filed=now())
        catalog['entries'][item_id] = entry
        self._save(catalog)
        self._ledger('filed', _who(by or 'librarian'), item_id, dict(title=title, kind=kind, files=[f['sha256'] for f in stored]))
        return dict(entry, id=item_id)

    def edit(self, item_id: str, *, by=None, **changes) -> dict:
        catalog = self.catalog()
        entry = catalog['entries'].get(item_id)
        if entry is None:
            raise LibraryError(f'No item {item_id!r}')
        allowed = {'title', 'summary', 'tags', 'related', 'kind'}
        bad = set(changes) - allowed
        if bad:
            raise LibraryError(f'Only {sorted(allowed)} can be edited, not {sorted(bad)}')
        before = {k: entry.get(k) for k in changes}
        for k, v in changes.items():
            entry[k] = sorted({str(x) for x in v}) if k in ('tags', 'related') else v
        self._save(catalog)
        self._ledger('edited', _who(by or 'librarian'), item_id, dict(before=before, after={k: entry[k] for k in changes}))
        return dict(entry, id=item_id)

    def read(self, item_id: str, name: str | None = None) -> bytes:
        entry = self.entry(item_id)
        name = name or (entry['files'][0]['name'] if entry['files'] else None)
        if not name:
            raise LibraryError(f'{item_id!r} has no files')
        return (self.items_dir / item_id / name).read_bytes()

    def lend(self, item_id: str, to, into: Path) -> dict:
        """Copy the item's files into a commission's library folder; count the lending; write the ledger."""
        entry = self.entry(item_id)
        target = Path(into) / item_id
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(self.items_dir / item_id, target)
        catalog = self.catalog()
        e = catalog['entries'][item_id]
        e['lends'] = int(e.get('lends', 0)) + 1
        e.setdefault('lent_to', []).append(dict(to=_who(to), at=now()))
        e['lent_to'] = e['lent_to'][-50:]
        self._save(catalog)
        self._ledger('lent', _who(to), item_id, dict(into=str(target)))
        return dict(entry, id=item_id, into=str(target))

    # -- deposits -------------------------------------------------------------------------------------------
    def deposit(self, sender, files: dict, note: str = '', *, commission: str | None = None, deposit_id: str | None = None) -> dict:
        """A mind hands something in. It waits in deposits/ until the librarian files or declines it. A deposit
        that already exists under `deposit_id` (a note delivered twice) is returned, not duplicated."""
        dep_id = _slug(deposit_id) if deposit_id else f'{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}'
        folder = self.deposits_dir / dep_id
        if (folder / 'note.json').exists():
            return json.loads((folder / 'note.json').read_text(encoding='utf-8'))
        if (self.declined_dir / dep_id / 'note.json').exists() or any(
                (e.get('source') or {}).get('deposit') == dep_id for e in self.entries().values()):
            return dict(id=dep_id, already='handled')
        stored = self._store(folder, files)
        body = dict(id=dep_id, sender=_who(sender), note=note, commission=commission, files=stored, at=now())
        _write_atomic(folder / 'note.json', json.dumps(body, indent=1, ensure_ascii=False))
        self._ledger('deposited', _who(sender), None, dict(deposit=dep_id, files=[f['sha256'] for f in stored], note=note[:500]))
        return body

    def deposits(self) -> list:
        out = []
        for folder in sorted(self.deposits_dir.iterdir()):
            note = folder / 'note.json'
            if folder.is_dir() and note.exists():
                out.append(json.loads(note.read_text(encoding='utf-8')))
        return out

    def file(self, deposit_id: str, *, item_id: str | None = None, title: str, kind: str = 'note', summary: str = '',
             tags=(), related=(), by=None) -> dict:
        """The librarian files a deposit as an item, in its own words."""
        folder = self.deposits_dir / deposit_id
        if not (folder / 'note.json').exists():
            raise LibraryError(f'No deposit {deposit_id!r}')
        body = json.loads((folder / 'note.json').read_text(encoding='utf-8'))
        files = {f['name']: (folder / f['name']) for f in body['files']}
        entry = self.add(item_id or title, title=title, kind=kind, summary=summary, files=files, tags=tags,
                         source=dict(deposit=deposit_id, sender=body['sender'], commission=body.get('commission'),
                                     note=body.get('note', ''), at=body.get('at')),
                         related=related, by=by)
        shutil.rmtree(folder)
        return entry

    def decline(self, deposit_id: str, why: str, *, by=None) -> dict:
        folder = self.deposits_dir / deposit_id
        if not (folder / 'note.json').exists():
            raise LibraryError(f'No deposit {deposit_id!r}')
        target = self.declined_dir / deposit_id
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(folder), str(target))
        self._ledger('declined', _who(by or 'librarian'), None, dict(deposit=deposit_id, why=why))
        return dict(deposit=deposit_id, why=why, at=str(target))

    # -- the ledger -----------------------------------------------------------------------------------------
    def _ledger(self, kind: str, who: dict, item_id: str | None, detail: dict | None = None) -> dict:
        if kind not in LEDGER_KINDS:
            raise LibraryError('Unknown ledger kind: ' + kind)
        rows = self.ledger()
        previous = rows[-1]['sha256'] if rows else None
        body = dict(schema=SCHEMA, n=len(rows) + 1, at=now(), collection=self.collection, kind=kind, who=who,
                    item=item_id, detail=dict(detail or {}), previous_sha256=previous)
        row = dict(body, sha256=digest(body))
        with self.ledger_path.open('ab') as handle:
            handle.write(encoded(row) + b'\n')
            handle.flush()
            os.fsync(handle.fileno())
        return row

    def ledger(self, limit: int | None = None) -> list:
        if not self.ledger_path.exists():
            return []
        rows = [json.loads(line) for line in self.ledger_path.read_text(encoding='utf-8').splitlines() if line.strip()]
        return rows[-limit:] if limit else rows

    def verify(self) -> dict:
        """The chain and the files, reported, never enforced."""
        rows = self.ledger()
        previous, first_problem = None, None
        for row in rows:
            body = {k: v for k, v in row.items() if k != 'sha256'}
            if row.get('previous_sha256') != previous or digest(body) != row.get('sha256'):
                first_problem = dict(n=row.get('n'), what='chain'); break
            previous = row['sha256']
        missing = []
        for item_id, e in self.entries().items():
            for f in e.get('files', ()):
                path = self.items_dir / item_id / f['name']
                if not path.exists() or sha256_bytes(path.read_bytes()) != f['sha256']:
                    missing.append(dict(item=item_id, file=f['name']))
        return dict(ok=first_problem is None and not missing, rows=len(rows), items=len(self.entries()),
                    first_problem=first_problem, files_changed_or_missing=missing)

    @classmethod
    def all_under(cls, root: Path) -> list:
        base = Path(root) / 'libraries'
        return [p.name for p in sorted(base.iterdir()) if (p / 'catalog.json').exists()] if base.exists() else []
