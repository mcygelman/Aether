"""Evaluator-only deterministic behavior checks. Never mount into agent inputs."""
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys

START = '2026-01-01T00:00:00Z'
END = '2026-01-02T00:00:00Z'


def row(ident='b', amount='0.29', currency='USD', at=START):
    return dict(id=ident, amount=amount, currency=currency, at=at)


def grade(project, repository):
    if project not in ('A', 'B'): raise ValueError('Select A or B')
    repository = Path(repository).resolve(strict=True)
    for name in list(sys.modules):
        if name == 'harbor' or name.startswith('harbor.'): del sys.modules[name]
    sys.path.insert(0, str(repository))
    checks = {}
    def check(name, action):
        try:
            action(); checks[name] = 'PASS'
        except Exception as exc:
            checks[name] = 'FAIL:'+type(exc).__name__
    def equal(actual, expected):
        assert type(actual) is type(expected), (type(actual), type(expected))
        if isinstance(expected, dict):
            assert set(actual) == set(expected)
            for key in expected: equal(actual[key], expected[key])
        elif isinstance(expected, list):
            assert len(actual) == len(expected)
            for a, b in zip(actual, expected): equal(a, b)
        else:
            assert actual == expected, (actual, expected)
    def rejects(action):
        try: action()
        except ValueError: return
        raise AssertionError('Expected ValueError')
    try:
        money = importlib.import_module('harbor.money')
        reports = importlib.import_module('harbor.reports')
        name = 'daily_report' if project == 'A' else 'currency_report'
        fn = getattr(reports, name, None)
        check('feature_present', lambda: equal(callable(fn), True))
        check('recurring_decimal_defect', lambda: equal(money.parse_minor('0.29'), 29))
        check('minor_unit_upper_boundary', lambda: equal(money.parse_minor('9999999.99'), 999999999))
        for amount in ('1e2', '-0.01', '+1', 'NaN', ' 1', '1.', '.2', '0.001', '10000000.00'):
            check('amount_reject_'+amount, lambda a=amount: rejects(lambda: money.parse_minor(a)))
        selected = [row('z'), row('a', '0.58'), row('excluded', '1', at=END)]
        expected = dict(count=2, total_minor=87, ids=['a', 'z'])
        if project == 'B': expected = [dict(currency='USD', **expected)]
        check('exact_total_half_open_sorted', lambda: equal(fn(selected, START, END), expected))
        check('empty', lambda: equal(fn([], START, END), dict(count=0,total_minor=0,ids=[]) if project == 'A' else []))
        before = deepcopy(selected)
        check('no_input_mutation', lambda: (fn(selected, START, END), equal(selected, before)))
        check('input_order_independent', lambda: equal(fn(list(reversed(selected)), START, END), expected))
        for label, rows in {
            'duplicate': [row('x'), row('x')], 'outside_invalid': [row(amount='bad', at=END)],
            'unknown_currency': [row(currency='EUR')], 'timestamp': [row(at='2026-02-30T00:00:00Z')],
            'id': [row(ident='../bad')], 'unknown_field': [dict(row(), surprise=True)],
        }.items():
            check('row_reject_'+label, lambda r=rows: rejects(lambda: fn(r, START, END)))
        check('empty_invalid_interval', lambda: rejects(lambda: fn([], END, START)))
        if project == 'B':
            mixed = [row('k', '0.029', 'KWD'), row('j', '29', 'JPY'), row('u', '0.29')]
            wanted = [dict(currency=c, count=1,total_minor=29,ids=[i]) for c,i in [('JPY','j'),('KWD','k'),('USD','u')]]
            check('changed_currency_precision', lambda: equal(fn(mixed,START,END),wanted))
            check('jpy_fraction_rejected', lambda: rejects(lambda: fn([row(amount='1.2',currency='JPY')],START,END)))
            check('kwd_extra_precision_rejected', lambda: rejects(lambda: fn([row(amount='0.0001',currency='KWD')],START,END)))
        else:
            check('a_usd_only', lambda: rejects(lambda: fn([row(currency='JPY')],START,END)))
        for artifact in ('docs/OPERATOR_GUIDE.md', 'IMPLEMENTATION.md', 'REVIEW.md'):
            check('artifact_'+artifact, lambda a=artifact: equal((repository/a).is_file() and (repository/a).stat().st_size > 0,True))
    finally:
        sys.path.remove(str(repository))
    return dict(project=project, layer='OFFLINE_FIXTURE_BEHAVIOR', checks=checks,
                outcome='PASS' if all(x=='PASS' for x in checks.values()) else 'FAIL',
                independent_review='NOT_RUN', memory_benefit='NOT_ASSESSED', mvp_acceptance='NOT_RUN')


if __name__ == '__main__':
    result = grade(*sys.argv[1:]); print(json.dumps(result, sort_keys=True, indent=2))
    sys.exit(0 if result['outcome']=='PASS' else 1)
