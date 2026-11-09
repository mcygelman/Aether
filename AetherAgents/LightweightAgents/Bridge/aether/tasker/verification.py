"""Own native Tasker output verification and operator acceptance.

Never execute outputs, invent expected answers, repair work or file reflections.
Contracts and bound reviews are trusted operator inputs, not a hostile-code sandbox.
An operator's acceptance over a failed check is its own record (accept_anyway); it never
changes a check's result or claims the checks passed.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from . import output_checks as checks
from ._files import read_json, write_json


def validate(contracts):
    if not isinstance(contracts, dict) or not contracts:
        raise ValueError('Declare output contracts by task number, or solo for a solo sprint')
    if 'solo' in contracts and set(contracts) != {'solo'}:
        raise ValueError('A solo contract cannot be mixed with staff task contracts')
    for key, contract in contracts.items():
        if key != 'solo' and (not isinstance(key, str) or not key.isdigit() or int(key) < 1 or str(int(key)) != key):
            raise ValueError('Contract keys must be canonical positive task numbers')
        checks.validate(contract)
    return checks.parse(json.dumps(contracts))


def folder(sprint):
    return sprint.data_root/'verification'/sprint.id


def configure(sprint, contracts):
    contracts = validate(contracts)
    root = folder(sprint)
    root.mkdir(parents=True, exist_ok=False)
    checks.write_receipt(root/'contracts.json', sprint.root/'outputs', contracts)
    sprint.update(lambda d: d.update(verification_sha256=checks.digest(contracts)))


def contracts(sprint):
    expected = sprint.record().get('verification_sha256')
    if expected is None:
        return None  # Existing sprints are not migrated or retrospectively accepted.
    value = validate(checks.load(folder(sprint)/'contracts.json'))
    if checks.digest(value) != expected:
        raise ValueError('Operator verification contract changed; preserve this sprint')
    return value


def _root(sprint, key):
    return sprint.root/'outputs' if key == 'solo' else sprint.outputs(int(key))


def _receipt(sprint, key, result):
    return folder(sprint)/f'{key}-{checks.digest(result)[:24]}.json'


def _extras(root, declared):
    """Undeclared files under an output folder, by name only; they are never opened."""
    found = set()
    for base, _dirs, files in os.walk(root, followlinks=False):
        found.update((Path(base)/name).relative_to(root).as_posix() for name in files)
    return sorted(found - set(declared))


def _check(root, contract):
    """The checker's result, except that an undeclared extra file no longer fails it (Michel, 2026-11-07: "creating
    the file is natural outcome from most AI"). The extra is named in the result and never opened. A missing, empty or
    wrong declared file, a key file or any other finding still fails. output_checks.py itself is unchanged: its own
    hash is in every retained receipt, so results without an extra stay byte-for-byte what they were."""
    result = checks.check(root, contract)
    root = Path(root).absolute()
    try:
        extra = _extras(root, contract['files']) if root.is_dir() else []
        for name in extra:
            checks._safe_name(name)
    except (OSError, ValueError):
        return result
    finding = 'Output file set differs: missing=[], extra=' + repr(extra)
    if not extra or finding not in result['findings']:
        return result
    findings = [f for f in result['findings'] if f != finding]
    return dict(result, findings=findings, extra_files=extra, status='failed' if findings else 'checks_passed')


def _accept(root, contract, receipt, review=None):
    """output_checks.accept over _check: the same bound-review rules."""
    current = _check(root, contract)
    if current['status'] != 'checks_passed':
        return dict(status='failed', findings=current['findings'])
    if not isinstance(receipt, dict) or checks.digest(current) != checks.digest(receipt):
        return dict(status='unverified_or_stale')
    if current['review_required']:
        if not isinstance(review, dict) or review.get('verdict') not in ('PASS', 'FAIL'):
            return dict(status='review_required')
        if review.get('receipt_sha256') != checks.digest(receipt) or not review.get('reviewer'):
            return dict(status='unbound_or_stale_review')
        if review['verdict'] != 'PASS':
            return dict(status='review_failed')
    return dict(status='accepted', receipt_sha256=checks.digest(receipt),
                limitation='Only declared checks and the explicit bound review; no universal quality claim')


def _halt(sprint, exc):
    from .terminal import stop
    stop(sprint, 'output_verification_failed', str(exc))
    raise ValueError('Output verification failed; sprint stopped: ' + str(exc)) from exc


def gate(sprint, key):
    """Check and retain evidence before the task's Closed write releases dependents."""
    try:
        declared = contracts(sprint)
        if declared is None:
            return
        if key not in declared:
            raise ValueError(f'No operator output contract for task {key}')
        result = _check(_root(sprint, key), declared[key])
        path = _receipt(sprint, key, result)
        if path.exists():
            if checks.load(path) != result:
                raise ValueError('Existing verification receipt changed')
        else:
            checks.write_receipt(path, _root(sprint, key), result)
        if result['status'] != 'checks_passed':
            raise ValueError('; '.join(result['findings']))
    except (OSError, ValueError) as exc:
        _halt(sprint, exc)


def before_close(tasker_path, number):
    from .sprint import load_sprint
    if (Path(tasker_path).parent/'sprint.json').exists():
        gate(load_sprint(Path(tasker_path).parent), str(number))


def before_start(tasker_path, dependencies):
    from .sprint import load_sprint
    root = Path(tasker_path).parent
    if not (root/'sprint.json').exists():
        return
    sprint = load_sprint(root)
    try:
        declared = contracts(sprint)
        if declared is None:
            return
        from .terminal import failure
        if failure(sprint):
            raise ValueError(str(failure(sprint)))
        for number in dependencies:
            key = str(number)
            current = _check(_root(sprint, key), declared[key])
            receipt = _receipt(sprint, key, current)
            if current['status'] != 'checks_passed' or not receipt.is_file() or checks.load(receipt) != current:
                raise ValueError(f'Dependency T{number} no longer has its verified output')
    except (OSError, ValueError, KeyError) as exc:
        _halt(sprint, exc)


def task_declared(tasker_path, number):
    from .sprint import load_sprint
    root = Path(tasker_path).parent
    if (root/'sprint.json').exists():
        declared = contracts(load_sprint(root))
        if declared is not None and str(number) not in declared:
            raise ValueError(f'Task {number} is outside the operator output contract; keep the declared task plan')


def summary(sprint):
    declared = contracts(sprint)
    return {key: list(value['files']) for key, value in (declared or {}).items()}


def before_drop(tasker_path):
    from .sprint import load_sprint
    root = Path(tasker_path).parent
    if (root/'sprint.json').exists() and contracts(load_sprint(root)) is not None:
        raise ValueError('A declared output task cannot be dropped to release dependents; finish the sprint as fail instead')


def evaluate(sprint, reviews=None, *, require_complete=True):
    """Recheck current bytes; review decisions cannot override failed or missing checks."""
    try:
        declared = contracts(sprint)
        if declared is None:
            return dict(status='legacy_unverified', tasks={})
        if reviews is None and require_complete:
            saved = read_json(folder(sprint)/'review-current.json', {})
            reviews = saved.get('reviews', {})
        tasks = sprint.tasker('operator').list()
        keys = {'solo'} if not tasks else {str(t.number) for t in tasks}
        if keys != set(declared) or any(t.status != 'Closed' or t.note.lower().startswith('dropped:') for t in tasks):
            return dict(status='incomplete', tasks={}, detail='Every declared task must close through its verification gate')
        expected = {('' if key == 'solo' else f'T{key}/') + name for key,c in declared.items() for name in c['files']}
        output_root = sprint.root/'outputs'
        actual = set()
        checks._plain(output_root)
        for base, dirs, files in os.walk(output_root, followlinks=False):
            for name in dirs + files:
                path = Path(base)/name
                checks._plain(path)
                relative = checks._safe_name(path.relative_to(output_root).as_posix())
                if name in files:
                    actual.add(relative)
        if expected - actual:
            raise ValueError('Sprint output file set differs from the declared deliverables: missing '
                             + ', '.join(sorted(expected - actual)))
        extra = sorted(actual - expected)    # named for the operator, never opened (Michel, 2026-11-07)
        if reviews is not None and (not isinstance(reviews, dict) or set(reviews)-keys):
            raise ValueError('Reviews must be a map of declared task numbers to bound decisions')
        outcomes = {}
        for key, contract in declared.items():
            current = _check(_root(sprint, key), contract)
            receipt = _receipt(sprint, key, current)
            if current['status'] != 'checks_passed':
                decision = dict(status='failed', findings=current['findings'])
            elif not receipt.is_file():
                decision = dict(status='unverified_or_stale')
            else:
                decision = _accept(_root(sprint, key), contract, checks.load(receipt), (reviews or {}).get(key))
            outcomes[key] = dict(decision, receipt=str(receipt), receipt_sha256=checks.digest(current))
        statuses = {d['status'] for d in outcomes.values()}
        state = 'accepted' if statuses == {'accepted'} else 'review_required' if statuses <= {'accepted', 'review_required'} else 'failed'
        if require_complete:
            from .completion import staff_complete
            from .terminal import failure
            timer = read_json(sprint.root/'timer/timer.json', {})
            if failure(sprint):
                state = 'failed'
            elif (sprint.record().get('state') != 'closed-pass' or not staff_complete(sprint)
                  or not _overseer_complete(sprint) or timer.get('running') or timer.get('pending')):
                state = 'incomplete'
        return dict(status=state, tasks=outcomes, **({'extra_files': extra} if extra else {}))
    except (OSError, ValueError) as exc:
        return dict(status='failed', tasks={}, detail=str(exc))


def finish(sprint):
    """Allow normal process closure after mechanical checks; prose acceptance remains separate."""
    try:
        if contracts(sprint) is None:
            return
        if not sprint.tasker('operator').list():
            gate(sprint, 'solo')
    except (OSError, ValueError) as exc:
        _halt(sprint, exc)
    result = evaluate(sprint, require_complete=False)
    if result['status'] not in ('accepted', 'review_required'):
        _halt(sprint, ValueError(str(result)))


def acceptance(sprint, reviews=None):
    """Retain each operator decision without overwriting a prior check or execution record."""
    result = evaluate(sprint, reviews)
    record = dict(result=result, reviews=reviews or {}, contracts_sha256=sprint.record().get('verification_sha256'))
    root = folder(sprint)
    if not root.exists():
        return result
    path = root/f'acceptance-{checks.digest(record)[:24]}.json'
    if not path.exists():
        checks.write_receipt(path, sprint.root/'outputs', record)
    elif checks.load(path) != record:
        raise ValueError('Existing acceptance receipt changed')
    if reviews is not None:
        write_json(root/'review-current.json', dict(reviews=reviews, acceptance_receipt=str(path)))
    return dict(result, acceptance_receipt=str(path))


def _overseer_complete(sprint):
    import hashlib
    key = hashlib.sha256(sprint.id.encode('utf-8')).hexdigest()[:24]
    receipt = read_json(sprint.data_root/'minds'/sprint.overseer/'logs/tasker_closure'/f'{key}.json', {})
    return (isinstance(receipt, dict) and receipt.get('status') == 'CLOSED'
            and receipt.get('source_ref') == sprint.id and receipt.get('closure_id') == key
            and bool(receipt.get('note_id')) and bool(receipt.get('text_sha256')))


OVERRIDE = 'operator-accepted.json'


def _outputs_now(sprint):
    """The declared outputs' current bytes, as SHA-256 (None for a file not written)."""
    hashes = {}
    for key, contract in (contracts(sprint) or {}).items():
        for name in contract['files']:
            try:
                hashes[key + '/' + name] = hashlib.sha256(checks._bytes(_root(sprint, key)/name)).hexdigest()
            except (OSError, ValueError):
                hashes[key + '/' + name] = None
    return hashes


def accept_anyway(sprint, result, reason, operator):
    """Michel, 2026-11-07: "I can imagine scenarios where an operator wants to proceed over an error. We need a way
    forward." One record per sprint, written once: who, when, why, the failure and check result they accepted over,
    and the output bytes they saw. Checks and receipts are left exactly as they are."""
    from .terminal import failure
    root = folder(sprint)
    if not root.is_dir():
        raise ValueError('This sprint has no output checks to accept over')
    record = dict(kind='accepted_over_failed_check',
                  by=dict(id=str(operator.get('id') or ''), name=str(operator.get('name') or operator.get('username') or '')),
                  at=datetime.now(timezone.utc).isoformat(timespec='seconds'), reason=reason,
                  failure=str(failure(sprint) or ''), verification=result, outputs=_outputs_now(sprint))
    try:
        checks.write_receipt(root/OVERRIDE, sprint.root/'outputs', record)
    except FileExistsError:
        raise ValueError('This sprint was already accepted anyway') from None
    return operator_accepted(sprint)


def operator_accepted(sprint):
    """The operator's acceptance over a failed check, and whether the outputs are still the bytes they accepted."""
    path = folder(sprint)/OVERRIDE
    if not path.is_file():
        return None
    record = checks.load(path)
    return dict(by=record.get('by'), at=record.get('at'), reason=record.get('reason'), failure=record.get('failure'),
                current=record.get('outputs') == _outputs_now(sprint))


def extra_outputs(sprint):
    """Files in the sprint's outputs that no contract declared, by name; never opened."""
    declared = contracts(sprint)
    root = sprint.root/'outputs'
    if not declared or not root.is_dir():
        return []
    expected = {('' if key == 'solo' else f'T{key}/') + name for key, c in declared.items() for name in c['files']}
    try:
        return _extras(root, expected)
    except OSError:
        return []
