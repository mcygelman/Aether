"""A mailbox: a directory with an inbox, an outbox, and the two places an outbox note can end up.

  <root>/mailboxes/<participant id>/inbox/      notes delivered to this participant, one JSON file each, in order
  <root>/mailboxes/<participant id>/outbox/     notes this participant wrote and has not yet handed over
  <root>/mailboxes/<participant id>/sent/       outbox notes that were handed over
  <root>/mailboxes/<participant id>/bounced/    outbox notes that came back, with why
  <root>/mailboxes/<participant id>/delivered.json   the last note number delivered here (idempotent delivery)

The root can be a share: that is how a mind on another machine joins. Every write is a temporary file renamed into
place, so a reader on the other side never sees a half-written note.
"""
from __future__ import annotations

import base64
import json
import os
import uuid
from pathlib import Path

BROADCAST = None     # a note with no recipient is to everyone in the commission


class MailboxError(ValueError):
    pass


def _write(path: Path, value: dict) -> Path:
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, path)
    return path


def _participant(value) -> dict:
    if not isinstance(value, dict) or not value.get('id') or not value.get('kind'):
        raise MailboxError('A participant is {id, kind} (and a name, if it has one)')
    return dict(id=str(value['id']), kind=str(value['kind']), **({'name': str(value['name'])} if value.get('name') else {}))


class Mailbox:
    def __init__(self, root: Path, participant: dict):
        self.participant = _participant(participant)
        self.root = Path(root) / 'mailboxes' / self.participant['id']
        for name in ('inbox', 'outbox', 'sent', 'bounced'):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    # -- the mind's side -----------------------------------------------------------------------------------
    def inbox(self, unread_only: bool = True, commission: str | None = None) -> list:
        """The notes here, oldest first; `commission` keeps only one commission's notes (a mind has one mailbox
        for every commission on the share)."""
        notes = []
        for path in sorted((self.root / 'inbox').glob('*.json')):
            note = json.loads(path.read_text(encoding='utf-8'))
            if unread_only and note.get('read'):
                continue
            if commission is not None and note.get('campaign_id') not in (None, commission):
                continue
            notes.append(note)
        return notes

    def mark_read(self, note_id: str) -> None:
        for path in (self.root / 'inbox').glob('*.json'):
            note = json.loads(path.read_text(encoding='utf-8'))
            if note['id'] == note_id and not note.get('read'):
                _write(path, dict(note, read=True))

    def send(self, text: str, *, files: dict | None = None, cost: dict | None = None, to: dict | None = None,
             in_reply_to: str | None = None, place: str | None = None, memory: dict | None = None) -> Path:
        """Write a note to the outbox. `files` maps a relative name to bytes; they travel base64 inside the note."""
        if type(text) is not str:
            raise MailboxError('A note is text')
        item = dict(id=str(uuid.uuid4()), sender=self.participant, to=_participant(to) if to else None,
                    in_reply_to=in_reply_to, text=text, cost=dict(cost or {}), place=place, memory=memory,
                    files={name: base64.b64encode(data).decode('ascii') for name, data in (files or {}).items()})
        return _write(self.root / 'outbox' / (item['id'] + '.json'), item)

    def bounces(self) -> list:
        return [json.loads(p.read_text(encoding='utf-8')) for p in sorted((self.root / 'bounced').glob('*.json'))]

    # -- the carrier's side --------------------------------------------------------------------------------
    def pending(self) -> list:
        """Outbox notes in the order written: [(path, note), ...]. Files are decoded to bytes."""
        items = []
        paths = []
        for path in (self.root / 'outbox').glob('*.json'):
            try:
                paths.append((path.stat().st_mtime_ns, path.name, path))
            except FileNotFoundError:
                continue                                   # taken by another place between the listing and now
        for _, _, path in sorted(paths):
            try:
                note = json.loads(path.read_text(encoding='utf-8'))
            except FileNotFoundError:
                continue
            note['files'] = {name: base64.b64decode(encoded) for name, encoded in note.get('files', {}).items()}
            items.append((path, note))
        return items

    def sent(self, path: Path) -> Path:
        target = self.root / 'sent' / Path(path).name
        try:
            os.replace(path, target)
        except FileNotFoundError:
            pass                                           # another place moved it already
        return target

    def bounce(self, path: Path, reason: str, detail: str) -> Path:
        note = json.loads(Path(path).read_text(encoding='utf-8'))
        note['bounce'] = dict(reason=reason, detail=detail)
        target = _write(self.root / 'bounced' / Path(path).name, note)
        Path(path).unlink()
        return target

    def deliver(self, note: dict) -> bool:
        """Put a daybook note in the inbox once (by its number). True when it was new here."""
        target = self.root / 'inbox' / f"{int(note['n']):06d}-{note['id']}.json"
        if target.exists():
            return False
        _write(target, dict(note, read=False))
        return True

    def delivered_through(self, commission: str | None = None) -> int:
        """The last note number delivered here for a commission (note numbers restart at 1 per commission)."""
        mark = self.root / 'delivered.json'
        if not mark.exists():
            return 0
        value = json.loads(mark.read_text(encoding='utf-8'))
        if commission is None:
            return int(value.get('n', 0))
        return int(value.get('by_commission', {}).get(commission, 0))

    def mark_delivered(self, n: int, commission: str | None = None) -> None:
        mark = self.root / 'delivered.json'
        value = json.loads(mark.read_text(encoding='utf-8')) if mark.exists() else {}
        value['n'] = int(n)
        if commission is not None:
            value.setdefault('by_commission', {})[commission] = int(n)
        _write(mark, value)

    @classmethod
    def all_under(cls, root: Path) -> list:
        base = Path(root) / 'mailboxes'
        return [p.name for p in sorted(base.iterdir()) if (p / 'inbox').is_dir()] if base.exists() else []
