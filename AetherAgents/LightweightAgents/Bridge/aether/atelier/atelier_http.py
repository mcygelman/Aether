"""The atelier page and its API: one thread per commission, every participant's notes in it.

Routes (the gateway admits only this server's own pages; POST is checked again here):
  GET  /projects/atelier                                     the page
  GET  /api/atelier                                          the commissions here, the most recently written first
  POST /api/atelier/open  {text, files?, request_kind?}       the letter; request_kind is project (default) or library
  GET  /api/atelier/read?commission=<id>[&since=<note id>]   notes (after `since`, when given), facts, budget, numbers, chain
  POST /api/atelier/post  {commission, text, files?, to?}    an operator's note: anything, "go" and "accepted" included
  POST /api/atelier/wake  {commission, participant}          ask the atelier to run that participant's turn now
  GET  /api/atelier/file?commission=<id>&sha256=<hex>        one file version, by hash, as a download

A file in a request is {name, content_base64}; the atelier keeps it once by hash and the note carries {name, sha256,
bytes}. A stop (money, consent, secrets, harm) is not an error: the atelier wrote a note that says why, and the answer
names the stop. The turn runner is whatever the server was given (`state['atelier_turn']`, called as
run_turn(atelier, participant, since_message_id)), else `aether.atelier.turn.run_turn` when that module is present.
Nothing here previews, confirms, holds or re-welcomes.
"""
from __future__ import annotations

import base64
import binascii
import re
import threading
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from aether.integrity.http_body import json_body
from aether.paths import LIGHTWEIGHT_ROOT, WEB_DIR

from . import projection
from .atelier import Atelier, AtelierStop
from .daybook import DaybookError

PAGE = projection.PAGE
API = '/api/atelier'
PATHS = {PAGE, API, API + '/open', API + '/read', API + '/post', API + '/wake', projection.FILE_API}
PAGE_FILE = WEB_DIR / 'atelier.html'
BODY_LIMIT = 24 * 1024 * 1024          # a 16 MiB file in base64 with room to spare
HEX = re.compile(r'^[0-9a-f]{64}$')
OPERATOR = dict(id='operator', kind='operator', name='Operator')



class NotHere(LookupError):
    """No such commission or file in this atelier (404)."""


_guard = threading.Lock()
_locks: dict = {}
_working: dict = {}                     # (atelier folder, commission, participant id) -> thread


def root_of(state) -> Path:
    """Where this server's ateliers live: the configured root, else the installation's data root."""
    workplace = state.get('workplace')
    if workplace and workplace.config():
        return Path(workplace.config()['share'])
    return Path(state.get('atelier_root') or LIGHTWEIGHT_ROOT / 'atelier')


def _lock(root, commission):
    with _guard:
        return _locks.setdefault(str(Path(root).resolve() / commission), threading.RLock())


class SharedAtelier(Atelier):
    """An atelier whose writes take one lock per commission, so a turn's notes and the operator's notes are each
    appended whole and in order. Two writers must not corrupt one file; nothing else waits on this."""

    def __post_init__(self):
        super().__post_init__()
        self.lock = _lock(self.root, self.campaign_id)
        append = self.daybook.append

        def locked(*args, **kwargs):
            with self.lock:
                return append(*args, **kwargs)
        self.daybook.append = locked


def _locked(name):
    def method(self, *args, **kwargs):
        with self.lock:
            return getattr(Atelier, name)(self, *args, **kwargs)
    method.__name__ = name
    return method


for _name in ('open', 'post', 'note', 'closure', 'filed', 'turn_failed', 'mark_read'):
    setattr(SharedAtelier, _name, _locked(_name))


def atelier(state, commission) -> SharedAtelier:
    secrets = state.get('atelier_secrets')
    return SharedAtelier(root_of(state), commission, secrets=tuple(secrets() if callable(secrets) else secrets or ()))


def _commission(state, value) -> str:
    if not projection.exists(root_of(state), value):
        raise NotHere('No such project here: ' + str(value)[:80])
    return value


def _operator(state) -> dict:
    return dict(state.get('atelier_operator') or OPERATOR)


def _files(target: SharedAtelier, body: dict, sender: dict) -> list:
    """Keep each attached file by hash; return the references the note carries."""
    refs = []
    for item in body.get('files') or ():
        if not isinstance(item, dict) or not isinstance(item.get('name'), str) or not isinstance(item.get('content_base64'), str):
            raise ValueError('Attach a file as {name, content_base64}')
        try:
            data = base64.b64decode(item['content_base64'], validate=True)
        except (binascii.Error, ValueError):
            raise ValueError('The file ' + item['name'][:80] + ' is not base64') from None
        refs.append(target.daybook.store_file(item['name'], data, sender))
    return refs


def _text(body: dict) -> str:
    text = body.get('text', '')
    if not isinstance(text, str):
        raise ValueError('A note is text')
    return text


def open_commission(state, body: dict) -> dict:
    text, operator = _text(body), _operator(state)
    request_kind = body.get('request_kind', 'project')
    if request_kind not in ('project', 'library', 'chat'):
        raise ValueError('Choose a team project, a library request or a chat.')
    agent = body.get('agent') if request_kind == 'chat' else None
    if request_kind == 'chat' and (not isinstance(agent, dict) or not isinstance(agent.get('id'), str) or not agent['id'].strip()):
        raise ValueError('Choose the agent to chat with.')
    if not text.strip():
        raise ValueError('Write the commission letter first: what to deliver, why, what helps, the budget.')
    commission = 'c-' + uuid.uuid4().hex[:12]
    target = atelier(state, commission)
    refs = _files(target, body, operator)
    answer = dict(commission_id=commission)
    try:   # a brand-new commission: the letter is its first note, and its authorization
        if agent:
            to = dict(id=agent['id'], kind='member', name=str(agent.get('name') or agent['id'])[:200])
            answer['note'] = target.post(operator, text, files=refs, to=to,
                                         facts=dict(request_kind='chat', chat_with=agent['id']))
        else:
            answer['note'] = target.post(operator, text, files=refs, facts=dict(request_kind=request_kind))
    except AtelierStop as stop:
        answer.update(stop=stop.reason, detail=stop.detail)
    if state.get('campaign_roster') and projection.exists(root_of(state), commission):
        state['campaign_roster'].initialize_new(commission)
    return answer


def post_note(state, body: dict) -> dict:
    commission = _commission(state, body.get('commission'))
    target, operator = atelier(state, commission), _operator(state)
    text = _text(body)
    refs = _files(target, body, operator)
    if not text.strip() and not refs:
        raise ValueError('Write a note or attach a file')
    to = body.get('to')
    answer = dict(commission_id=commission)
    try:
        answer['note'] = target.post(operator, text, files=refs, to=to if isinstance(to, dict) else None)
    except AtelierStop as stop:
        answer.update(stop=stop.reason, detail=stop.detail)
    return answer


def turn_runner(state):
    runner = state.get('atelier_turn')
    if runner is None:
        try:
            from .turn import run_turn
            def runner(*args):
                return run_turn(*args, stopped=lambda: bool(state.get('workspace_store') and state['workspace_store'].halted()))
        except ImportError:
            return None
    return runner


def wake(state, body: dict) -> tuple:
    commission = _commission(state, body.get('commission'))
    # While this computer works in a network, its place takes the turns. Out of the network (never joined, or left), an
    # operator's explicit request runs the turn here (a chat must answer).
    if state.get('workplace') and state['workplace'].config() and state['workplace'].active():
        return 200, dict(commission_id=commission, started=False, managed=True,
                         detail='While this computer is joined to the network, agents take their turns as notes arrive. See Join Network.')
    who = body.get('participant')
    if not isinstance(who, dict) or not isinstance(who.get('id'), str) or who.get('kind') in (None, 'host', 'operator'):
        raise ValueError('Wake a participant: {id, kind, name}')
    participant = {k: str(who[k]) for k in ('id', 'kind', 'name') if who.get(k)}
    runner = turn_runner(state)
    if runner is None:
        return 503, dict(error='This build has no turn runner yet. Your notes are in the daybook; the participant reads '
                               'them when it wakes.', commission_id=commission, wired=False)
    target = atelier(state, commission)
    roster = state.get('campaign_roster')
    if roster and state.get('atelier_turn') is None:
        from .campaign_roster import assignments
        doc = assignments(root_of(state), commission)
        if not doc.get('inherit_defaults', True) and participant['id'] not in doc['members']:
            return 409, dict(error='Add this agent to the campaign roster before waking it.', started=False)
        if participant['id'] in doc['members']:
            mind = next((m for m in roster.effective(commission) if m.id == participant['id']), None)
            if mind is None:
                return 409, dict(error='This assigned agent is unavailable. Check the Agents page.', started=False)
            participant = mind.record()
            def runner(target, record, since):
                from aether.mailbox import Mailbox
                from .postmaster import Postmaster
                from .turn import run_turn, Deployment
                box = Mailbox(target.root, mind.record())
                Postmaster(target, target.root).deliver({mind.id: mind.kind}, {mind.id: mind.name})
                client = Deployment.load(mind.campaign_profiles_path, mind.deployment).client()
                root = Path(target.root) / 'work' / commission
                root.mkdir(parents=True, exist_ok=True)
                result = run_turn(mind, target, box, client, root,
                    stopped=lambda: bool(state.get('workspace_store') and state['workspace_store'].halted()))
                # The reply waits in the mind's outbox; outside a network no place collects it, so hand it to the
                # conversation now (a chat, or a turn asked for by hand, must show its answer).
                Postmaster(target, target.root).collect(only=[mind.id])
                return result
    key = (str(root_of(state)), commission, participant['id'])
    with _guard:
        running = _working.get(key)
        if running is not None and running.is_alive():
            return 200, dict(commission_id=commission, participant=participant, working=True, started=False)
        registry = state.get('agent_registry')
        if registry and not registry.acquire(participant['id']):
            return 409, dict(error='This agent is unavailable. Reinject it on the Agents page before starting another turn.', started=False)
        from aether.keys.runtime_keys import inherited
        thread = threading.Thread(target=inherited(_turn, runner, target, participant, key, registry), daemon=True,
                                  name='atelier-turn-' + participant['id'][:24])
        _working[key] = thread
        try:
            thread.start()
        except Exception:
            _working.pop(key, None)
            if registry:
                registry.release(participant['id'])
            raise
    return 202, dict(commission_id=commission, participant=participant, working=True, started=True)


def _turn(runner, target: SharedAtelier, participant: dict, key, registry=None) -> None:
    try:
        runner(target, participant, target.read_marks.get(participant['id']))
    except AtelierStop:
        pass                                          # the atelier wrote the stop into the daybook
    except Exception as exc:                          # noqa: BLE001 - every failed turn is written down
        from aether.keys.redaction import redact
        target.turn_failed(participant, redact(f'{type(exc).__name__}: {exc}')[:2000], dict(where='wake'))
    finally:
        if registry:
            registry.release(participant['id'])
        with _guard:
            if _working.get(key) is threading.current_thread():
                del _working[key]


def working(state, commission) -> list:
    with _guard:
        return [k[2] for k, t in _working.items() if k[:2] == (str(root_of(state)), commission) and t.is_alive()]


def settle(timeout: float = 30.0) -> None:
    """Wait for every turn started here (tests, shutdown)."""
    with _guard:
        threads = list(_working.values())
    for thread in threads:
        thread.join(timeout)


def _send_file(handler, state, query) -> None:
    commission = _commission(state, (query.get('commission') or [''])[0])
    sha = (query.get('sha256') or [''])[0]
    if not HEX.match(sha):
        raise ValueError('Name a file by its sha256')
    target = Atelier(root_of(state), commission)
    data = target.daybook.read_file(sha)
    names = [f['name'] for r in target.daybook.read() for f in r.get('files', ()) if f['sha256'] == sha]
    name = re.sub(r'[^A-Za-z0-9._-]', '_', (names[0].rsplit('/', 1)[-1] if names else sha))[:120] or sha
    handler.send_response(200)
    for header, value in (('Content-Type', 'application/octet-stream'), ('Content-Length', str(len(data))),
                          ('Content-Disposition', f'attachment; filename="{name}"'), ('Cache-Control', 'no-store'),
                          ('X-Content-Type-Options', 'nosniff'), ('Connection', 'close')):
        handler.send_header(header, value)
    handler.close_connection = True
    handler.end_headers()
    handler.wfile.write(data)


def handle_atelier(handler, method, state) -> bool:
    parsed = urlparse(handler.path)
    path = parsed.path
    if path not in PATHS:
        return False
    try:
        if method == 'POST' and handler.headers.get('Origin') != f'http://127.0.0.1:{handler.server.server_port}':
            handler.settings_send(dict(error='Write from the atelier page on this Mission Control.'), 403)
            return True
        query = parse_qs(parsed.query, keep_blank_values=True)
        get = {PAGE, API, API + '/read', projection.FILE_API}
        if (method == 'GET') != (path in get):
            handler.settings_send(dict(error=('GET' if path in get else 'POST') + ' is how this route is used'), 405)
            return True
        if path == PAGE:
            handler.settings_send(PAGE_FILE.read_bytes(), html=True)
        elif path == API:
            handler.settings_send(dict(commissions=projection.commissions(root_of(state)), runner=turn_runner(state) is not None))
        elif path == API + '/read':
            commission = _commission(state, (query.get('commission') or [''])[0])
            since = (query.get('since') or [''])[0] or None
            managed = bool(state.get('workplace') and state['workplace'].config())
            local = state['workplace'].status() if managed else None
            active = set(working(state, commission))
            if local:
                for place in local['places']:
                    turn = place.get('working') or {}
                    if (place.get('alive') and place['name'] != local['config']['name']
                            and turn.get('commission') == commission):
                        active.add(turn['participant'])
                turn = local.get('working') or {}
                if turn.get('commission') == commission:
                    active.add(turn['participant'])
            value = projection.thread(root_of(state), commission, since, active)
            if state.get('campaign_roster'):
                members = state['campaign_roster'].view(commission)['members']
                people = {p['id']: p for p in value['people']}
                for member in members:
                    aid = member['agent_id']
                    people[aid] = dict(people.get(aid) or {}, id=aid, name=member['name'], kind=member['role'],
                                       working=aid in active, available_here=member['available'])
                from .campaign_roster import assignments
                if not assignments(root_of(state), commission).get('inherit_defaults', True):
                    assigned = {m['agent_id'] for m in members}
                    people = {aid: p for aid, p in people.items() if aid in assigned}
                value['people'] = list(people.values())
            registry = state.get('agent_registry')
            if registry:
                for person in value['people']:
                    person['available_here'] = registry.available(person['id'])
            value['places_managed'] = bool(managed and local and local['running'])
            if local:
                value['availability'] = dict(running=local['running'], stopping=local['stopping'],
                                             error=local['error'], places=local['places'])
            handler.settings_send(value)
        elif path == projection.FILE_API:
            _send_file(handler, state, query)
        else:
            body = json_body(handler, BODY_LIMIT, 'Send the note as JSON')
            if not isinstance(body, dict):
                raise ValueError('Send the note as a JSON object')
            if path == API + '/open':
                handler.settings_send(open_commission(state, body))
            elif path == API + '/post':
                handler.settings_send(post_note(state, body))
            else:
                code, value = wake(state, body)
                handler.settings_send(value, code)
    except NotHere as exc:
        handler.settings_send(dict(error=str(exc)), 404)
    except (DaybookError, ValueError, TypeError, OSError) as exc:
        from aether.keys.redaction import redact
        handler.settings_send(dict(error=redact(str(exc))), 409)
    except Exception as exc:     # noqa: BLE001 - answer with the error, never drop the request
        from aether.keys.redaction import redact
        handler.settings_send(dict(error=redact(f'The atelier could not answer: {type(exc).__name__}: {exc}')), 500)
    return True
