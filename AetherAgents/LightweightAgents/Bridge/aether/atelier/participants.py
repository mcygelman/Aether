"""Participants: the minds of an atelier, as records the place daemon can wake.

A participant is a directory (its home) plus a record. The home carries what the mind is and remembers:

  <home>/identity/System_0.txt             who it is (sealed for heavyweights; authored text for assistants)
  <home>/protocols/*.md                     how it works
  <home>/workspace/SESSION_CURRENT.txt      its own carry: what it was doing, what it wants its next self to know
  <home>/memory/index/experience_index.md   its own index of experiences
  <home>/memory/archive/experiences/        its own experiences (one .md each), written at closure in its own words
  <home>/memory/.cartridge.json             pointer to the mind's Memory Fabric cartridge (mind_memory.py; every authored mind has one)

The record (participant.json beside the home, or passed in) says: id, kind (lead, member, reviewer), name, home,
deployment (which profile in runtime_profiles.json), roots (what it may read and write under the commission root),
and the mailboxes root it reads. Nothing here checks anything at a door: wake() reads the home and offers it;
file_closure() writes the mind's own closing words into its own home, and into its cartridge when it has one.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from . import mind_memory

KINDS = ('lead', 'member', 'reviewer', 'librarian')
CARTRIDGE_POINTER = 'memory/.cartridge.json'


@dataclass
class Participant:
    id: str
    kind: str
    name: str
    home: Path
    deployment: str = 'research'              # profile id in runtime_profiles.json
    roots: dict = field(default_factory=dict)  # relative path under the commission root -> 'read' | 'write'
    mailboxes: Path | None = None
    tags: list = field(default_factory=list)
    library: str | None = None                 # a librarian's collection on the share (<share>/libraries/<name>)

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError('A participant is a lead, a member, a reviewer or a librarian')
        self.home = Path(self.home)
        if self.mailboxes is not None:
            self.mailboxes = Path(self.mailboxes)

    # -- records -----------------------------------------------------------------------------------------
    def record(self) -> dict:
        return dict(id=self.id, kind=self.kind, name=self.name)

    def save(self) -> Path:
        self.home.mkdir(parents=True, exist_ok=True)
        data = asdict(self); data['home'] = str(self.home); data['mailboxes'] = str(self.mailboxes) if self.mailboxes else None
        path = self.home / 'participant.json'
        path.write_text(json.dumps(data, indent=1), encoding='utf-8')
        return path

    @classmethod
    def load(cls, home: Path) -> 'Participant':
        data = json.loads((Path(home) / 'participant.json').read_text(encoding='utf-8'))
        # The record remembers where the home was saved; the home is where it is found. A restored or moved home
        # works from its new place, and nothing is rewritten by a load (v0.1, Fable 10-22).
        data['home'] = Path(home); data['mailboxes'] = Path(data['mailboxes']) if data.get('mailboxes') else None
        return cls(**data)

    @classmethod
    def author(cls, home: Path, *, id: str, kind: str, name: str, system: str, protocols: str = '',
               deployment: str = 'research', roots: dict | None = None, mailboxes: Path | None = None,
               library: str | None = None, cartridge: bool = True) -> 'Participant':
        """Write a new assistant or critic home from authored text. Heavyweights are not authored here: they are born
        through the consent dialogue and their homes already exist."""
        home = Path(home)
        for rel, text in {'identity/System_0.txt': system, 'protocols/orientation.md': protocols or 'Read your own continuity, then the commission. Say what you did and what you learned when it closes.',
                          'workspace/SESSION_CURRENT.txt': 'New. Nothing carried yet.', 'memory/index/experience_index.md': ''}.items():
            path = home / rel; path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text(text, encoding='utf-8')
        (home / 'memory/archive/experiences').mkdir(parents=True, exist_ok=True)
        p = cls(id=id, kind=kind, name=name, home=home, deployment=deployment, roots=roots or {}, mailboxes=mailboxes, library=library)
        p.save()
        if cartridge:
            mind_memory.create(home, id, name)
            from . import seeds
            seeds.plant(home, id, name)          # born with the onboarding inside the memory
        return p

    def move_in(self) -> dict:
        """Give this home a cartridge and file the experiences it already holds (see mind_memory.move_in)."""
        report = mind_memory.move_in(self.home, self.id, self.name)
        from . import seeds
        report['seeds'] = seeds.plant(self.home, self.id, self.name)
        return report

    # -- what the mind is and remembers -------------------------------------------------------------------
    def _read(self, rel: str, limit: int = 60_000) -> str:
        path = self.home / rel
        return path.read_text(encoding='utf-8', errors='replace')[:limit] if path.is_file() else ''

    def system_text(self) -> str:
        protocols = '\n\n'.join(self._read('protocols/' + p.name) for p in sorted((self.home / 'protocols').glob('*.md'))) if (self.home / 'protocols').is_dir() else ''
        return (self._read('identity/System_0.txt') + ('\n\n' + protocols if protocols else '')).strip()

    def continuity(self, *, max_experiences: int = 6, tags: list | None = None) -> dict:
        """The mind's own carry, index and a few experiences, newest first or by tag overlap. Offered, never checked."""
        carry = self._read('workspace/SESSION_CURRENT.txt', 8_000)
        index = self._read('memory/index/experience_index.md', 8_000)
        folder = self.home / 'memory/archive/experiences'
        files = sorted(folder.glob('*.md'), key=lambda p: p.stat().st_mtime, reverse=True) if folder.is_dir() else []
        wanted = {t.lower() for t in (tags or [])}
        if wanted:
            def score(p):
                head = p.read_text(encoding='utf-8', errors='replace')[:600].lower()
                return sum(1 for t in wanted if t in head)
            files.sort(key=score, reverse=True)
        chosen = [(p.name, p.read_text(encoding='utf-8', errors='replace')[:4_000]) for p in files[:max_experiences]]
        return dict(carry=carry, index=index, experiences=chosen, cartridge=self.cartridge())

    def cartridge(self) -> dict | None:
        return mind_memory.pointer(self.home)

    def recall(self, tags: list, limit: int = 8) -> list:
        """Recall from the cartridge by tag, when the mind has one; otherwise from its experience files."""
        if self.cartridge():
            try:
                return mind_memory.recall(self.home, list(tags), limit=limit)
            except Exception as exc:          # a damaged cartridge is reported in the wake context, never a stop
                return [dict(id=None, title='cartridge unavailable', body=str(exc)[:200])]
        return [dict(id=name, title=name, body=text) for name, text in self.continuity(max_experiences=limit, tags=tags)['experiences']]

    def load_memory(self, note_ids: list, session: str = 'turn') -> list:
        """The bodies of the memories the mind chose (cartridge), or the named experience files (folder memory)."""
        if self.cartridge():
            try:
                return mind_memory.load(self.home, list(note_ids), session=session)
            except Exception as exc:
                return [dict(id=None, title='cartridge unavailable', body=str(exc)[:200])]
        folder = self.home / 'memory/archive/experiences'
        return [dict(id=n, title=n, body=(folder / n).read_text(encoding='utf-8', errors='replace')[:6000]) for n in note_ids if (folder / n).is_file()]

    def file_now(self, kind: str, title: str, body: str, tags: list, *, commission_id: str | None) -> dict:
        """A memory filed by the mind during a commission (cartridge minds only)."""
        if not self.cartridge():
            return dict(text='You have no cartridge; say it in your closing words instead.')
        try:
            return mind_memory.file_now(self.home, kind, title, body, tags, commission_id=commission_id)
        except Exception as exc:
            return dict(text=f'not filed: {type(exc).__name__}: {str(exc)[:160]}')

    def file_decision(self, words: str, *, subject: str | None, scope: str, decided_by: str, source_ref: str,
                      decided_at: str | None = None, commission_id: str | None = None) -> dict | None:
        """A ruling into this mind's cartridge (cartridge minds only); see mind_memory.file_decision."""
        if not self.cartridge():
            return None
        try:
            return mind_memory.file_decision(self.home, words, subject=subject, scope=scope, decided_by=decided_by,
                                             source_ref=source_ref, decided_at=decided_at, commission_id=commission_id)
        except Exception as exc:
            return dict(id=None, error=f'{type(exc).__name__}: {str(exc)[:160]}')

    def file_record(self, facts: dict, commission_id: str) -> dict | None:
        """Reviews by others and the operator's acceptance into this mind's record (cartridge minds only)."""
        if not self.cartridge():
            return None
        try:
            return mind_memory.file_record(self.home, facts, self.record(), commission_id)
        except Exception as exc:
            return dict(error=f'{type(exc).__name__}: {str(exc)[:160]}')

    def card_line(self) -> str | None:
        if not self.cartridge():
            return None
        try:
            return mind_memory.card_line(self.home)
        except Exception:
            return None

    def log(self, rows: list, *, commission_id: str | None, session: str = 'turn') -> int:
        """Append to the side log (cartridge minds only; a folder mind keeps no log)."""
        if not self.cartridge() or not rows:
            return 0
        try:
            return mind_memory.log_actions(self.home, rows, commission_id=commission_id, session=session)
        except Exception:
            return 0

    def log_recent(self, commission_id: str | None = None, limit: int = 8) -> list:
        if not self.cartridge():
            return []
        try:
            return mind_memory.log_recent(self.home, commission_id, limit=limit)
        except Exception:
            return []

    def log_search(self, words: list, limit: int = 10) -> list:
        if not self.cartridge():
            return []
        try:
            return mind_memory.log_search(self.home, words, limit=limit)
        except Exception:
            return []

    def wake_memory(self, tags: list, commission_id: str | None = None) -> str | None:
        """The memory block for a wake: the decisions that bind here (whole), the skips, then recall headers, for a mind
        with a cartridge; None for the folder memory."""
        if not self.cartridge():
            return None
        try:
            heads = mind_memory.recall(self.home, list(tags))
            self.last_offered = [h['id'] for h in heads if h.get('id')]      # the receipt's "offered"; not a field, never saved
            binding = mind_memory.decisions(self.home, commission_id, tags) if commission_id else []
            self.last_decisions = [d['id'] for d in binding]                  # the receipt's "decisions shown"; never saved
            ruled = mind_memory.render_decisions(binding)
            block = mind_memory.render_skips(mind_memory.skips(self.home))
            return (ruled + '\n\n' if ruled else '') + (block + '\n\n' if block else '') + mind_memory.render_headers(heads)
        except Exception as exc:
            return f'YOUR MEMORY: cartridge unavailable ({type(exc).__name__}: {str(exc)[:160]})'

    # -- closure: the mind's own words, filed into its own home ---------------------------------------------
    def file_closure(self, text: str, *, commission_id: str, tags: list | None = None) -> dict:
        """File authored words with stable receipts; publish verified closure, never a partial pass."""
        import hashlib
        from aether.integrity.sealbox import atomic_write_bytes
        closure_id = hashlib.sha256((commission_id + '\0' + text).encode('utf-8')).hexdigest()
        receipt = self.home / 'logs/atelier_closure' / (closure_id[:24] + '.json')
        receipt.parent.mkdir(parents=True, exist_ok=True)
        state = json.loads(receipt.read_text(encoding='utf-8')) if receipt.exists() else {
            'closure_id': closure_id, 'status': 'REQUESTED',
            'stamp': datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}
        if self.cartridge() and not state.get('session_id'):
            with mind_memory._open_write(self.home, 'closure:' + closure_id) as engine:
                sid = mind_memory._session_for(engine, commission_id)
                if sid is None or engine.cart.conn.execute("SELECT 1 FROM session_events WHERE session_id=? AND kind='closed'", (sid,)).fetchone():
                    from aether.memory.foundations import begin
                    sid = begin(engine, commission_id + ':' + (sid or 'first'), source_system='atelier',
                                source_ref='atelier:' + commission_id, previous_session_id=sid)
                state['session_id'] = sid
            atomic_write_bytes(receipt, json.dumps(state, sort_keys=True).encode('utf-8'))
        stamp = state['stamp']
        if state['status'] != 'CLOSED':
            experience = self.home / 'memory/archive/experiences' / f'EXP_{stamp}_{closure_id[:8]}.md'
            carry = self.home / 'workspace/SESSION_CURRENT.txt'
            index = self.home / 'memory/index/experience_index.md'
            for path in (experience, carry, index):
                path.parent.mkdir(parents=True, exist_ok=True)
            tag_line = ', '.join(tags or [])
            experience_text = f'# Commission {commission_id}\n\nTags: {tag_line}\nWritten by: {self.name} ({self.id}), at closure\n\n{text.strip()}\n'
            carry_text = f'Carry after {commission_id} ({stamp}):\n{text.strip()}\n'
            # Preserve the start stamp before any files are written so crash retries reuse names.
            atomic_write_bytes(receipt, json.dumps(state, sort_keys=True).encode('utf-8'))
            atomic_write_bytes(experience, experience_text.encode('utf-8'))
            atomic_write_bytes(carry, carry_text.encode('utf-8'))
            first = text.strip().splitlines()[0][:120] if text.strip() else commission_id
            entry = f'- {experience.name}: {first}\n'
            existing = index.read_text(encoding='utf-8') if index.exists() else ''
            if entry not in existing:
                atomic_write_bytes(index, (existing + entry).encode('utf-8'))
            written = dict(experience=str(experience), carry=str(carry), index=str(index), cartridge_note=None)
            error = None
            if self.cartridge():
                try:
                    filed = mind_memory.file_closing(self.home, text, commission_id=commission_id, tags=list(tags or []))
                    written['cartridge_note'] = filed['notes'][0][1]
                    written['cartridge_notes'] = filed['notes']
                except Exception as exc:
                    error = str(exc)
                    written['cartridge_note'] = 'unavailable: ' + error[:200]
            if experience.read_text(encoding='utf-8') != experience_text or carry.read_text(encoding='utf-8') != carry_text or entry not in index.read_text(encoding='utf-8'):
                raise ValueError('closure file persistence verification failed')
            state.update(status='FAILED' if error else 'CLOSED', written=written, error=error,
                         carry_sha256=hashlib.sha256(text.strip().encode('utf-8')).hexdigest())
            atomic_write_bytes(receipt, json.dumps(state, sort_keys=True).encode('utf-8'))
        written = dict(state['written'])
        if self.cartridge():
            try:
                from aether.memory.foundations import publish
                with mind_memory._open_write(self.home, 'closure:' + closure_id) as engine:
                    sid = state['session_id']
                    written['lifecycle'] = publish(engine, sid, closure_id, receipt, source_system='atelier',
                        carry_text=text.strip(), failure=state.get('error'),
                        carry_note_id=written.get('cartridge_note') if state['status'] == 'CLOSED' else None)
            except Exception as exc:
                written['lifecycle'] = dict(status='publication_pending', error=str(exc), evidence_ref=str(receipt))
        return written
