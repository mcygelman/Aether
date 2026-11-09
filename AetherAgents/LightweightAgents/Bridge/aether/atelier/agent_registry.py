"""Installation-local availability. Agent homes and campaign records are read-only inputs."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from itertools import islice
from pathlib import Path
import threading

from aether.integrity.sealbox import atomic_write_bytes


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_json(path, limit=65536):
    with Path(path).open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('Agent metadata is too large.')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('Agent metadata must be an object.')
    return value


def metadata(folder):
    if not isinstance(folder, str) or not folder.strip() or not Path(folder).is_absolute():
        raise ValueError('Choose the absolute path of an existing agent folder.')
    home = Path(folder).resolve(strict=True)
    if not home.is_dir():
        raise ValueError('Choose an agent folder.')
    participant = home / 'participant.json'
    pointer = home / 'memory/.cartridge.json'
    bridge_pointer = home / 'memory/cartridge.json'
    if participant.is_file():
        value = read_json(participant)
        aid, name = value.get('id'), value.get('name')
        description = value.get('description') or value.get('kind') or 'Agent'
    elif pointer.is_file() or bridge_pointer.is_file():
        value = read_json(pointer if pointer.is_file() else bridge_pointer)
        aid, name, description = value.get('owner'), home.name, 'Agent with a memory cartridge'
    else:
        raise ValueError('This folder has no participant record or agent memory pointer.')
    if not isinstance(aid, str) or not aid.strip() or len(aid) > 160 or any(ord(c) < 32 for c in aid):
        raise ValueError('The agent metadata needs a stable identity (participant id or memory owner).')
    return dict(id=aid, name=str(name or aid)[:200], description=str(description)[:500],
                home=str(home), metadata_revision=digest(value), weight=weight(home),specialist='specialist' in value.get('tags',[]))


def weight(home) -> str:
    """Lightweight: an Atelier participant record (it may also carry a memory cartridge). Heavyweight: a mind with only
    its Memory Fabric cartridge pointer, born through the consent dialogue; on a project team it works as a member."""
    return 'lightweight' if (Path(home) / 'participant.json').is_file() else 'heavyweight'


SCAN_DEPTH = 2          # <folder>/<agent> and <folder>/<team>/<agent>, as the starter team is written
SCAN_LIMIT = 400        # folders looked at per search folder, so a large tree never slows the page


def scan(folder) -> list:
    """Agent homes in and under `folder`, read-only, nearest first; folders that are not agents are skipped."""
    found, seen, queue = [], 0, [(Path(folder), 0)]
    while queue and seen < SCAN_LIMIT:
        path, depth = queue.pop(0)
        seen += 1
        try:
            found.append(metadata(str(path)))
            continue                     # an agent's own subfolders are its memory, not other agents
        except (OSError, ValueError):
            pass
        if depth < SCAN_DEPTH:
            try:
                remaining = max(0, SCAN_LIMIT - seen - len(queue))
                # Bound enumeration too: sorting an entire large folder defeats SCAN_LIMIT.
                children = sorted(islice(path.iterdir(), remaining))
                queue.extend((child, depth + 1) for child in children
                             if not child.name.startswith(('.', '__')) and child.is_dir())
            except OSError:
                continue
    return found


class AgentRegistry:
    def __init__(self, path, *, legacy_homes=lambda: (), campaign_roots=lambda: (), default_folder=None):
        self.path = Path(path)
        self.legacy_homes, self.campaign_roots = legacy_homes, campaign_roots
        self.default_folder = str(Path(default_folder).resolve()) if default_folder else None
        self.lock = threading.RLock()
        self.active = {}  # Reservations serialize turn start against ejection, not the whole turn.
        self.reservations = {}

    def _saved(self):
        if not self.path.exists():
            return {}
        doc = read_json(self.path, 4 * 1024 * 1024)
        if doc.get('version') != 1 or not isinstance(doc.get('agents'), dict):
            raise ValueError('The agent registry cannot be read. Its contents were preserved.')
        for aid, row in doc['agents'].items():
            if not isinstance(row, dict) or row.get('id') != aid or row.get('state') not in ('available', 'ejected'):
                raise ValueError('The agent registry contains an invalid entry. Its contents were preserved.')
        return doc['agents']

    def _entries(self):
        entries = self._saved()
        # Compatibility: existing configured/discovered agents remain available until explicitly ejected.
        # GET never persists the discovery or opens a memory cartridge.
        for home in self.legacy_homes():
            try:
                row = metadata(str(home))
            except (OSError, ValueError):
                continue
            entries.setdefault(row['id'], dict(row, state='available'))
        return entries

    def _write(self, entries):
        atomic_write_bytes(self.path, (json.dumps(dict(version=1, agents=entries), indent=2) + '\n').encode())

    def usage(self, row):
        from .atelier import Atelier
        from .projection import people, exists
        found, complete = [], True
        try:
            roots = {str(Path(p).resolve()) for p in self.campaign_roots()}
        except (OSError, ValueError):
            return [], False
        for root in sorted(roots):
            try:
                if not Path(root).is_dir():
                    complete = False
                    continue
                paths = sorted((Path(root) / 'daybooks').glob('*/daybook.jsonl'))
            except OSError:
                complete = False
                continue
            for path in paths:
                cid = path.parent.name
                if not exists(root, cid):
                    continue
                try:
                    atelier = Atelier(Path(root), cid)
                    notes, facts = atelier.daybook.read(), atelier.facts()
                    members = people(notes, facts)
                    # Older roster notes can name an agent instead of storing its stable id.
                    matches = [p for p in members if p.get('id') == row['id'] or
                               (p.get('id') == p.get('name') == row['name'])]
                    from .campaign_roster import assignments
                    if matches or row['id'] in assignments(root, cid)['members']:
                        found.append(dict(id=cid, root=root, label=(notes[0]['text'][:100] if notes else cid)))
                except (OSError, ValueError, KeyError, TypeError):
                    complete = False
        return found, complete

    def _display(self, row):
        result = dict(row)
        running = self.active.get(row['id'], 0)
        result['status'] = 'ejection_pending' if row['state'] == 'ejected' and running else row['state']
        result['working'] = bool(running)
        result['problem'] = None
        try:
            current = metadata(row['home'])
            result['specialist']=current.get('specialist',False)
            if current['id'] != row['id']:
                raise ValueError('The folder now identifies a different agent.')
        except (OSError, ValueError) as exc:
            result['problem'] = str(exc)
            if row['state'] == 'available':
                result['status'] = 'unavailable'
        result['campaigns'], result['usage_complete'] = self.usage(row)
        result['weight'] = weight(row['home'])
        return result

    # Where to look for agents that can be added (the operator chooses from a list, and can set a folder location).
    # Saved beside the registry; the installation's agents folder is the default. Reading a folder never registers.
    def _folders_path(self):
        return self.path.with_name('agent_folders.json')

    def folders(self, entries=None) -> list:
        """The saved locations, else the installation's agents folder plus every folder that already holds an agent."""
        try:
            saved = json.loads(self._folders_path().read_text(encoding='utf-8')).get('folders')
            if isinstance(saved, list):
                return [str(f) for f in saved if isinstance(f, str) and f.strip()]
        except (OSError, ValueError, AttributeError):
            pass
        found = [self.default_folder] if self.default_folder else []
        for row in (entries if entries is not None else self._entries()).values():
            parent = str(Path(row['home']).parent)
            inside = any(parent.casefold() == f.casefold() or parent.casefold().startswith(f.casefold().rstrip('\\/') + ('\\' if '\\' in f else '/'))
                         for f in found)            # a folder inside a listed one is already scanned
            if not inside:
                found.append(parent)
        return found

    def set_folders(self, folders) -> dict:
        if not isinstance(folders, list):
            raise ValueError('Send the agent folders as a list.')
        clean = []
        for folder in folders:
            text = str(folder or '').strip()
            if not text:
                continue
            if not Path(text).is_absolute():
                raise ValueError('Use the full path of each agent folder: ' + text[:120])
            if text.casefold() not in {c.casefold() for c in clean}:
                clean.append(text)
        atomic_write_bytes(self._folders_path(), (json.dumps(dict(folders=clean[:20]), indent=2) + '\n').encode())
        return self.listing()

    def candidates(self, entries) -> list:
        """Agents found in the search folders that this installation has not added yet."""
        homes = {str(Path(r['home']).resolve()).casefold() for r in entries.values()}
        out, seen = [], set()
        for folder in self.folders(entries):
            for row in scan(folder):
                key = row['home'].casefold()
                if key in homes or key in seen:
                    continue
                seen.add(key)
                out.append(dict(row, status='addable', folder=folder, registered_elsewhere=row['id'] in entries))
        return sorted(out, key=lambda r: (r['name'].casefold(), r['home']))

    def listing(self):
        with self.lock:
            entries = self._entries()
            return dict(agents=[self._display(row) for row in sorted(entries.values(), key=lambda r: (r['name'].casefold(), r['id']))],
                        revision=digest(entries), default_folder=self.default_folder,
                        folders=self.folders(entries), candidates=self.candidates(entries))

    def heavyweights(self) -> list:
        """Available heavyweights this installation has added, for an Admin's Chat: read-only, no usage or folder scan."""
        with self.lock:
            out = []
            for row in self._entries().values():
                if row['state'] != 'available':
                    continue
                try:
                    current = metadata(row['home'])
                except (OSError, ValueError):
                    continue
                if current['id'] == row['id'] and current['weight'] == 'heavyweight':
                    out.append(dict(row, working=bool(self.active.get(row['id']))))
            return out

    def preview(self, action, value):
        with self.lock:
            entries = self._entries()
            if action == 'inject':
                row = metadata(value)
                existing = entries.get(row['id'])
                if existing and Path(existing['home']).resolve() != Path(row['home']):
                    raise ValueError('This identity is already registered at ' + existing['home'] +
                                     '. Resolve the folder conflict before injecting; no duplicate was created.')
                for other in entries.values():
                    if Path(other['home']).resolve() == Path(row['home']) and other['id'] != row['id']:
                        raise ValueError('This folder is registered to another identity. Its registration was preserved.')
                if self.active.get(row['id']) and existing and existing['state'] == 'ejected':
                    raise ValueError('Ejection is pending. Cancel it before reinjecting this agent.')
                row['state'] = existing['state'] if existing else 'ejected'
                already = bool(existing and existing['state'] == 'available')
            elif action == 'eject':
                if value not in entries:
                    raise ValueError('This agent is not registered.')
                row, already = entries[value], False
            else:
                raise ValueError('Unknown agent action.')
            review = dict(action=action, agent=self._display(row), revision=digest(entries), already_available=already)
            return dict(review=review, token=digest(review))

    def confirm(self, review, token):
        if not isinstance(review, dict) or not isinstance(token, str):
            raise ValueError('Review the agent action first.')
        with self.lock:
            action = review.get('action')
            row = review.get('agent') or {}
            current = self.preview(action, row.get('home') if action == 'inject' else row.get('id'))
            if current['token'] != token or digest(review) != token:
                raise ValueError('The agent or its campaign usage changed. Review the action again.')
            entries = self._entries()
            row = {k: current['review']['agent'][k] for k in ('id', 'name', 'description', 'home', 'metadata_revision')}
            row['state'] = 'available' if action == 'inject' else 'ejected'
            entries[row['id']] = row
            self._write(entries)
            return self.listing()

    def cancel_eject(self, aid):
        with self.lock:
            entries = self._entries()
            row = entries.get(aid)
            if not row or row['state'] != 'ejected' or not self.active.get(aid):
                raise ValueError('Making the agent unavailable has completed. Use Make available to bring it back.')
            row['state'] = 'available'
            self._write(entries)
            return self.listing()

    def available(self, aid, home=None):
        with self.lock:
            entries = self._entries()
            row = entries.get(aid)
            # Legacy conversation rosters sometimes retain a name instead of an id.
            if row is None:
                matches = [r for r in entries.values() if r['name'] == aid]
                if matches:
                    return all(self.available(r['id'], home) for r in matches)
            if row is None:
                return True  # Preserve pre-existing runners that do not use installation discovery.
            if row['state'] != 'available':
                return False
            try:
                return metadata(row['home'])['id'] == aid and (home is None or Path(home).resolve() == Path(row['home']).resolve())
            except (OSError, ValueError):
                return False

    def acquire(self, aid, home=None):
        with self.lock:
            if not self.available(aid, home):
                return False
            keys = tuple(self._identities(aid))
            self.reservations.setdefault(aid, []).append(keys)
            for key in keys:
                self.active[key] = self.active.get(key, 0) + 1
            return True

    def _identities(self, aid):
        entries = self._entries()
        return [aid] if aid in entries else ([r['id'] for r in entries.values() if r['name'] == aid] or [aid])

    def is_working(self, aid):
        with self.lock:
            return bool(self.active.get(aid))

    def release(self, aid):
        with self.lock:
            held = self.reservations.get(aid, [])
            keys = held.pop() if held else ()
            if not held:
                self.reservations.pop(aid, None)
            for key in keys:
                count = self.active.get(key, 0) - 1
                if count > 0:
                    self.active[key] = count
                else:
                    self.active.pop(key, None)

    @contextmanager
    def turn(self, aid, home=None):
        allowed = self.acquire(aid, home)
        try:
            yield allowed
        finally:
            if allowed:
                self.release(aid)
