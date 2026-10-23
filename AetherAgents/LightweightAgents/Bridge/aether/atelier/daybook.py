"""The atelier's record: an append-only daybook of messages with a hash chain, and the files the atelier saw, by hash.

The daybook is the single source of truth of a campaign. Everything else (facts, pages, memory filings) is derived
from it. It never refuses a message for its shape: a message is text from a participant, with optional files and
cost; the host decides what to make of it afterwards (facts.py). Integrity is a record, not a gate: `verify()`
reports a broken chain; nothing blocks on it.

Layout under <root>/daybooks/<campaign_id>/:
  daybook.jsonl   one JSON object per line, in order, each carrying previous_sha256 and its own sha256
  files/<sha256>  every file version a participant attached, stored once by content hash
  facts.json      the derived facts (facts.py writes it; the daybook never reads it)
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from aether.roadmaps.project_engagement_contract import digest, encoded

SCHEMA = 'aether-room-message-1.0.0'
KINDS = ('message', 'host', 'turn_failed', 'withheld')
PARTICIPANT_KINDS = ('operator', 'lead', 'member', 'reviewer', 'librarian', 'host')
MAX_TEXT = 1_000_000
MAX_FILE = 16 * 1024 * 1024


class DaybookError(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _participant(value) -> dict:
    if not isinstance(value, dict) or set(value) < {'id', 'kind'} or value['kind'] not in PARTICIPANT_KINDS:
        raise DaybookError('A participant is {id, kind} with a known kind')
    return dict(id=str(value['id']), kind=value['kind'], **({'name': str(value['name'])} if value.get('name') else {}))


class Daybook:
    def __init__(self, root: Path, campaign_id: str, place: str | None = None):
        self.root = Path(root) / 'daybooks' / campaign_id
        self.campaign_id = campaign_id
        self.place = place                       # the place appending, stamped on every note unless the note says
        self.path = self.root / 'daybook.jsonl'
        self.files = self.root / 'files'
        self.root.mkdir(parents=True, exist_ok=True)
        self.files.mkdir(exist_ok=True)

    # -- files -------------------------------------------------------------------------------------------
    def store_file(self, name: str, data: bytes, written_by: dict) -> dict:
        """Keep one file version by content hash; returns the reference a message carries."""
        if type(data) is not bytes or len(data) > MAX_FILE:
            raise DaybookError('A file is bytes, at most 16 MiB')
        if not name or name != name.strip() or '..' in name.split('/') or name.startswith(('/', '\\')):
            raise DaybookError('A file name is a relative, portable path')
        sha = hashlib.sha256(data).hexdigest()
        target = self.files / sha
        if not target.exists():
            tmp = self.files / (sha + '.tmp')
            tmp.write_bytes(data)
            os.replace(tmp, target)
        return dict(name=name, sha256=sha, bytes=len(data), written_by=_participant(written_by))

    def read_file(self, sha256: str) -> bytes:
        path = self.files / sha256
        if not path.is_file():
            raise DaybookError('No such file in this atelier: ' + sha256)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != sha256:
            raise DaybookError('Stored file bytes differ from their hash: ' + sha256)
        return data

    # -- messages ----------------------------------------------------------------------------------------
    def append(self, sender: dict, text: str, *, files=(), cost=None, to=None, kind='message', facts=None, place=None) -> dict:
        if kind not in KINDS:
            raise DaybookError('Unknown message kind: ' + str(kind))
        if type(text) is not str or len(text) > MAX_TEXT:
            raise DaybookError('A message is text, at most 1 MB')
        rows = self.read()
        previous = rows[-1]['sha256'] if rows else None
        body = dict(schema=SCHEMA, id=str(uuid.uuid4()), n=len(rows) + 1, at=now(), campaign_id=self.campaign_id,
                    kind=kind, sender=_participant(sender), to=_participant(to) if to else None, text=text,
                    files=[dict(f) for f in files], cost=dict(cost or {}), facts=dict(facts or {}),
                    place=place or self.place, previous_sha256=previous)
        message = dict(body, sha256=digest(body))
        with self.path.open('ab') as handle:
            handle.write(encoded(message) + b'\n')
            handle.flush()
            os.fsync(handle.fileno())
        return message

    def read(self, since_id: str | None = None, limit: int | None = None) -> list:
        """Messages in order; after `since_id` when given; the last `limit` when given."""
        if not self.path.exists():
            return []
        rows = [json.loads(line) for line in self.path.read_bytes().decode('utf-8').splitlines() if line.strip()]
        if since_id is not None:
            index = next((i for i, r in enumerate(rows) if r['id'] == since_id), None)
            if index is None:
                raise DaybookError('No such message in this atelier: ' + since_id)
            rows = rows[index + 1:]
        if limit is not None:
            rows = rows[-limit:] if limit else []
        return rows

    def last(self) -> dict | None:
        rows = self.read()
        return rows[-1] if rows else None

    def verify(self) -> dict:
        """Recompute the chain. A report, never a gate: {ok, messages, first_problem}."""
        previous = None
        for n, row in enumerate(self.read(), 1):
            body = {k: v for k, v in row.items() if k != 'sha256'}
            if row.get('n') != n or row.get('previous_sha256') != previous or digest(body) != row.get('sha256'):
                return dict(ok=False, messages=n - 1, first_problem=f'message {n} breaks the chain')
            for f in row.get('files', ()):
                if not (self.files / f['sha256']).is_file():
                    return dict(ok=False, messages=n - 1, first_problem=f'message {n} names a missing file {f["sha256"][:12]}')
            previous = row['sha256']
        return dict(ok=True, messages=len(self.read()), first_problem=None)
