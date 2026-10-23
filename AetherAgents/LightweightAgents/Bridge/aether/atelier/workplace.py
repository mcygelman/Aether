"""Machine-local workplace settings and the lifecycle of its place; no model calls at setup."""
from __future__ import annotations

import json
import re
import socket
import threading
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


class Workplace:
    def __init__(self, path, *, place_factory=Place):
        self.path = Path(path)
        self.factory = place_factory
        self.lock = threading.RLock()
        self.worker = self.place = None
        self.error = ''
        self.messages = []

    def config(self):
        return json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}

    def active(self):
        return self.worker is not None and self.worker.is_alive()

    def save(self, body):
        with self.lock:
            if self.active():
                raise ValueError('Stop this place and let its turn finish before changing its connection.')
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

    def start(self):
        with self.lock:
            if self.active():
                return self.status()
            config = self.config()
            if not config:
                raise ValueError('Join a workplace first.')
            if any(p['alive'] and p.get('name') == config['name'] for p in place_records(config['share'])):
                raise ValueError('That place is already running. Use a different name for each machine.')
            self.error = ''
            place = self.factory(Path(config['share']), [Path(h) for h in config['homes']],
                                 name=config['name'], profiles_path=Path(config['profiles']) if config['profiles'] else None,
                                 log=self.log)
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
            self.worker = threading.Thread(target=run, name='atelier-place-' + config['name'], daemon=False)
            self.worker.start()
            return self.status()

    def stop(self):
        with self.lock:
            if self.place and self.active():
                self.place.request_stop('Mission Control')
            return self.status()

    def close(self, timeout=5):
        self.stop()
        if self.worker:
            self.worker.join(timeout)
        return not self.active()

    def prepare_library(self, body):
        """An explicit operator action: author one test assistant, shelf and local-model profile."""
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
                          source={'kind': 'fixture', 'name': 'Praxis lunch check'})
            return self.save(dict(config, homes=[str(home)], profiles=str(profiles)))
