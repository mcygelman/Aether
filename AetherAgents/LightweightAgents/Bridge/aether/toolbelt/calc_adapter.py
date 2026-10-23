"""Host boundary for effect-free calculations; all agent calls use a worker.

Windows contract: bounded payload/arithmetic plus a hard five-second outer
timeout, NOT an OS memory cap. calc's cooperative budget remains two seconds.
"""
import json
import math
from aether.toolbelt import calc

HINT = 'Compute numbers with calculate and quote its value; use round for currency.'
OUTER_SECONDS = 5.0


def refusal(message):
    return dict(ok=False, tool='calculate', version=calc.VERSION, error=message)


def arguments(value):
    """Validate the public schema before creating a process; never evaluate here."""
    try:
        raw = value if isinstance(value, str) else json.dumps(value, allow_nan=False)
        if len(raw) > calc.MAX_ARGUMENT_CHARS:
            raise ValueError('calculate arguments exceed 20000 characters')
        args = json.loads(raw)
        if not isinstance(args, dict) or set(args)-{'expression', 'variables', 'digits'}:
            raise ValueError('calculate expects expression, variables?, digits?')
        expression = args.get('expression')
        if not isinstance(expression, str) or not 1 <= len(expression) <= calc.MAX_EXPRESSION_CHARS:
            raise ValueError('calculate expression must contain 1..4000 characters')
        if 'digits' in args and (type(args['digits']) is not int or not 1 <= args['digits'] <= calc.MAX_DIGITS):
            raise ValueError('calculate digits must be an integer from 1 to 100')
        if 'variables' in args:
            variables = args['variables']
            if not isinstance(variables, dict) or len(variables) > calc.MAX_VARIABLES:
                raise ValueError('calculate variables must be an object with at most 64 bindings')
            for name, item in variables.items():
                if not calc.NAME_RE.fullmatch(name) or type(item) not in (str, int, float):
                    raise ValueError('calculate variables require named numeric values or expressions')
                if isinstance(item, str) and not 1 <= len(item) <= calc.MAX_EXPRESSION_CHARS:
                    raise ValueError('calculate variable expression must contain 1..4000 characters')
                if isinstance(item, float) and not math.isfinite(item):
                    raise ValueError('calculate variables must be finite')
        return args
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        # Do not echo potentially private/unbounded argument values.
        if type(exc) is ValueError and str(exc).startswith('calculate '):
            raise
        raise ValueError('calculate arguments must be bounded valid JSON') from None


def dispatch(value):
    try:
        args = arguments(value)
        return calc.evaluate_isolated(args['expression'], args.get('variables'),
                                      args.get('digits'), timeout=OUTER_SECONDS)
    except ValueError as exc:
        return refusal(str(exc))
    except Exception:
        return refusal('calculate worker could not complete; no effects')
