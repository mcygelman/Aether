"""Bound corrections of a malformed tool envelope; never repair text, execute tools or certify their results.

Only hashes and parser facts enter shared evidence. Original arguments stay in
the home transcript. Corrected JSON still passes through every normal tool check.
"""
from copy import deepcopy

POLICY = 'tool-json-recovery-v1'
MAX_CORRECTIONS = 2


class JsonRecovery:
    policy, domain = POLICY, 'json'

    def __init__(self):
        self.attempts, self.pending, self.recovered, self.violations = [], {}, [], []

    def step(self, name, facts):
        if facts['valid'] and not self.pending and not self.violations:
            return None
        correction = self.pending.get('corrections', 0) + 1 if self.pending else 0
        event = dict(name=name, **deepcopy(facts), ref=len(self.attempts), correction=correction)
        problem = None
        if self.violations:
            problem = ('json_recovery_exhausted', 'JSON recovery was already stopped; do not continue.')
        elif not facts['known']:
            problem = ('json_unknown_tool', 'JSON recovery cannot authorize an unknown tool.')
        elif self.pending and name != self.pending['name']:
            problem = ('json_wrong_tool', 'Correct the same pending tool request before any other tool call.')
        elif self.pending and facts['valid']:
            event['status'] = 'corrected'
            self.recovered.append(dict(first=self.pending['first'], fixed_by=event['ref'], corrections=correction))
            self.pending = {}
        else:
            event['status'] = 'malformed'
            if not self.pending:
                self.pending = dict(name=name, first=event['ref'], corrections=0)
            else:
                self.pending['corrections'] = correction
                if correction >= MAX_CORRECTIONS:
                    problem = ('json_recovery_exhausted', 'The second JSON correction is still malformed; stop.')
        if problem:
            event['status'] = 'rejected'
            self.violations.append(dict(kind=problem[0], detail=problem[1], ref=event['ref']))
        self.attempts.append(event)
        return deepcopy(event)

    def report(self):
        return deepcopy(dict(policy=POLICY, max_corrective_attempts=MAX_CORRECTIONS,
            attempts=self.attempts, pending=self.pending, recovered=self.recovered, violations=self.violations))

    @classmethod
    def from_report(cls, report):
        tracker = cls()
        for event in report['attempts']:
            replayed = tracker.step(event['name'], {k:event[k] for k in ('valid','known','sha256','size','error','position')})
            if replayed != event:
                raise ValueError('JSON recovery events do not replay exactly.')
        if tracker.report() != report:
            raise ValueError('JSON recovery counters or policy do not match the events.')
        return tracker


def task_key(agent, kind, task):
    return f'{agent}:CLOSURE:JSON' if kind == 'CLOSURE' else f'{agent}:T{task}:JSON'


def replay(turns):
    trackers = {}
    for turn in sorted(turns, key=lambda t:t.get('turn',0)):
        if turn.get('mind') in ('overseer','librarian'):
            continue
        task = (turn.get('cost') or {}).get('recovery_task', turn.get('task'))
        key = task_key(turn.get('mind'), turn.get('kind'), task)
        for call in (turn.get('cost') or {}).get('tools') or []:
            event = call.get('argument_outcome')
            if event is None:
                continue
            tracker = trackers.setdefault(key, JsonRecovery())
            actual = tracker.step(call.get('name'), {k:event[k] for k in ('valid','known','sha256','size','error','position')})
            if actual != event:
                tracker.violations.append(dict(kind='json_evidence_mismatch',detail='Durable JSON event does not replay.',ref=event.get('ref')))
    return {key:value.report() for key,value in trackers.items()}
