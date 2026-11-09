"""Machine-local workplace settings and the lifecycle of its place; no model calls at setup."""
from __future__ import annotations

import json
import re
import socket
import threading
import time
from pathlib import Path

from .place import Place, _age, MIN_LIVE_SECONDS
from .participants import Participant


def place_records(share):
    records = []
    for path in sorted((Path(share) / 'places').glob('*.json')):
        try:
            row = json.loads(path.read_text(encoding='utf-8'))
            age = _age(row.get('heartbeat'))
            row['alive'] = age < max(MIN_LIVE_SECONDS, 2 * float(row.get('interval') or 3)) and not row.get('stopped')
            row['age'] = round(age, 1) if age != float('inf') else None
            records.append(row)
        except (OSError, ValueError, TypeError):
            continue
    return records


NETWORK_FILE = 'network.json'      # the network's own name, inside the shared folder every computer joins
NETWORK_NAMES = ('aether', 'aether network', 'aethernetwork', 'aether-network', 'aether_network')


def network_name(folder) -> str:
    try:
        name = json.loads((Path(folder) / NETWORK_FILE).read_text(encoding='utf-8')).get('name')
        if isinstance(name, str) and name.strip():
            return name.strip()
    except (OSError, ValueError, AttributeError):
        pass
    return ''


def name_network(folder, name):
    """Give the network folder its name: computers join a network, and the folder is how they share it."""
    text = ' '.join(str(name or '').split())[:80]
    if not text:
        raise ValueError('Give the network a name.')
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    from aether.integrity.sealbox import atomic_write_bytes
    atomic_write_bytes(folder / NETWORK_FILE, (json.dumps(dict(name=text), indent=2) + '\n').encode('utf-8'))
    return text


def _looks_like_network(folder: Path) -> bool:
    return (folder / NETWORK_FILE).is_file() or (folder / 'places').is_dir() or (folder / 'daybooks').is_dir()


_discovery_lock = threading.Lock()
_discovery_workers = {}
_discovery_results = {}


def discover_networks(timeout=1.5) -> list:
    """Network folders at the top of this computer's drives and mapped network drives (Windows), found in the time
    given; a drive that does not answer in time is skipped. Nothing is written."""
    import os
    import string
    if os.name != 'nt':
        return []
    workers = []

    def look(drive):
        found = []
        try:
            for child in Path(drive).iterdir():
                if child.is_dir() and child.name.lower() in NETWORK_NAMES and _looks_like_network(child):
                    found.append(str(child))
        except OSError:
            pass
        finally:
            with _discovery_lock:
                _discovery_results[drive] = found
    with _discovery_lock:
        for letter in string.ascii_uppercase[2:]:
            drive = f'{letter}:\\'
            worker = _discovery_workers.get(drive)
            if worker is None or not worker.is_alive():
                _discovery_results.pop(drive, None)
                worker = threading.Thread(target=look, args=(drive,), daemon=True)
                _discovery_workers[drive] = worker
                worker.start()
            workers.append(worker)
    deadline = time.monotonic() + timeout
    for worker in workers:
        worker.join(max(0.0, deadline - time.monotonic()))
    with _discovery_lock:
        return sorted({folder for rows in _discovery_results.values() for folder in rows})


def here_name() -> str:
    """This computer's place name, as the join form suggests it: lowercase letters, digits, hyphens, underscores."""
    name = re.sub(r'[^a-z0-9_-]+', '-', socket.gethostname().lower()).strip('-_')[:64]
    return name if name and name[0].isalnum() else 'this-computer'


class Workplace:
    def __init__(self, path, *, place_factory=Place):
        self.path = Path(path)
        self.factory = place_factory
        self.lock = threading.RLock()
        self.worker = self.place = None
        self.error = ''
        self.messages = []
        self.agent_registry = None
        self.campaign_roster = None
        self.pending = None             # the account whose hosting resumes when it next signs in (O2)

    def config(self):
        return json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}

    def active(self):
        return self.worker is not None and self.worker.is_alive()

    def save(self, body):
        with self.lock:
            if self.active():
                raise ValueError('Leave the network, and let the current turn finish, before changing this computer’s options.')
            share = Path(str(body.get('share') or '')).expanduser()
            if not str(body.get('share') or '').strip() or not share.is_absolute():
                raise ValueError('Choose an absolute shared workplace folder.')
            name = str(body.get('name') or socket.gethostname()).lower().strip()
            if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', name):
                raise ValueError('Use letters, numbers, hyphens or underscores for the place name.')
            homes = body.get('homes') or []
            if not isinstance(homes, list):
                raise ValueError('Homes must be a list of local folders.')
            homes = [str(Path(h).resolve()) for h in homes]
            for home in homes:
                mind = Participant.load(Path(home))
                if mind.home.resolve() != Path(home).resolve():
                    raise ValueError('The participant record points at another home: ' + home)
            profiles = str(body.get('profiles') or '')
            if homes and not Path(profiles).is_file():
                raise ValueError('Choose the runtime profiles file for these minds.')
            share.mkdir(parents=True, exist_ok=True)
            value = dict(share=str(share), name=name, homes=homes, profiles=profiles)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix('.tmp')
            temp.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
            temp.replace(self.path)
            return self.status()

    def log(self, text):
        self.messages = (self.messages + [str(text)])[-25:]

    def status(self):
        config = self.config()
        places = place_records(config['share']) if config else []
        library = None
        if config and (Path(config['share']) / 'libraries/lunch-check/catalog.json').is_file():
            from aether.library import Library
            shelf = Library(Path(config['share']), 'lunch-check')
            library = dict(loans=sum(e.get('lends', 0) for e in shelf.entries().values()),
                           verified=shelf.verify()['ok'])
        return dict(config=config, running=self.active(), stopping=bool(self.place and self.place._stop),
                    working=getattr(self.place, 'current_turn', None) if self.active() else None,
                    error=self.error, messages=list(self.messages), places=places,
                    host=socket.gethostname(), settings=str(self.path), library=library)

    def start_here(self, share, profiles=None):
        """One computer (GUI review S7): join this installation's own campaigns folder under this computer's name,
        hosting every campaign's team, then start. An existing connection is used as it is."""
        with self.lock:
            if not self.config():
                self.save(dict(share=str(share), name=here_name(), homes=[], profiles=str(profiles or '')))
            return self.start()

    # Networks: the page says "join a network"; underneath, a computer saves the shared folder as its
    # workplace and starts its place there. Joining or leaving never deletes anything.
    def _remembered_path(self):
        return self.path.parent / 'networks.json'

    def remembered(self) -> list:
        try:
            rows = json.loads(self._remembered_path().read_text(encoding='utf-8'))
            return [str(r) for r in rows if isinstance(r, str)]
        except (OSError, ValueError, TypeError):
            return []

    def remember(self, folder):
        rows = [r for r in self.remembered() if r.casefold() != str(folder).casefold()]
        self._remembered_path().parent.mkdir(parents=True, exist_ok=True)
        self._remembered_path().write_bytes((json.dumps([str(folder)] + rows[:19], indent=2) + '\n').encode('utf-8'))

    def networks(self, local, *, scan=True) -> list:
        """Every network this computer can see: its own, the one it joined, the ones it joined before, and any found
        on its drives; each with its computers and whether this computer works in it now."""
        config = self.config()
        current = config.get('share')
        candidates = [('local', str(local))]
        if current:
            candidates.append(('joined', current))
        candidates += [('remembered', r) for r in self.remembered()]
        if scan:
            candidates += [('found', f) for f in discover_networks()]
        out, seen = [], set()
        for origin, folder in candidates:
            key = str(Path(folder)).casefold()
            if key in seen:
                continue
            seen.add(key)
            reachable = origin == 'local' or Path(folder).is_dir()     # this computer's own folder is made on joining
            nodes = place_records(folder) if reachable else []
            name = (network_name(folder) if reachable else '') or (
                'This computer’s network' if origin == 'local' else Path(folder).name or str(folder))
            out.append(dict(folder=str(folder), name=name, local=origin == 'local', reachable=reachable,
                            current=bool(current) and key == str(Path(current)).casefold(),
                            nodes=[dict(name=n.get('name'), host=n.get('host'), online=bool(n.get('alive')),
                                        stopped=bool(n.get('stopped')), age=n.get('age'), agents=n.get('minds') or [])
                                   for n in nodes],
                            online=sum(bool(n.get('alive')) for n in nodes)))
        return out

    def join_network(self, folder, *, name=None, node=None, profiles=None):
        """Join this computer to the network in `folder` and start working there."""
        with self.lock:
            folder = str(folder or '').strip()
            if not folder or not Path(folder).expanduser().is_absolute():
                raise ValueError('Choose a network, or type the full path of its shared folder (for example '
                                 '\\\\machine1\\aether).')
            config = self.config()
            if self.active():
                if config.get('share') and Path(config['share']).resolve() == Path(folder).resolve():
                    return self.status()
                raise ValueError('This computer is working in another network. Leave it first; a turn in progress '
                                 'finishes before it leaves.')
            node = node or config.get('name') or here_name()
            if not config or Path(config.get('share', '')).resolve() != Path(folder).resolve() or node != config.get('name'):
                self.save(dict(share=folder, name=node, homes=config.get('homes') or [],
                               profiles=config.get('profiles') or str(profiles or '')))
            if name:
                name_network(folder, name)
            elif not network_name(folder):
                name_network(folder, 'Aether network')
            self.remember(folder)
            return self.start()

    def start(self):
        with self.lock:
            if self.active():
                return self.status()
            config = self.config()
            if not config:
                raise ValueError('Join a network first.')
            if any(p['alive'] and p.get('name') == config['name'] for p in place_records(config['share'])):
                raise ValueError('A computer with this name is already working in the network. Give each computer its own name.')
            self.error = ''
            place = self.factory(Path(config['share']), [Path(h) for h in config['homes']],
                                 name=config['name'], profiles_path=Path(config['profiles']) if config['profiles'] else None,
                                 log=self.log)
            place.agent_registry = self.agent_registry
            place.halt_check = getattr(self, 'halt_check', lambda: False)
            if self.campaign_roster:
                from .campaign_roster import CampaignRoster
                place.campaign_roster = CampaignRoster(lambda: place.share, self.agent_registry,
                                                       self.campaign_roster.profiles_path)
            if place.claimed_elsewhere():
                raise ValueError('A live place already hosts one of these minds: ' + str(place.claimed_elsewhere()))
            self.place = place

            def run():
                try:
                    place.serve()
                except Exception as exc:
                    self.error = f'{type(exc).__name__}: {exc}'
                    self.log(self.error)
            # Retain the process until a turn finishes; never kill a model call at window close.
            from aether.keys.runtime_keys import inherited
            self.worker = threading.Thread(target=inherited(run), name='atelier-place-' + config['name'], daemon=False)
            self.worker.start()
            self._hosting(True)
            return self.status()

    def stop(self):
        """Leave the network: the turn in flight finishes, and this computer stays out after a restart."""
        with self.lock:
            self._hosting(False)
            return self._finish()

    def _finish(self):
        with self.lock:
            if self.place and self.active():
                self.place.request_stop('Mission Control')
            return self.status()

    @property
    def hosting_path(self):
        return self.path.with_name('hosting.json')

    def _hosting(self, value: bool):
        from aether.keys.runtime_keys import principal
        try:
            self.hosting_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.hosting_path.with_suffix('.tmp')
            # Who joined: a resumed place works for that account, with its own live grants, never anyone else's.
            temp.write_text(json.dumps(dict(hosting=value, by=principal.get() if value else None,
                                            at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))) + '\n',
                            encoding='utf-8')
            temp.replace(self.hosting_path)
        except OSError as exc:
            self.log(f'Could not record whether this computer is hosting: {exc}')

    def resume(self, account=None):
        """After a restart, take up hosting again if this computer was hosting when it stopped without a Leave
        (a crash, a closed console). Only Leave network keeps it out. Never raises; a reason is logged instead
        (First Principles 1 and 2; rehearsals 1 and 4, O2).

        A place works with the keys of the account that joined. Started at boot there is no account, and background
        work never borrows one (runtime_keys), so hosting that an account began waits for that account to sign in;
        the server then calls resume(account) inside that account's own request."""
        try:
            intent = json.loads(self.hosting_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        if not intent.get('hosting') or self.active() or not self.config():
            self.pending = None
            return None
        if intent.get('by') and account != intent['by']:
            if self.pending != intent['by']:
                self.log('This computer was hosting when it stopped. Hosting resumes when the account that joined '
                         'signs in again.')
            self.pending = intent['by']
            return None
        self.pending = None
        self.log('Hosting again after a restart: this computer was in the network when it stopped.')
        try:
            return self.start()
        except (ValueError, OSError) as exc:
            if 'already working in the network' not in str(exc):
                self.log(f'Could not host again after the restart: {exc} Join network when ready.')
                return None
        # The stopped place's last heartbeat still looks live for up to MIN_LIVE_SECONDS (live check, 10-28): try again
        # in the background until it ages out. Leave network, or a Join by hand, ends the wait.
        def again():
            deadline, last = time.monotonic() + 3 * MIN_LIVE_SECONDS, 'the stopped place still looks alive.'
            while time.monotonic() < deadline:
                time.sleep(2)
                try:
                    if self.active() or not json.loads(self.hosting_path.read_text(encoding='utf-8')).get('hosting'):
                        return
                    self.start()
                    self.log('Hosting again: the stopped place\'s heartbeat has aged out.')
                    return
                except (ValueError, OSError) as exc:
                    last = str(exc)
            self.log(f'Could not host again after the restart: {last} Join network when ready.')
        from aether.keys.runtime_keys import inherited
        threading.Thread(target=inherited(again), name='atelier-resume', daemon=True).start()
        return self.status()

    def close(self, timeout=5):
        self._finish()              # closing Mission Control is not leaving: hosting resumes at the next start
        if self.worker:
            self.worker.join(timeout)
        return not self.active()

    def prepare_library(self, body):
        """A library connection check for a second computer (tests and diagnostics; not on the page): author one
        test librarian, shelf and local-model profile."""
        from urllib.parse import urlsplit
        from aether.library import Library
        with self.lock:
            config = self.config()
            if not config or self.active():
                raise ValueError('Join a workplace, with this place stopped, before preparing the librarian.')
            endpoint = str(body.get('endpoint') or '').rstrip('/')
            parsed = urlsplit(endpoint)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('Use a model server URL without credentials, query or fragment.')
            model = str(body.get('model') or '').strip()
            if not model:
                raise ValueError('Enter the model identifier served by LM Studio.')
            home = self.path.parent / 'minds' / 'lunch-librarian'
            profiles = self.path.parent / 'library_profiles.json'
            desired = dict(profiles={'lunch-local': dict(endpoint=endpoint, requestedApiIdentifier=model,
                            dialect='openai', credential_ref=None, maxOutputTokens=2048, timeoutSecs=180)})
            if home.exists():
                existing = Participant.load(home)
                if (existing.id != 'lunch-librarian' or existing.library != 'lunch-check'
                        or existing.mailboxes != Path(config['share'])
                        or json.loads(profiles.read_text(encoding='utf-8')) != desired):
                    raise ValueError('The prepared librarian belongs to another setup; its home has been preserved.')
            else:
                profiles.parent.mkdir(parents=True, exist_ok=True)
                profiles.write_text(json.dumps(desired, indent=2), encoding='utf-8')
                Participant.author(home, id='lunch-librarian', kind='librarian', name='Lunch Librarian',
                    deployment='lunch-local', mailboxes=Path(config['share']), library='lunch-check',
                    roots={'.': 'read'}, system='You are the librarian of this commission. Keep the lunch-check collection. '
                    'Search and read what fits, lend it with shelf_lend, then explain your choice and cite its filename. '
                    'Do not invent items or operator decisions. When accepted, leave your closing words and rest.')
            shelf = Library(Path(config['share']), 'lunch-check', title='Library connection check')
            if 'connection-guide' not in shelf.entries():
                shelf.add('connection-guide', title='A guide to the library connection check', kind='note',
                          summary='How to recognise the known document delivered across places.',
                          tags=['library', 'connection', 'check'], files={'connection-guide.txt':
                          b'Library connection check\nVerification phrase: LUNCH-LIBRARY-01\n'
                          b'A successful loan reaches the commission as a readable file and is recorded in the library ledger.\n'},
                          source={'kind': 'fixture', 'name': 'library connection check'})
            return self.save(dict(config, homes=[str(home)], profiles=str(profiles)))
