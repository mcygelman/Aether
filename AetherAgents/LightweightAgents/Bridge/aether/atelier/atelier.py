"""The atelier: the workshop and the building, not the manager.

It delivers messages, keeps the record exact, derives the facts, builds a participant's wake context, meters the
budget, computes the three numbers, and stops for four things only: money, consent, secrets, harm. Everything
else is recorded and the work continues. A question the host has is a message in the atelier to the one who can
answer it, never a refusal of what was said.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .daybook import Daybook, DaybookError
from . import facts as facts_module

HOST = dict(id='host', kind='host', name='Aether')
STOP_REASONS = ('money', 'consent', 'secrets', 'harm')
QUIET_NOTES_PER_MIND = 2    # notes with nothing new, per member of the team, before the operator is told (Atelier._signpost)


def is_capacity_failure(note: dict) -> bool:
    """Physical request limits are host evidence, not mail or policy refusals."""
    return (note.get('kind') == 'turn_failed' and note.get('sender', {}).get('kind') == 'host'
            and 'context' in (note.get('facts', {}).get('evidence') or {}))


PLACEHOLDER_ENDPOINT = 'http://127.0.0.1:9/'     # installed_app.unconfigured_profiles: the setup placeholder answers nowhere


def setup_failure(note: dict) -> str | None:
    """'credentials', 'placeholder' or 'unreachable' when a turn never reached its model: setup, not a refusal.
    Read from the recorded reason (`<Type>: <message>`, as the place and the wake write it)."""
    if note.get('kind') != 'turn_failed' or note.get('sender', {}).get('kind') != 'host':
        return None
    reason = (note.get('text') or '').partition(': your last turn did not complete: ')[2]
    if reason.startswith(('CredentialError: ', 'VaultError: ')):
        return 'credentials'
    if reason.startswith('ModelError: model endpoint unreachable at '):
        return 'placeholder' if reason.startswith('ModelError: model endpoint unreachable at ' + PLACEHOLDER_ENDPOINT) else 'unreachable'
    return None


class AtelierStop(Exception):
    """One of the four reasons. The atelier records it and waits for the operator; nothing else is refused."""
    def __init__(self, reason: str, detail: str):
        if reason not in STOP_REASONS:
            raise ValueError('A stop is one of money, consent, secrets, harm')
        super().__init__(f'{reason}: {detail}')
        self.reason, self.detail = reason, detail


@dataclass
class Budget:
    model_calls: int | None = None
    seconds: float | None = None
    monetary: float | None = None


@dataclass
class Atelier:
    root: Path
    campaign_id: str
    budget: Budget = field(default_factory=Budget)
    secrets: tuple = ()                      # byte strings that must never enter the record (the leak scan)
    place: str | None = None                 # the place this atelier object runs in (stamped on what it appends)
    daybook: Daybook = field(init=False)
    read_marks: dict = field(default_factory=dict)   # participant id -> last message id read

    def __post_init__(self):
        self.daybook = Daybook(self.root, self.campaign_id, place=self.place)
        marks = self.daybook.root / 'read-marks.json'
        if marks.exists():
            self.read_marks = json.loads(marks.read_text(encoding='utf-8'))

    # -- opening and posting -----------------------------------------------------------------------------
    def open(self, operator: dict, text: str) -> dict:
        """The operator's first message opens the campaign and is its authorization."""
        if self.daybook.read():
            raise DaybookError('This room is already open; post to it')
        return self.post(operator, text)

    def post(self, sender: dict, text: str, *, files=(), cost=None, to=None, place=None, facts=None) -> dict:
        """Record what a participant said, derive facts, meter, and ask what is still needed."""
        held = self._withheld(text, files)
        if held:
            message = self.daybook.append(HOST, held, kind='withheld', to=sender, facts=dict(withheld=True))
            self._refresh()
            raise AtelierStop('secrets', held)
        message = self.daybook.append(sender, text, files=files, cost=cost, to=to, place=place, facts=facts)
        for cited in ((facts or {}).get('memory') or {}).get('cited') or ():
            # A mind's words cite a decision: the room hears what it rests on, right after the note (recorded, never a stop).
            from .mind_memory import render_citation
            self.daybook.append(HOST, render_citation(sender.get('name') or sender['id'], cited), kind='host',
                                to=dict(id='reviewer', kind='reviewer', name='the critic'), facts=dict(cited_decision=dict(cited, mind=sender, note=message['id'])))
        state = self._refresh()
        self._ask(state)
        if sender.get('kind') != 'operator':
            self._meter(state)
        return message

    def quiet(self, participant: dict, cost: dict | None, memory: dict | None = None) -> dict:
        """A mind read its mail and had nothing to add. Its thinking still cost; the daybook keeps the cost, and the
        recall receipt (what its wake offered and what it read) when it has a cartridge."""
        name = participant.get('name') or participant.get('id')
        message = self.daybook.append(HOST, f'{name} read the new notes and had nothing to add.', kind='host',
                                      cost=cost or None, facts=dict(quiet=participant, **(dict(memory=memory) if memory else {})))
        self._refresh()          # the meter speaks at the next note; a quiet turn is recorded, never a failure
        return message

    def note(self, text: str, *, facts=None, to=None) -> dict:
        """A host note in the atelier (hired, filed, budget, stop): a record, never a gate."""
        message = self.daybook.append(HOST, text, kind='host', facts=facts, to=to)
        self._refresh()
        return message

    def turn_failed(self, participant: dict, reason: str, evidence: dict | None = None, *, cost=None, files=()) -> dict:
        """Keep failed work attributable. Capacity evidence is record, not another inbox item."""
        capacity = 'context' in (evidence or {})
        message = self.daybook.append(HOST, f"{participant.get('name') or participant['id']}: your last turn did not complete: {reason}",
                                      kind='turn_failed', to=None if capacity else participant,
                                      facts=dict(participant=participant, evidence=evidence or {}), cost=cost, files=files)
        self._refresh()
        return message

    # -- waking a participant ----------------------------------------------------------------------------
    def wake_context(self, participant: dict, *, allowance_messages: int = 40) -> dict:
        """What a participant sees when it wakes: the facts so far (summary), the messages since it last read
        (bounded), its own prior closures from other daybooks under the same root (continuity the building offers),
        and the budget left. No check is passed; this is the atelier being shown to a colleague."""
        state = self.facts()
        since = self.read_marks.get(participant['id'])
        try:
            unread = self.daybook.read(since_id=since)
        except DaybookError:
            unread = self.daybook.read()
        # The last note this wake shows. A turn marks read through it, not through whatever arrived while it ran (run
        # a90: "Accepted." and the closing-words invitation came mid-turn, were marked read unseen, and Auditor never closed).
        seen_through = unread[-1]['id'] if unread else since
        unread = [m for m in unread if self._for(m, participant) and (m.get('sender') or {}).get('id') != participant['id']]
        return dict(campaign=state['campaign'], plan=state['plan'], roster=state['roster'], seen_through=seen_through,
                    approvals=state['approvals'], acceptance=state['acceptance'],
                    unread=unread[-allowance_messages:], unread_total=len(unread),
                    prior_closures=self.prior_closures(participant), budget_left=self.budget_left(state),
                    questions_for_me=[m['facts']['question'] for m in unread if m['kind'] == 'host' and m.get('facts', {}).get('question')
                                      and str((m.get('to') or {}).get('id') or '').lower() in
                                      {str(participant.get(k) or '').lower() for k in ('id', 'kind', 'name')} - {''}])

    def mark_read(self, participant: dict, message_id: str | None = None) -> None:
        last = message_id or (self.daybook.last() or {}).get('id')
        if last:
            self.read_marks[participant['id']] = last
            marks = self.daybook.root / 'read-marks.json'          # whole or not at all: a half-written file would stop every open
            tmp = marks.with_suffix('.json.tmp.' + uuid.uuid4().hex)
            try:
                tmp.write_text(json.dumps(self.read_marks, indent=1), encoding='utf-8')
                os.replace(tmp, marks)
            finally:
                tmp.unlink(missing_ok=True)

    def prior_closures(self, participant: dict) -> list:
        """This participant's closing messages from every other room under the root, newest first."""
        rooms = Path(self.root) / 'daybooks'
        found = []
        for path in sorted(rooms.glob('*/facts.json')) if rooms.exists() else []:
            if path.parent.name == self.campaign_id:
                continue
            try:
                other = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            for closure in other.get('closures', ()):
                if closure['participant']['id'] == participant['id']:
                    found.append(dict(campaign_id=other['campaign']['id'], text=closure['text'], filed=closure.get('filed', False)))
        return list(reversed(found))

    # -- closure -------------------------------------------------------------------------------------------
    def closure(self, participant: dict, text: str, *, cost=None) -> dict:
        """A participant's closing message, in its own words. Filing into its container is the turn runner's job;
        `filed()` records that it happened."""
        message = self.daybook.append(participant, text, cost=cost, facts=dict(closure=dict(participant=participant, text=text)))
        self._refresh()
        return message

    def filed(self, participant: dict, where: str, note: dict | None = None) -> dict:
        return self.note(f"Filed {participant.get('name') or participant['id']}'s closure into {where}.",
                         facts=dict(filed=dict(participant=participant, where=where, **(note or {}))))

    # -- facts, meter, numbers ---------------------------------------------------------------------------
    def facts(self) -> dict:
        return facts_module.rebuild(self.daybook)

    def budget_left(self, state: dict | None = None) -> dict:
        used = self.cost()
        limit = self.budget.model_calls
        if limit is None and state and state['campaign'].get('budget_model_calls'):
            limit = state['campaign']['budget_model_calls']
        return dict(model_calls_used=used['model_calls'], model_calls_limit=limit,
                    seconds_used=round(used['seconds'], 1), seconds_limit=self.budget.seconds,
                    monetary_used=round(used['monetary'], 4), monetary_limit=self.budget.monetary)

    def cost(self) -> dict:
        total = dict(model_calls=0, prompt_tokens=0, completion_tokens=0, seconds=0.0, monetary=0.0)
        for row in self.daybook.read():
            for key in total:
                total[key] += row.get('cost', {}).get(key, 0) or 0
        return total

    def numbers(self) -> dict:
        """The three numbers, from the record: thinking minutes per harness minute, refusals per hundred calls,
        approvals per sprint."""
        rows = self.daybook.read()
        if not rows:
            return dict(thinking_per_harness=None, refusals_per_hundred=None, approvals_per_sprint=None, capacity_failures=0,
                        setup_failures=0)
        from datetime import datetime
        first, last = datetime.fromisoformat(rows[0]['at']), datetime.fromisoformat(rows[-1]['at'])
        wall = max((last - first).total_seconds(), 0.0)
        thinking = sum(r.get('cost', {}).get('seconds', 0) or 0 for r in rows)
        harness = max(wall - thinking, 0.0)
        state = self.facts()
        calls = max(sum((r.get('cost') or {}).get('model_calls') or (1 if r['kind'] == 'message' and r['sender']['kind'] != 'operator' else 0) for r in rows), 1)
        capacity_failures = sum(is_capacity_failure(row) for row in rows)
        setup_failures = sum(bool(setup_failure(row)) for row in rows)
        refused = state['failed_turns'] - capacity_failures - setup_failures + state['withheld']
        sprints = max(len(state['plan']['sprints']), 1)
        return dict(thinking_per_harness=round(thinking / harness, 3) if harness else None,
                    refusals_per_hundred=round(100.0 * refused / calls, 2),
                    capacity_failures=capacity_failures, setup_failures=setup_failures,
                    approvals_per_sprint=round(facts_module.go_notes(state) / sprints, 2),
                    thinking_seconds=round(thinking, 1), harness_seconds=round(harness, 1), calls=calls)

    # -- internals -----------------------------------------------------------------------------------------
    def _refresh(self) -> dict:
        state = self.facts()
        facts_module.write(self.daybook, state)
        return state

    def tend(self) -> dict:
        """Look at the conversation again and ask whatever is now askable. A place does this every round, so a
        question the atelier could not see at post time (or after a repair to its reading) is still asked."""
        state = self.facts()
        self._ask(state)
        self._signpost(state)
        return state

    def _signpost(self, state: dict) -> None:
        """When the minds keep writing to one another with nothing new (no file, no word from the operator), tell the
        operator once, in a note only the operator receives. Nothing is held: the minds keep their rhythm, the wake's
        wording leads them to say readiness once and then rest, and the budget stays the backstop (Paved Roads, Not
        Fences). Rehearsal F6 (2026-10-27): three minds holding at the close woke each other for forty notes; in
        rehearsal 2 the wording alone kept the room quiet, and a hold placed here stopped a working team (F8)."""
        if state['acceptance'] or state['campaign'].get('request_kind') in ('chat', 'library') or self.waiting_for_operator():
            return
        notes = self.daybook.read()
        quiet = 0
        for note in reversed(notes):
            kind = note['sender'].get('kind')
            if kind == 'operator' or note.get('files'):
                break
            if note['kind'] == 'host' and (note.get('facts') or {}).get('signpost'):
                return                                   # said once since the operator last spoke
            if note['kind'] == 'message' and kind != 'host':
                quiet += 1
        # The team is every mind that has written here, as well as any the plan names: a team assigned on the Team page
        # is not in the plan's roster (rehearsal 2 rested after two notes because the plan named only one mind).
        team = {str(n['sender'].get('id')) for n in notes if n['kind'] == 'message' and n['sender'].get('kind') not in ('operator', 'host')}
        team |= {str(m.get('id') or m.get('name')) for m in state['roster']}
        if quiet >= QUIET_NOTES_PER_MIND * max(2, len(team)):
            self.note(f'The team has written {quiet} notes since the last file or word from you, with nothing new. They may '
                      'be waiting for your ruling: accept the result, say what to change, or say "carry on".',
                      facts=dict(signpost='circles'), to=dict(id='operator', kind='operator', name='Operator'))

    def _ask(self, state: dict) -> None:
        for q in facts_module.questions(state):
            self.daybook.append(HOST, q['text'], kind='host', to=dict(id=q['to'], kind=self._kind_of(q['to'], state)),
                                facts=dict(question=q))
        facts_module.write(self.daybook, self.facts())

    @staticmethod
    def _for(message: dict, participant: dict) -> bool:
        """A note is for a participant unless it is the atelier's own note addressed to someone else."""
        to = message.get('to')
        if message.get('kind') != 'host' or not to:
            return True
        mine = {str(participant.get(k, '')).lower() for k in ('id', 'kind', 'name')} - {''}
        target = {str(to.get(k, '')).lower() for k in ('id', 'kind', 'name')} - {''}
        if mine & target:
            return True
        import re
        words = {w for x in mine for w in re.split(r'[\s\-_]+', x) if len(w) >= 3}
        return any(t in words for t in target)

    def _kind_of(self, target: str, state: dict) -> str:
        if target in ('operator', 'lead', 'member', 'reviewer', 'librarian', 'host'):
            return target
        for member in state['roster']:
            if member.get('id') == target or member.get('name') == target:
                return member.get('kind', 'member')
        return 'member'

    def waiting_for_operator(self) -> str | None:
        """The reason the atelier is waiting on the operator (a stop it recorded with no operator note since), or
        None. A place does not wake minds while this is set: the conversation resumes when the operator speaks."""
        for note in reversed(self.daybook.read()):
            if note['sender'].get('kind') == 'operator':
                return None
            stop = (note.get('facts') or {}).get('stop')
            if note['kind'] == 'host' and stop:
                return stop
        return None

    def _meter(self, state: dict) -> None:
        left = self.budget_left(state)
        for used, limit, what in ((left['model_calls_used'], left['model_calls_limit'], 'model calls'),
                                  (left['seconds_used'], left['seconds_limit'], 'seconds'),
                                  (left['monetary_used'], left['monetary_limit'], 'monetary budget')):
            if limit is not None and used >= limit:
                self.note(f'Budget reached: {used} of {limit} {what}. Waiting for the operator.', facts=dict(stop='money'))
                raise AtelierStop('money', f'{used} of {limit} {what}')

    def _withheld(self, text: str, files) -> str | None:
        if not self.secrets:
            return None
        blobs = [text.encode('utf-8')] + [self.daybook.read_file(f['sha256']) for f in files if f.get('sha256')]
        for secret in self.secrets:
            needle = secret if isinstance(secret, bytes) else str(secret).encode('utf-8')
            if any(needle in blob for blob in blobs):
                return 'A message was withheld from the record because it contained a credential. Nothing else was lost; say it again without the secret.'
        return None
