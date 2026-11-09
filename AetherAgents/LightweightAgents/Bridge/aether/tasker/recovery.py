"""Record command attempts and bounded, explicitly linked recovery.

Never run a command, change task state, infer intent from prose, or certify output
quality. A recovered execution still needs the task's independent result checks.
"""
import re

POLICY = 'command-recovery-v1'
MAX_RETRIES = 2
COMMANDS = ('run_command', 'run_tests')


class CommandResult(str):
    """Human-readable feedback carrying durable execution facts beside the text."""
    def __new__(cls, text, status, exit_code=None):
        obj = super().__new__(cls, text)
        obj.command_outcome = dict(status=status, exit_code=exit_code)
        return obj


def outcome(name, result, saved=None):
    if name not in COMMANDS:
        return None
    facts = saved if saved is not None else getattr(result, 'command_outcome', None)
    if facts is not None:
        return dict(facts)
    # Compatibility with archived runs. Only the leading tool header is evidence;
    # a traceback or the word 'error' printed by a successful program is not.
    text = str(result)
    match = re.match(r'^\[(?:[^\]\n]+, )?exit code (-?\d+)\]', text)
    if match:
        code = int(match[1])
        return dict(status='succeeded' if code == 0 else 'failed', exit_code=code)
    if text.startswith(('[timed out', 'TimeoutExpired:')):
        return dict(status='timed_out', exit_code=None)
    if text.startswith('[no program named '):
        return dict(status='not_found', exit_code=None)
    # Refusals/invalid arguments remain boundary/tool findings, not retryable exits.
    return dict(status='unknown', exit_code=None)


class Recovery:
    """One task's command history. Callers retain this across that task's wakes."""
    policy, operations, domain = POLICY, COMMANDS, 'command'
    failure_statuses = ('failed', 'timed_out', 'not_found')
    read_outcome = staticmethod(outcome)
    def __init__(self):
        self.attempts, self.pending, self.recovered, self.violations = [], {}, [], []

    def before(self, name, args):
        if name == 'task_close' and self.pending:
            return 'command_unresolved', 'Cannot close with an unresolved command failure.'
        if name not in self.operations:
            return None
        pending = self.pending.get(name)
        if args.get('retry') is True:
            if not pending:
                return 'recovery_link_invalid', 'retry=true requires a failed operation of this tool in this task.'
            if pending['retries'] >= MAX_RETRIES:
                return 'recovery_exhausted', 'Two corrective attempts have already been used.'
        elif pending:
            return 'command_unresolved', 'Correct the failed operation and rerun with retry=true, or task_block.'
        return None

    def observe(self, name, args, result, *, ref=None, saved=None):
        problem = self.before(name, args)
        if problem:
            self.violations.append(dict(kind=problem[0], detail=problem[1], ref=ref))
            return problem
        facts = self.read_outcome(name, result, saved)
        if facts is None:
            return None
        attempt = dict(ref=ref, name=name, arguments=args, **facts)
        pending = self.pending.get(name)
        if pending:
            pending['retries'] += 1
            attempt['recovery_of'] = pending['first']
        self.attempts.append(attempt)
        if facts['status'] == 'succeeded':
            if pending:
                self.recovered.append(dict(first=pending['first'], fixed_by=ref, retries=pending['retries']))
                del self.pending[name]
        elif facts['status'] in self.failure_statuses:
            if pending is None:
                self.pending[name] = dict(first=ref, retries=0)
            elif pending['retries'] >= MAX_RETRIES:
                problem = ('recovery_exhausted', 'The second corrective attempt failed; stop and report the cause.')
        elif facts['status'] == 'refused':
            problem = (self.domain + '_refused', 'The tool refused the operation before execution; stop without expanding access.')
        else:
            problem = (self.domain + '_outcome_unknown', 'Operation outcome is missing or is not a recognized result.')
        if problem:
            self.violations.append(dict(kind=problem[0], detail=problem[1], ref=ref))
        return problem

    def report(self):
        return dict(policy=self.policy, max_corrective_attempts=MAX_RETRIES, attempts=self.attempts,
                    recovered=self.recovered, pending=self.pending, violations=self.violations)


def trackers(turns):
    """Replay only staff command records; Overseer recovery keeps its own policy."""
    tasks = {}
    for turn in sorted(turns, key=lambda t: t.get('turn', 0)):
        if turn.get('mind') in ('overseer', 'librarian'):
            continue
        from .closure_recovery import ClosureRecovery
        closing = turn.get('kind') == 'CLOSURE'
        if closing and not any('closure_outcome' in c for c in (turn.get('cost') or {}).get('tools') or []):
            continue  # Historical closure prose has no structured recovery evidence.
        task = (turn.get('cost') or {}).get('recovery_task', turn.get('task'))
        key = f"{turn.get('mind')}:CLOSURE" if closing else f"{turn.get('mind')}:T{task}"
        tracker = tasks.setdefault(key, ClosureRecovery() if closing else Recovery())
        for index, call in enumerate((turn.get('cost') or {}).get('tools') or []):
            if call.get('dispatched') is False:
                continue  # An envelope failure is not an executed command/closure attempt.
            tracker.observe(call.get('name'), call.get('arguments') or {}, call.get('result', ''),
                            ref=dict(turn=turn.get('turn'), tool_index=index),
                            saved=call.get('closure_outcome' if closing else 'command_outcome'))
    return {key: tracker for key, tracker in tasks.items() if tracker.attempts or tracker.violations}


def replay(turns):
    return {key: tracker.report() for key, tracker in trackers(turns).items()}
