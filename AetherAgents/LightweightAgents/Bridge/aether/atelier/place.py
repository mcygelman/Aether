"""A place: one machine's part of the network.

A place is a share it can reach (daybooks and mailboxes live there), the homes of the minds that live on this
machine, and a deployment (which model answers here). Its daemon does two things, forever or once: run the
postmaster (collect what minds wrote, deliver what is new) for every commission on the share, and wake every local
mind whose inbox has something. Four machines are four places on one share; a mind moves by moving its home.

The place writes a record on the share (`places/<name>.json`: host, pid, minds, deployments, heartbeat) so two
places never wake the same mind at once: a mind another live place claims is left to it, and `serve` says so. A
stop file (`places/<name>.stop`) or Ctrl-C ends the daemon after the turn in flight, never in the middle of one.
Every note a mind writes here carries this place's name.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from aether.mailbox import Mailbox

from .atelier import Atelier, AtelierStop
from .participants import Participant
from .postmaster import Postmaster
from . import faults
from .turn import run_turn, Deployment, ContextCapacityError, _tree

MIN_LIVE_SECONDS = 20.0      # a heartbeat younger than max(2 * interval, this) means the place is alive


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _age(stamp: str | None) -> float:
    if not stamp:
        return float('inf')
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds()
    except ValueError:
        return float('inf')


class PlaceClaimed(RuntimeError):
    """Another live place hosts one of these minds."""


class Place:
    def __init__(self, share: Path, homes: list, *, name: str | None = None, client=None, profiles_path: Path | None = None,
                 secrets=(), log=print, interval: float = 3.0, take: bool = False):
        self.share = Path(share)
        self.name = (name or socket.gethostname()).lower()
        self.minds = [Participant.load(Path(h)) for h in homes]
        self.client, self.profiles_path, self.secrets, self.log = client, profiles_path, tuple(secrets), log
        self.interval, self.take = float(interval), bool(take)
        self._clients: dict = {}
        self._client_profiles: dict = {}
        self._profile_trouble: set = set()      # deployments whose profile could not be read; said once
        self._setup_waiting: dict = {}
        self._capacity_waiting: dict = {}
        self._waiting: dict = {}
        self._stop = False
        self._resting = set()
        self._record_lock = threading.Lock()
        self._pulse_stop = threading.Event()
        self._started = None
        self.current_turn = None
        self.agent_registry = None
        self.campaign_roster = None
        self.halt_check = lambda: False
        self._left_to: dict = {}
        self.records_dir = self.share / 'places'
        self.records_dir.mkdir(parents=True, exist_ok=True)

    def campaign_minds(self, cid):
        return self.campaign_roster.effective(cid, self.minds) if self.campaign_roster else self.minds

    def local_minds(self):
        minds = {m.id: m for m in self.minds}
        if self.campaign_roster:
            for cid in self.commissions():
                for mind in self.campaign_minds(cid):
                    minds.setdefault(mind.id, mind)
        return list(minds.values())

    # -- the record on the share ------------------------------------------------------------------------
    @property
    def record_path(self) -> Path:
        return self.records_dir / f'{self.name}.json'

    @property
    def stop_path(self) -> Path:
        return self.records_dir / f'{self.name}.stop'

    def record(self, **extra) -> dict:
        local = self.local_minds()
        hosted = local if not self.agent_registry else [m for m in local if
                  self.agent_registry.available(m.id, m.home) or self.agent_registry.is_working(m.id)]
        body = dict(name=self.name, host=socket.gethostname(), pid=os.getpid(), minds=[m.id for m in hosted],
                    participants=[m.record() for m in hosted],
                    deployments=sorted({m.deployment for m in local}), interval=self.interval,
                    heartbeat=_now(), working=self.current_turn, stopping=self._stop, **extra)
        text = json.dumps(body, indent=1)
        with self._record_lock:                        # the pulse thread and the round loop both write it
            tmp = self.record_path.with_suffix(f'.{os.getpid()}.tmp')
            tmp.write_text(text, encoding='utf-8')
            for attempt in range(20):                  # another place may be reading the record right now (Windows)
                try:
                    tmp.replace(self.record_path)
                    return body
                except PermissionError:
                    time.sleep(0.05 * (attempt + 1))
            self.record_path.write_text(text, encoding='utf-8')
            try:
                tmp.unlink()
            except OSError:
                pass
        return body

    def _pulse(self) -> None:
        """Heartbeat from its own thread, so a long turn (minutes of tool calls) never makes this place look dead
        to another machine, which would otherwise take its minds over mid-turn (seen 10-19 by the Project C run)."""
        while not self._pulse_stop.wait(max(self.interval, 5.0)):
            try:
                self.heartbeat()
            except OSError:
                pass

    def heartbeat(self) -> None:
        self.record(started=self._started)

    def others(self) -> list:
        """Every other place's record on the share, with its age in seconds and whether it counts as alive."""
        out = []
        for path in sorted(self.records_dir.glob('*.json')):
            if path.name == self.record_path.name:
                continue
            if path.suffix != '.json' or path.name.endswith('.tmp'):
                continue
            try:
                body = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            age = _age(body.get('heartbeat'))
            window = max(2 * float(body.get('interval') or self.interval), MIN_LIVE_SECONDS)
            out.append(dict(body, age=round(age, 1), alive=age < window and not body.get('stopped')))
        return out

    def claimed_elsewhere(self) -> dict:
        """mind id -> the live place that hosts it, for the minds this place would also host."""
        mine = {m.id for m in self.local_minds()}
        claimed = {}
        for other in self.others():
            if other['alive']:
                for mid in other.get('minds', ()):
                    if mid in mine:
                        claimed[mid] = other['name']
        return claimed

    def is_tender(self) -> bool:
        """The tender of a share asks the atelier's questions and delivers mail: the live place with the lowest name.
        Every place collects its own minds' notes and wakes its own minds; only one tends, so nothing is asked or
        delivered twice. A lone place is always the tender."""
        return not any(o['alive'] and o['name'] < self.name for o in self.others())

    # -- deployments --------------------------------------------------------------------------------------
    def commissions(self) -> list:
        base = self.share / 'daybooks'
        return [p.name for p in sorted(base.iterdir()) if (p / 'daybook.jsonl').exists()] if base.exists() else []

    def client_for(self, mind: Participant):
        """Reload from one profile snapshot; publish a replacement only after construction succeeds.

        Read, parse, configuration and client-construction errors retain a working
        client and are reported once. Without a cached client, the error remains explicit.
        """
        if self.client is not None:
            return self.client
        mind.delegation_profiles_snapshot = {}
        profiles_path = getattr(mind, 'campaign_profiles_path', self.profiles_path)
        cache_key = (str(profiles_path), mind.deployment) if hasattr(mind, 'campaign_profiles_path') else mind.deployment
        if profiles_path is None:
            raise RuntimeError('No deployment: pass --profiles or a client')
        try:
            raw = json.loads(Path(profiles_path).read_text(encoding='utf-8'))
            mind.delegation_profiles_snapshot = raw
            profile = (raw.get('profiles') or raw.get('document', {}).get('profiles') or {})[mind.deployment]
            if cache_key not in self._clients or self._client_profiles.get(cache_key) != profile:
                candidate = Deployment.from_profile(profile).client()
                self._clients[cache_key] = candidate
                self._client_profiles[cache_key] = profile
        except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
            if cache_key in self._clients:
                if cache_key not in self._profile_trouble:
                    self._profile_trouble.add(cache_key)
                    self.log(f"{mind.name}: deployment profile '{mind.deployment}' cannot be reloaded right now "
                             f"({type(exc).__name__}: {exc}); keeping the client already built")
                return self._clients[cache_key]
            raise
        if cache_key in self._profile_trouble:
            self._profile_trouble.discard(cache_key)
            self.log(f"{mind.name}: deployment profile '{mind.deployment}' usable again")
        return self._clients[cache_key]

    @staticmethod
    def _memory_revision(mind):
        """Read-only change detection; memory never needs an acknowledgment or repair to proceed."""
        from . import mind_memory
        try:
            pointer = mind.cartridge()
            if not pointer:
                return None
            with mind_memory._open_read(mind.home) as engine:
                return (pointer, engine.revision)
        except Exception as exc:
            return (type(exc).__name__, str(exc))

    def _capacity_inputs(self, atelier, mind, box, client, root):
        """Inputs that can change a fresh wake; host bookkeeping alone never schedules a retry.

        This is a per-process retry cache, not an admission state. A restarted place
        may try once again. No history is rewritten and the inbox remains unread.
        """
        rows = atelier.daybook.read()
        return dict(
            note=next((r['id'] for r in reversed(rows) if r['sender'].get('kind') != 'host'), None),
            inbox=[n['id'] for n in box.inbox(commission=atelier.campaign_id) if n['sender'].get('kind') != 'host'],
            deployment=(mind.deployment, id(client), *(getattr(client, k, None) for k in
                         ('model', 'endpoint', 'context_window', 'max_tokens'))),
            identity=mind.system_text(),
            continuity=mind.continuity(tags=mind.tags, max_experiences=0 if mind.cartridge() else 6),
            participant=(mind.kind, mind.name, list(mind.tags), dict(mind.roots), mind.library),
            tree=_tree(root), memory=self._memory_revision(mind))

    def _setup_inputs(self, atelier, mind, box):
        profiles = getattr(mind, 'campaign_profiles_path', self.profiles_path)
        try:
            stat = Path(profiles).stat() if profiles else None
            version = (stat.st_mtime_ns, stat.st_size) if stat else None
        except OSError:
            version = None
        from .readiness import _keys
        return dict(deployment=(str(profiles), mind.deployment, version),
                    participant=(mind.kind, str(mind.home)), keys=_keys(),
                    notes=[r['id'] for r in atelier.daybook.read() if r['sender'].get('kind') != 'host'
                           and r.get('kind') != 'turn_failed'],
                    inbox=[n['id'] for n in box.inbox(commission=atelier.campaign_id)
                           if n['sender'].get('kind') != 'host' and n.get('kind') != 'turn_failed'])

    def kinds(self) -> dict:
        return {m.id: m.kind for m in self.minds}

    def names(self) -> dict:
        return {m.id: m.name for m in self.minds}

    def delivery_directory(self, cid=None):
        """The tending place must know remote roles too: an invitation to librarian crosses machines."""
        people = {m.id: m.record() for m in self.minds}
        for record in self.others():
            if record['alive']:
                for who in record.get('participants', []):
                    if isinstance(who, dict) and who.get('id') and who.get('kind'):
                        people.setdefault(who['id'], who)
        if cid and self.campaign_roster:
            from .campaign_roster import assignments
            for aid, row in assignments(self.share, cid)['members'].items():
                people[aid] = dict(people.get(aid) or dict(id=aid, name=aid), kind=row['role'])
            for mind in self.campaign_minds(cid):
                people[mind.id] = mind.record()
        return ({pid: who['kind'] for pid, who in people.items()},
                {pid: who.get('name', pid) for pid, who in people.items()})

    # -- stopping -----------------------------------------------------------------------------------------
    def request_stop(self, why: str = 'asked') -> None:
        if not self._stop:
            self.log(f'[{self.name}] stop requested ({why}); finishing the turn in flight')
        self._stop = True

    def stop_requested(self) -> bool:
        if not self._stop and self.stop_path.exists():
            self.request_stop('stop file')
        return self._stop

    # -- one round ----------------------------------------------------------------------------------------
    def round(self) -> dict:
        """One round over every commission: exchange, wake the local minds that have mail, exchange again."""
        report = dict(commissions={}, turns=[], failed=[], skipped=[])
        claimed = self.claimed_elsewhere()
        for mid, where in claimed.items():
            if self._left_to.get(mid) != where:
                self.log(f'[{self.name}] {mid} is hosted by live place {where}; leaving it to them (serve --take to move it here)')
            self._left_to[mid] = where
        for mid in list(self._left_to):
            if mid not in claimed:
                self._left_to.pop(mid)
        tender = self.is_tender()
        delivery_kinds, delivery_names = self.delivery_directory()
        if tender != getattr(self, '_was_tender', None):
            self.log(f'[{self.name}] ' + ('tending this share (asks and delivers)' if tender else 'another place tends this share; collecting and waking my own minds only'))
            self._was_tender = tender
        mine = [m.id for m in self.minds]
        for cid in self.commissions():
            if self.stop_requested():
                break
            atelier = Atelier(self.share, cid, secrets=self.secrets, place=self.name)
            post = Postmaster(atelier, self.share)
            campaign_minds = self.campaign_minds(cid)
            mine = [m.id for m in campaign_minds]
            for mind in campaign_minds:
                Mailbox(self.share, mind.record())
            delivery_kinds, delivery_names = self.delivery_directory(cid)
            try:
                if tender:
                    atelier.tend()
                post.exchange(delivery_kinds, delivery_names, only=mine, deliver=tender)
            except AtelierStop as stop:
                self.log(f'[{cid}] stopped: {stop}'); report['commissions'][cid] = f'stopped: {stop.reason}'; continue
            waiting = atelier.waiting_for_operator()
            if waiting:
                if self._waiting.get(cid) != waiting:
                    self.log(f'[{cid}] waiting for the operator ({waiting}); no mind is woken until the operator speaks')
                self._waiting[cid] = waiting
                report['commissions'][cid] = f'waiting: {waiting}'
                continue
            self._waiting.pop(cid, None)
            root = atelier.daybook.root.parent.parent / 'work' / cid
            root.mkdir(parents=True, exist_ok=True)
            self.file_decisions(atelier, cid)                # rulings reach every local mind before any of them wakes
            for selected in campaign_minds:
                # Resolve once immediately before each turn. An in-flight turn keeps its snapshot.
                mind = next((m for m in self.campaign_minds(cid) if m.id == selected.id), None)
                if mind is None:
                    continue
                claimed = self.claimed_elsewhere()
                if self.stop_requested():
                    break
                if tender:
                    atelier.tend()                       # ask what is now askable before the next mind wakes
                if atelier.waiting_for_operator():
                    break                                # a stop recorded during this round holds the rest of it
                if mind.id in claimed:
                    report['skipped'].append(dict(commission=cid, mind=mind.id, hosted_by=claimed[mind.id]))
                    continue
                if self.agent_registry and not self.agent_registry.available(mind.id, mind.home):
                    report['skipped'].append(dict(commission=cid, mind=mind.id, reason='agent_unavailable'))
                    continue
                box = Mailbox(self.share, mind.record())
                if self.resting(atelier, mind.id):
                    for note in box.inbox(commission=cid):       # its closing words are filed: it rests, and its
                        box.mark_read(note['id'])                # mail is read for it, until the operator speaks again
                    if (cid, mind.id) not in self._resting:
                        self._resting.add((cid, mind.id)); self.log(f'[{cid}] {mind.name}: closure filed; resting')
                    continue
                if not box.inbox(commission=cid):
                    continue
                reserved = self.agent_registry.acquire(mind.id, mind.home) if self.agent_registry else True
                if not reserved:
                    report['skipped'].append(dict(commission=cid, mind=mind.id, reason='agent_unavailable'))
                    continue
                key = (cid, mind.id)
                inputs = None
                previous_failure = self._setup_waiting.get(key)
                try:
                    setup_inputs = self._setup_inputs(atelier, mind, box)
                    if previous_failure and previous_failure['inputs'] == setup_inputs:
                        if time.monotonic() < previous_failure['retry_at']:
                            report['skipped'].append(dict(commission=cid, mind=mind.id, reason='connection_retry_backoff'))
                            continue
                    else:
                        self._setup_waiting.pop(key, None)
                        previous_failure = None
                    client = self.client_for(mind)
                    try:
                        inputs = self._capacity_inputs(atelier, mind, box, client, root)
                    except Exception:
                        inputs = None          # retry bookkeeping cannot prevent an otherwise usable turn
                    key = (cid, mind.id)
                    if key in self._capacity_waiting:
                        if inputs is not None and self._capacity_waiting[key] == inputs:
                            report['skipped'].append(dict(commission=cid, mind=mind.id, reason='context_capacity_unchanged'))
                            continue
                        self._capacity_waiting.pop(key)
                        self.log(f'[{cid}] {mind.name}: wake inputs or deployment changed; retrying automatically')
                    unread = box.inbox(commission=cid)
                    self.current_turn = dict(commission=cid, participant=mind.id,
                                             in_reply_to=unread[-1]['id'] if unread else None)
                    try:
                        self.heartbeat()                 # the record says a turn is in flight from this moment
                    except OSError:
                        pass
                    sent = run_turn(mind, atelier, box, client, root, place=self.name,
                                    profiles_path=self.profiles_path, stopped=lambda: self.stop_requested() or self.halt_check())
                    self._setup_waiting.pop(key, None)
                    report['turns'].append(dict(commission=cid, mind=mind.id, sent=str(sent) if sent else None))
                    self.log(f'[{cid}] {mind.name}: ' + ('wrote a note' if sent else 'nothing to add'))
                    if sent:
                        try:                    # its note and its cost enter the daybook now, so the next mind
                            post.collect([mind.id])   # woken this round sees the budget that is really left
                        except AtelierStop as stop:
                            self.log(f'[{cid}] stopped: {stop}'); report['commissions'][cid] = f'stopped: {stop.reason}'
                            break
                except Exception as exc:
                    if isinstance(exc, ContextCapacityError) and exc.cost['model_calls'] == 0 and inputs is not None:
                        # begin_work may have recorded this activation. That host write
                        # is not new input; later owner memory changes still resume work.
                        inputs['memory'] = self._memory_revision(mind)
                        self._capacity_waiting[key] = inputs
                        self.log(f'[{cid}] {mind.name}: wake cannot fit; leaving unread mail in place until inputs or deployment change')
                    from .atelier import setup_failure
                    setup_kind = setup_failure(dict(kind='turn_failed', sender=dict(kind='host'),
                                                     text=f'{mind.name}: your last turn did not complete: {type(exc).__name__}: {exc}'))
                    duplicate = False
                    if setup_kind:
                        duplicate = bool(previous_failure and previous_failure['kind'] == setup_kind)
                        delay = min(300, previous_failure['delay'] * 2) if duplicate else 30
                        self._setup_waiting[key] = dict(inputs=self._setup_inputs(atelier, mind, box),
                                                       kind=setup_kind, delay=delay, retry_at=time.monotonic() + delay)
                    if not duplicate and not getattr(exc, 'recorded', False):
                        atelier.turn_failed(mind.record(), f'{type(exc).__name__}: {exc}', dict(trace=traceback.format_exc()[-4000:]))
                    report['failed'].append(dict(commission=cid, mind=mind.id, error=str(exc)))
                    self.log(f'[{cid}] {mind.name}: turn failed: {exc}')
                finally:
                    if self.agent_registry:
                        self.agent_registry.release(mind.id)
                    self.current_turn = None
                    try:
                        self.heartbeat()                 # and that it is over, so a later death is not blamed on it
                    except OSError:
                        pass
            try:
                post.exchange(delivery_kinds, delivery_names, only=mine, deliver=tender)
            except AtelierStop as stop:
                self.log(f'[{cid}] stopped: {stop}'); report['commissions'][cid] = f'stopped: {stop.reason}'
            self.file_decisions(atelier, cid)
            self.file_closures(atelier, cid, report)
            report['commissions'].setdefault(cid, 'ok')
        return report

    def recover_interrupted(self) -> list:
        """A place that died mid-turn left its record saying so (`working`). Say it in that commission's daybook, as a
        record and not as mail: a model call may have been made, files under the commission root may have changed, and
        no note exists for that turn. The mind's mail is still unread, so it is woken normally; nothing is replayed,
        nothing is asked of anyone, and a turn that completed leaves no such record (v0.1)."""
        try:
            body = json.loads(self.record_path.read_text(encoding='utf-8')) if self.record_path.exists() else {}
        except (OSError, ValueError):
            return []
        working = body.get('working')
        if not working or body.get('pid') == os.getpid() or body.get('stopped'):
            return []
        try:
            atelier = Atelier(self.share, working['commission'], place=self.name)
            if any(((r.get('facts') or {}).get('interrupted') or {}).get('heartbeat') == body.get('heartbeat') for r in atelier.daybook.read()):
                return []
            mind = next((m for m in self.campaign_minds(working['commission']) if m.id == working['participant']), None)
            who = mind.name if mind else working['participant']
            kind = mind.kind if mind else 'member'
            box = Mailbox(self.share, dict(id=working['participant'], kind=kind))
            unread = box.inbox(commission=working['commission'])
            reply_to = working.get('in_reply_to') or (unread[-1]['id'] if unread else None)
            in_outbox = bool(reply_to and any(item.get('in_reply_to') == reply_to for _, item in box.pending()))
            where = f"place {self.name} (pid {body.get('pid')}) stopped mid-turn after its heartbeat at {body.get('heartbeat')}"
            if in_outbox:          # the turn got as far as writing its note: it is collected this round, and not written twice
                text_ = (f"{who}'s turn was interrupted: {where}. Its note was written and is collected now; files it wrote are "
                         f"attached to that note. Nothing is uncertain about that turn and nothing is repeated.")
                uncertain = []
            else:                  # the turn died before its note: what the model said, if anything, is lost
                text_ = (f"{who}'s turn was interrupted: {where}. A model call may have been made and files under the commission root "
                         f"may have changed; no note exists for that turn and its cost is not in this record. The mail stays unread; "
                         f"{who} is woken again.")
                uncertain = ['model_call', 'files', 'cost']
            atelier.note(text_, facts=dict(interrupted=dict(participant=working['participant'], place=self.name, pid=body.get('pid'),
                                                           heartbeat=body.get('heartbeat'), note_in_outbox=in_outbox, uncertain=uncertain)))
            self.log(f"[{working['commission']}] {who}: the previous run of this place died mid-turn; recorded, mail left unread")
            return [working]
        except Exception as exc:
            self.log(f'[{self.name}] could not record the interrupted turn: {type(exc).__name__}: {exc}')
            return []

    @staticmethod
    def resting(atelier: Atelier, mind_id: str) -> bool:
        """A mind whose closing words are filed rests: it is not woken again for this commission unless the operator
        speaks after the filing. Without this, every closing note woke the others into more closing notes, each
        refiled as a new closure (10-19, the blind score's fresh arm: 53 notes for a commission closed at 36)."""
        filed_at = None
        for note in atelier.daybook.read():
            facts = note.get('facts') or {}
            if note['kind'] == 'host' and facts.get('filed') and (facts['filed'].get('participant') or {}).get('id') == mind_id:
                filed_at = note['n']
            elif filed_at is not None and note['sender'].get('kind') == 'operator':
                filed_at = None
        return filed_at is not None

    def file_decisions(self, atelier: Atelier, cid: str) -> None:
        """The operator's rulings, and those the lead recorded in the operator's words, into every local mind's cartridge,
        once each (a decision's source is its daybook note and line). A record, never a stop."""
        from .mind_memory import scope_for
        for d in atelier.facts().get('decisions') or ():
            lead = d.get('recorded_by_lead')
            by = d['by'].get('name') or d['by']['id']
            decided_by = f'{by} (lead, recording the operator)' if lead else by
            source = f"{d['message_id']}#{d['index']}"
            for mind in self.campaign_minds(cid):
                if mind.id in self._left_to or (self.agent_registry and not self.agent_registry.available(mind.id, mind.home)) or not mind.cartridge():
                    continue
                if mind.technical():
                    from .specialist_context import bind
                    bind(mind,atelier.daybook.root.parent.parent/'work'/cid,cid)
                done = mind.file_decision(d['words'], subject=d.get('subject'), scope=scope_for(d['words'], cid), decided_by=decided_by,
                                          source_ref=source, decided_at=d.get('at'), commission_id=cid)
                if done and done.get('id') and not done.get('already'):
                    self.log(f"[{cid}] {mind.name}: decision filed ({done['scope']}, {done['subject']}): {d['words'][:80]}")

    def file_closures(self, atelier: Atelier, cid: str, report: dict) -> None:
        """A local mind's closing note, once the atelier has it, is filed into that mind's own home (and cartridge),
        and the atelier records that it happened. The mind's words are filed as written."""
        facts = atelier.facts()
        for closure in facts['closures']:
            if closure.get('filed'):
                continue
            mind = next((m for m in self.campaign_minds(cid) if m.id == closure['participant']['id']), None)
            if mind is None or mind.id in self._left_to:
                continue
            try:
                if mind.technical():
                    from .specialist_context import bind
                    bind(mind,atelier.daybook.root.parent.parent/'work'/cid,cid)
                written = mind.file_closure(closure['text'], commission_id=cid, tags=mind.tags)
                faults.point('after_closure_filed')
                if str(written.get('cartridge_note', '')).startswith('unavailable:'):
                    raise RuntimeError('closure cartridge filing incomplete: ' + written['cartridge_note'])
                record = mind.file_record(facts, cid)          # what others said and verified, into its own record
                if record:
                    written = dict(written, record=record)
                if mind.cartridge() and not mind.technical():
                    try:
                        from .mind_memory import render_page
                        written = dict(written, page=str(render_page(mind.home)))     # the mind's page, for anyone to look at
                    except Exception as exc:
                        written = dict(written, page=f'not rendered: {type(exc).__name__}')
                destination="project continuity" if mind.technical() else f"{mind.name}'s home"
                atelier.filed(mind.record(), destination, dict(paths=written))
                report.setdefault('filed', []).append(dict(commission=cid, mind=mind.id, **written))
                self.log(f'[{cid}] filed {mind.name}\'s closure into {destination}' + (f"; page {written['page']}" if written.get('page') else ''))
            except Exception as exc:
                atelier.note(f"Could not file {mind.name}'s closure: {type(exc).__name__}: {exc}", facts=dict(filing_failed=dict(participant=mind.record())))
                report['failed'].append(dict(commission=cid, mind=mind.id, error='filing: ' + str(exc)))

    # -- the daemon ---------------------------------------------------------------------------------------
    def serve(self, *, interval: float | None = None, rounds: int | None = None) -> dict:
        """Run rounds until `rounds` are done, a stop is asked for, or forever. Returns the final record."""
        if interval is not None:
            self.interval = float(interval)
        claimed = self.claimed_elsewhere()
        if claimed and not self.take:
            lines = ', '.join(f'{mid} by {where}' for mid, where in sorted(claimed.items()))
            raise PlaceClaimed(f'another live place hosts: {lines}. Stop it, wait for its heartbeat to age, or pass --take.')
        self._started = _now()
        self.recover_interrupted()
        for mind in self.local_minds():
            Mailbox(self.share, mind.record())
        if self.stop_path.exists():
            self.stop_path.unlink()
        self.heartbeat()
        self._pulse_stop.clear()
        from aether.keys.runtime_keys import inherited
        pulse = threading.Thread(target=inherited(self._pulse), name=f'pulse-{self.name}', daemon=True)
        pulse.start()
        self.log(f'[{self.name}] serving {len(self.minds)} mind(s) on {self.share} (pid {os.getpid()})')
        handlers = {}
        for sig in (getattr(signal, 'SIGINT', None), getattr(signal, 'SIGTERM', None), getattr(signal, 'SIGBREAK', None)):
            if sig is not None:
                try:
                    handlers[sig] = signal.signal(sig, lambda s, f: self.request_stop('signal'))
                except (ValueError, OSError):
                    pass
        n = 0
        try:
            while (rounds is None or n < rounds) and not self.stop_requested():
                n += 1
                report = self.round()
                try:
                    self.heartbeat()
                except OSError as exc:
                    self.log(f'[{self.name}] heartbeat not written this round: {exc}')
                if rounds is None and not report['turns'] and not report['failed'] and not self.stop_requested():
                    time.sleep(self.interval)
        finally:
            self._pulse_stop.set()
            pulse.join(timeout=10)
            for sig, previous in handlers.items():
                try:
                    signal.signal(sig, previous)
                except (ValueError, OSError):
                    pass
            final = self.record(started=self._started, stopped=_now(), rounds=n)
            if self.stop_path.exists():
                self.stop_path.unlink()
            self.log(f'[{self.name}] stopped after {n} round(s); record at {self.record_path}')
        return final
