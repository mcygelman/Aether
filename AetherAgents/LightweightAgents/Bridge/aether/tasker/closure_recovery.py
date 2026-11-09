"""Track bounded closure-format corrections; never rewrite an agent's words or infer success from prose."""
from .recovery import Recovery


class ClosureResult(str):
    def __new__(cls, text, status):
        obj = super().__new__(cls, text)
        obj.closure_outcome = dict(status=status, exit_code=None)
        return obj


def outcome(name, result, saved=None):
    if name != 'identity_close': return None
    facts = saved if saved is not None else getattr(result, 'closure_outcome', None)
    return dict(facts) if facts is not None else dict(status='unknown', exit_code=None)


class ClosureRecovery(Recovery):
    policy, operations, domain = 'closure-format-recovery-v1', ('identity_close',), 'closure'
    failure_statuses = ('format_error',)
    read_outcome = staticmethod(outcome)

    def observe(self, name, args, result, *, ref=None, saved=None):
        # Authored memory stays in the agent's home transcript, never this ledger.
        evidence = {'retry': args.get('retry') is True} if name == 'identity_close' else args
        return super().observe(name, evidence, result, ref=ref, saved=saved)

    def before(self, name, args):
        problem = super().before(name, args)
        if problem and problem[0] == 'command_unresolved':
            return 'closure_unresolved', 'Correct the pending closure format and call identity_close with retry=true.'
        if problem and problem[0] == 'recovery_link_invalid':
            return 'recovery_link_invalid', 'retry=true requires this agent\'s pending closure-format error.'
        return problem
