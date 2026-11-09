"""Agent views for operators (MOD-12): each agent's card, Welcome Dashboard and memory, read-only.

One projection serves Mission Control and the standalone viewer (tools/agent_dashboards.py).
The card is the Memory Fabric's own trading card: the agent's name, its own scan line as its
title, the avatar it chose for itself, and levels earned only from outcomes someone else
verified. Nothing here invents a rank or a look: a missing value is reported as missing.

Inputs:  folders that hold agent homes. A home is a folder with an Atelier pointer
         (memory/.cartridge.json, {"path"}) or a Bridge profile pointer (memory/cartridge.json,
         {"owner", "home"}, checked by session.read_pointer).
Outputs: AgentDirectory(roots).agents() -> [summary]; .card(key); .sessions(key);
         .dashboard(key, session) -> the agent's own Welcome projection; .note(key, note_id,
         revision); .memory_page(key) -> the Memory Fabric viewer page.
Never:   writes, opens a logical session, refreshes or reinforces a memory, migrates a
         cartridge, reads identity files, or follows a path taken from a memory row.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .fabric import memory_fabric, open_engine

ATELIER_POINTER = Path('memory') / '.cartridge.json'
PROFILE_POINTER = Path('memory') / 'cartridge.json'
LATEST, NO_SESSION = '', 'none'


class AgentViewError(ValueError):
    """An agent or record that cannot be shown; the message says why, in plain words."""


class UnknownAgent(AgentViewError):
    pass


@dataclass(frozen=True)
class AgentHome:
    key: str                      # stable within this directory: the home folder's name, made unique
    home: Path
    source: str                   # the label of the root it was found under
    cartridge: Optional[Path]     # None when the pointer cannot be resolved
    problem: Optional[str]        # why the cartridge cannot be located


def _locate(home: Path):
    """The cartridge a home points to, or the reason it cannot be located."""
    atelier = home / ATELIER_POINTER
    if atelier.is_file():
        try:
            data = json.loads(atelier.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError('expected a JSON object')
            path = data.get('path', 'mind.sqlite')
            if not isinstance(path, str) or not path.strip() or '\x00' in path:
                raise ValueError('path must be a nonempty string')
            cart = Path(path)
            cart = cart if cart.is_absolute() else atelier.parent / cart
            return (cart, None) if cart.is_file() else (None, f'cartridge not found: {cart.name}')
        except (OSError, ValueError) as exc:
            return None, f'cartridge pointer unreadable: {exc}'
    if (home / PROFILE_POINTER).is_file():
        from .session import read_pointer, SessionMemoryError
        try:
            pointer = read_pointer(home)
        except (SessionMemoryError, OSError, ValueError) as exc:
            return None, str(exc)
        return pointer.home, None
    return None, None


def discover(roots: Iterable) -> list:
    """Agent homes directly under each root. roots: paths, or (label, path) pairs."""
    found, used = [], set()
    for root in roots:
        label, folder = root if isinstance(root, tuple) else (Path(root).name, root)
        folder = Path(folder)
        if not folder.is_dir():
            continue
        for home in sorted(p for p in folder.iterdir() if p.is_dir()):
            if not ((home / ATELIER_POINTER).is_file() or (home / PROFILE_POINTER).is_file()):
                continue
            cartridge, problem = _locate(home)
            key, n = home.name, 2
            while key.casefold() in used:
                key, n = f'{home.name}-{n}', n + 1
            used.add(key.casefold())
            found.append(AgentHome(key, home, label, cartridge, problem))
    return found


def _avatar_uri(svg: Optional[str]) -> Optional[str]:
    # Shown only as <img src>, where an SVG can neither run code nor fetch anything.
    return 'data:image/svg+xml;base64,' + base64.b64encode(svg.encode('utf-8')).decode('ascii') if svg else None


def _rank(specialties: list) -> dict:
    top = next((s for s in specialties if s['level'] > 0), None)
    if top is None:
        return {'level': 0, 'label': 'Unranked', 'specialty': None,
                'basis': 'No outcome verified by someone else yet.'}
    return {'level': top['level'], 'label': f"Level {top['level']}", 'specialty': top['specialty'],
            'basis': 'Highest specialty level, from outcomes verified by others; levels never go down.'}


def _opened(engine) -> list:
    return [dict(r) for r in engine.cart.conn.execute(
        "SELECT event_id, occurred_at, source_system, previous_session_id FROM session_events "
        "WHERE kind='opened' ORDER BY event_seq DESC LIMIT 20")]


class AgentDirectory:
    """The agents an operator can look at. Rebuilt from disk on every listing."""

    def __init__(self, roots: Iterable):
        self.roots = list(roots)

    def homes(self) -> list:
        return discover(self.roots)

    def find(self, key: str) -> AgentHome:
        for home in self.homes():
            if home.key == key:
                return home
        raise UnknownAgent(f'No agent named {key!r} in the configured folders.')

    def _open(self, home: AgentHome):
        if home.cartridge is None:
            raise AgentViewError(home.problem or 'This agent has no memory cartridge.')
        try:
            return open_engine(str(home.cartridge), read_only=True)
        except Exception as exc:  # CartridgeError and sqlite errors alike: report, never repair
            text = str(exc)
            if 'migration is required' in text:
                raise AgentViewError('This memory uses an older schema. It migrates at the '
                                     "agent's next working activation; viewing never migrates it.") from exc
            raise AgentViewError(f'Memory unavailable: {text}') from exc

    # -- the card ------------------------------------------------------------------------------
    def card(self, key: str) -> dict:
        return self._card(self.find(key))

    def _card(self, home: AgentHome) -> dict:
        base = {'key': home.key, 'source': home.source, 'home_name': home.home.name}
        try:
            engine = self._open(home)
        except AgentViewError as exc:
            return dict(base, status='unavailable', reason=str(exc), name=home.home.name,
                        title=None, rank=_rank([]), avatar_uri=None)
        with engine:
            from aether.atelier.specialist_learning import active, browse
            if active(home.home):
                data=browse(home.home)
                return dict(base,status='ok',agent_id=engine.agent_id,name=engine.card()['name'] or engine.agent_id,technical=True,lessons=len(data['lessons']),paths=len(data['paths']))
            card = engine.card()
            conn = engine.cart.conn
            report = engine.open_report
            sessions = _opened(engine)
            project = conn.execute(
                "SELECT e.project_id, n.title, e.occurred_at FROM session_events e JOIN notes n "
                "ON n.note_id=e.project_id WHERE e.kind='project_work' "
                "ORDER BY e.occurred_at DESC, e.event_seq DESC LIMIT 1").fetchone()
            return dict(
                base, status='ok', agent_id=card['agent_id'], name=card['name'] or card['agent_id'],
                title=card['scan_line'] or None, rank=_rank(card['specialties']),
                avatar_uri=_avatar_uri(card.get('avatar_svg')),
                avatar_description=card.get('avatar_description'),
                specialties=card['specialties'], stats=card['stats'], memory=card['memory'],
                recent_reviews=card['recent_reviews'],
                seal={'status': report.status, 'sealed': report.sealed, 'tampered': report.tampered},
                revision=engine.revision, last_session=sessions[0] if sessions else None,
                last_project=dict(project) if project else None)

    def agents(self) -> list:
        return [self._card(home) for home in self.homes()]

    # -- the Welcome Dashboard, as the agent sees it --------------------------------------------
    def sessions(self, key: str) -> list:
        with self._open(self.find(key)) as engine:
            memory_fabric()
            from memory_fabric.lifecycle import closure_outcome
            return [dict(s, state=closure_outcome(engine.cart, s['event_id'])['state']) for s in _opened(engine)]

    def dashboard(self, key: str, session: str = LATEST) -> dict:
        """session: '' for the agent's latest opening, 'none' for no session context, or an event ID."""
        home = self.find(key)
        with self._open(home) as engine:
            openings = _opened(engine)
            if session == LATEST:
                chosen = openings[0]['event_id'] if openings else None
            elif session == NO_SESSION:
                chosen = None
            else:
                row = engine.cart.conn.execute("SELECT 1 FROM session_events WHERE event_id=? AND kind='opened'",
                                               (session,)).fetchone()
                if row is None:
                    raise AgentViewError('That session is not an opening in this memory.')
                chosen = session
            result = engine.dashboard(session_id=chosen)
            result['viewing'] = {'agent': home.key, 'session_id': chosen,
                                 'basis': ('Live view of its notes, using the latest session context.'
                                           if session == LATEST and chosen else
                                           'Live view of its notes without session context: continuity cards cannot name a predecessor.'
                                           if chosen is None else 'Live view of its notes, using the selected session context.')}
            return result

    def history(self, key: str, before: Optional[int] = None) -> dict:
        """Complete journal, newest first, in bounded pages. Cursor survives new appends."""
        if before is not None and not 1 <= before <= 2**63 - 1:
            raise ValueError('before must be a positive SQLite event sequence')
        with self._open(self.find(key)) as engine:
            with engine.cart.read_snapshot():
                conn = engine.cart.conn
                rows = conn.execute('SELECT * FROM session_events ' +
                                    ('WHERE event_seq < ? ' if before is not None else '') +
                                    'ORDER BY event_seq DESC LIMIT 51',
                                    (before,) if before is not None else ()).fetchall()
                events = [dict(r) for r in rows[:50]]
                return {'agent': key, 'events': events,
                        'next_before': events[-1]['event_seq'] if len(rows) > 50 else None,
                        'retrieved_at': engine.cart.sync_clock()}

    def note(self, key: str, note_id: str, revision: Optional[int] = None) -> dict:
        with self._open(self.find(key)) as engine:
            try:
                if revision is None:
                    note = engine.note(note_id)
                else:
                    note = engine.maintain(action='reconstruct', id=note_id, at_revision=revision)['note']
            except Exception as exc:
                raise AgentViewError(f'Note unavailable: {exc}') from exc
            return {'note_id': note_id, 'title': note['title'], 'body': note['body'],
                    'created_at': note.get('created_at'), 'source_ref': note.get('source_ref'),
                    'at_revision': revision}

    def memory_page(self, key: str, *, include_proprietary: bool = False) -> str:
        """The existing Memory Fabric viewer, one self-contained page."""
        with self._open(self.find(key)) as engine:
            memory_fabric()
            from memory_fabric import viewer
            return viewer.render_html(viewer.snapshot(engine, include_proprietary=include_proprietary))
