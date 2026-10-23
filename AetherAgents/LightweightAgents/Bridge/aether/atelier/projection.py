"""Read-only views of a commission: what the atelier page, the campaign state page and the receipt page show.

Every view is computed from the daybook and the facts rebuilt from it (`facts.rebuild`); nothing here writes, and
nothing reads the per-step record families the campaign pages used before the atelier. The campaign state and the
receipt keep the field names their pages already render (stage, condition, reason, actions, obligations, signals,
sources; receipts with phase, path, sha256 and what each note captures), so a commission and an earlier campaign
show on the same pages.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urlencode

from . import facts as facts_module
from .atelier import Atelier

COMMISSION_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
PAGE = '/projects/atelier'
FILE_API = '/api/atelier/file'
STAGES = ['Plan', 'Sprint', 'Review', 'Close', 'Closed']
ROLE_ADDRESSES = ('lead', 'member', 'reviewer')     # the atelier writes to a role when it does not know the name


def exists(root, commission) -> bool:
    return (isinstance(commission, str) and bool(COMMISSION_RE.match(commission))
            and (Path(root) / 'daybooks' / commission / 'daybook.jsonl').is_file())


def commissions(root) -> list:
    """Every commission under this atelier, the most recently written first."""
    rooms = Path(root) / 'daybooks'
    out = []
    for path in rooms.glob('*/daybook.jsonl') if rooms.is_dir() else ():
        if not exists(root, path.parent.name):
            continue
        rows = Atelier(root, path.parent.name).daybook.read()
        if not rows:
            continue
        state = _rebuild(root, path.parent.name)
        out.append(dict(id=path.parent.name, letter=rows[0]['text'][:240], opened_at=rows[0]['at'], last_at=rows[-1]['at'],
                        notes=len(rows), accepted=bool(state['acceptance'])))
    return sorted(out, key=lambda c: (c['last_at'], c['id']), reverse=True)


def latest(root) -> str | None:
    found = commissions(root)
    return found[0]['id'] if found else None


def people(rows, state) -> list:
    """Whom the operator can wake: everyone who wrote or was written to, and everyone the master hired."""
    seen = {}
    for row in rows:
        for who in (row['sender'], row.get('to')):
            if who and who['kind'] not in ('host', 'operator') and who['id'] not in ROLE_ADDRESSES:
                seen.setdefault(who['id'], dict(who))
    for member in state['roster']:
        pid = member.get('id') or member.get('name')
        if pid and pid not in seen:
            kind = 'reviewer' if (member.get('role') or '').lower() in ('reviewer', 'critic') else 'member'
            seen[pid] = dict(id=pid, kind=kind, name=member.get('name') or pid)
    if state['campaign'].get('request_kind') == 'library':
        seen.pop('lead', None)
        if any(p['kind'] == 'librarian' and p['id'] != 'librarian' for p in seen.values()):
            seen.pop('librarian', None)
    elif not any(p['kind'] == 'lead' for p in seen.values()):
        seen['lead'] = dict(id='lead', kind='lead', name='The master')
    return list(seen.values())


def thread(root, commission, since=None, working=()) -> dict:
    """The atelier page's view: notes (all, or after `since`), facts, budget, the three numbers, the chain."""
    atelier = Atelier(root, commission)
    rows = atelier.daybook.read()
    notes, since_known = rows, True
    if since:
        index = next((i for i, r in enumerate(rows) if r['id'] == since), None)
        notes, since_known = (rows[index + 1:], True) if index is not None else (rows, False)
    state = facts_module.rebuild(atelier.daybook)
    status = conversation_status(state, rows, working)
    return dict(commission_id=commission, notes=notes, since_known=since_known,
                last_id=rows[-1]['id'] if rows else None, facts=state, budget=atelier.budget_left(state),
                numbers=atelier.numbers(), chain=atelier.daybook.verify(), people=people(rows, state),
                working=sorted(working), hint=status['detail'], status=status)


def hint(state) -> str:
    """What the operator's next note could be, in words. A suggestion, never a requirement."""
    if not state['campaign']['opened_by']:
        return 'Write the commission letter: what to deliver, why, what helps, and the budget (for example "Budget: 20 model calls").'
    if state['acceptance']:
        return _stage(state)[2]
    if state['campaign'].get('request_kind') == 'library':
        return _stage(state)[2]
    n = state['plan']['awaiting_approval']
    if state['plan']['sprints'] and n is not None:
        return f'Sprint {n} is planned. Reply "go" to approve it, or say what to change.'
    if state['plan']['sprints']:
        return 'Every sprint is approved. Reply "accepted" when the deliverable is what you asked for, or say what to change.'
    return 'The master has been asked for a plan. Write to anyone in the atelier at any time.'


def _rebuild(root, commission) -> dict:
    return facts_module.rebuild(Atelier(root, commission).daybook)


def _stage(state) -> tuple:
    plan, closures = state['plan'], state['closures']
    if state['acceptance']:
        filed = bool(closures) and all(c.get('filed') for c in closures)
        open_closures = [q for q in _open_questions(state) if q['about'] == 'closure']
        return ('Closed', 'DONE', 'Accepted; every closing note is filed.') if filed and not open_closures else \
               ('Close', 'WAITING', 'Accepted; closing notes are being written and filed.')
    if state['campaign'].get('request_kind') == 'library':
        return ('Review', 'NEEDS_YOU', 'The librarian replied. Review the reply and any files, then accept or ask for a change.') if state.get('library_reply') else \
               ('Plan', 'WAITING', 'Waiting for the librarian to reply. You can add a note at any time.')
    if not plan['sprints']:
        return 'Plan', 'WAITING', 'Waiting for the master to reply with a plan.'
    if plan['awaiting_approval'] is not None:
        return 'Sprint', 'NEEDS_YOU', f'Sprint {plan["awaiting_approval"]} is planned and waits for your "go".'
    if state['verdicts']:
        return 'Review', 'RUNNING', 'Every sprint is approved; the critic has written. Reply "accepted" when it is right.'
    return 'Sprint', 'RUNNING', 'Every sprint is approved; the assistants are working.'


def _sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def conversation_status(state, rows, working=()) -> dict:
    """Progress from recorded notes; availability is separate and never proves a turn is running."""
    stage, condition, detail = _stage(state)
    label = ('Finished' if stage == 'Closed' else 'Closing' if stage == 'Close' else
             'Waiting for you' if condition == 'NEEDS_YOU' else 'Waiting for reply')
    reason = None
    for row in reversed(rows):
        if row['sender']['kind'] == 'operator':
            break
        if row['kind'] == 'host' and (row.get('facts') or {}).get('stop'):
            reason = row['facts']['stop']
            label, condition, detail = 'Waiting for you', 'NEEDS_YOU', row['text']
            break
    if not reason:
        if working:
            label, condition, detail = 'Working', 'RUNNING', 'Taking a turn: ' + ', '.join(sorted(working)) + '.'
        elif rows and rows[-1]['kind'] == 'turn_failed':
            label, detail = 'Turn failed', rows[-1]['text']
        elif rows and (rows[-1].get('facts') or {}).get('filing_failed'):
            label, detail = 'Filing failed', rows[-1]['text']
        elif not state['acceptance']:
            detail = hint(state)
    return dict(label=label, stage=stage, condition=condition, detail=detail,
                can_approve=not reason and not state['acceptance'] and state['plan']['awaiting_approval'] is not None,
                can_accept=not reason and not state['acceptance'] and
                    (state['campaign'].get('request_kind') != 'library' or bool(state.get('library_reply'))))


def _file_href(commission, sha):
    return FILE_API + '?' + urlencode(dict(commission=commission, sha256=sha))


def campaign_state(root, commission) -> dict:
    """GET /api/campaign-state for a commission: the daybook's facts in the campaign page's own fields."""
    atelier = Atelier(root, commission)
    state = facts_module.rebuild(atelier.daybook)
    status = conversation_status(state, atelier.daybook.read())
    stage, condition, reason = status['stage'], status['condition'], status['detail']
    link = PAGE + '?' + urlencode(dict(commission=commission))
    signals = [dict(family='commission', literal=state['campaign']['deliverable'], condition=condition, stage='Plan', source='daybook.jsonl')]
    signals += [dict(family='sprint', literal=f"{s['number']}: {s['title']}", condition='DONE' if any(a.get('sprint') == s['number'] for a in state['approvals']) else 'WAITING',
                     stage='Sprint', source='daybook.jsonl') for s in state['plan']['sprints']]
    signals += [dict(family='deliverable', literal=f"{d['name']} {d.get('sha256') or ''}".strip(), condition='DONE', stage='Sprint',
                     source='daybook.jsonl') for d in state['deliverables']]
    signals += [dict(family='verdict', literal=f"{v['subject']}: {v['verdict']}", condition='DONE' if v['verdict'] == 'PASS' else 'NEEDS_YOU',
                     stage='Review', source='daybook.jsonl') for v in state['verdicts']]
    signals += [dict(family='closure', literal=c['participant'].get('name') or c['participant']['id'], condition='DONE' if c.get('filed') else 'WAITING',
                     stage='Close', source='daybook.jsonl') for c in state['closures']]
    obligations = [dict(kind='atelier_question', condition='NEEDS_YOU' if q['to'] == 'operator' else 'WAITING', literal=q['about'],
                        source='daybook.jsonl', note=f"to {q['to']}: {q['text']}") for q in _open_questions(state)]
    root_dir = atelier.daybook.root
    return dict(source='atelier', campaign_id=commission, stage=stage, stages=STAGES, condition=condition, reason=reason,
                actions=[dict(label='Open the atelier', href=link)], obligations=obligations, signals=signals,
                sources=[dict(path='daybook.jsonl', sha256=_sha(atelier.daybook.path)),
                         dict(path='facts.json', sha256=_sha(root_dir / 'facts.json'))],
                facts=state, budget=atelier.budget_left(state), numbers=atelier.numbers(), chain=atelier.daybook.verify())


def _open_questions(state) -> list:
    """The atelier's questions not yet answered by what was said since."""
    approved = {str(a.get('sprint')) for a in state['approvals']}
    closed = {c['participant']['id'] for c in state['closures']} | {c['participant'].get('name') for c in state['closures']}
    out = []
    for q in state['questions_asked']:
        about = q['about']
        if about == 'plan' and (state['plan']['sprints'] or state['acceptance']):
            continue
        if about == 'shelf' and (state.get('library_reply') or state['acceptance']):
            continue
        if about.startswith('approve sprint') and (state['acceptance'] or about.rsplit(' ', 1)[-1] in approved):
            continue
        if about == 'closure' and q['to'] in closed:
            continue
        out.append(q)
    return out


def campaign_receipts(root, commission) -> dict:
    """GET /api/campaign-receipts for a commission: every note in the daybook with its hash and what it captures."""
    atelier = Atelier(root, commission)
    rows = atelier.daybook.read()
    state = facts_module.rebuild(atelier.daybook)
    status = conversation_status(state, rows)
    stage, condition, reason = status['stage'], status['condition'], status['detail']
    chain = atelier.daybook.verify()
    receipts = []
    for row in rows:
        who = row['sender'].get('name') or row['sender']['id']
        captured = ([dict(field='previous note', sha256=row['previous_sha256'])] if row.get('previous_sha256') else [])
        captured += [dict(field=f['name'], sha256=f['sha256'], href=_file_href(commission, f['sha256']), bytes=f.get('bytes'))
                     for f in row.get('files', ())]
        receipts.append(dict(phase=f"Note {row['n']} · {who} ({row['sender']['kind']}) · {row['kind']} · {row['at']}",
                             path=f"daybook.jsonl#{row['n']}", href=PAGE + '?' + urlencode(dict(commission=commission)) + '#note-' + row['id'],
                             sha256=row['sha256'], captured=captured, cost=row.get('cost') or {}))
    files = {}
    for row in rows:
        for f in row.get('files', ()):
            files.setdefault(f['sha256'], dict(sha256=f['sha256'], names=[], bytes=f.get('bytes'), href=_file_href(commission, f['sha256'])))
            if f['name'] not in files[f['sha256']]['names']:
                files[f['sha256']]['names'].append(f['name'])
    return dict(source='atelier', campaign_id=commission, stage=stage, condition=condition,
                reason=reason + (' The daybook chain verifies.' if chain['ok'] else f" The daybook chain does not verify: {chain['first_problem']}."),
                problems=[] if chain['ok'] else [dict(source='daybook.jsonl', error=chain['first_problem'])],
                planning_memory=[], artifact_candidates=[], receipts=receipts, files=list(files.values()),
                verification=chain, facts_sha256=_sha(atelier.daybook.root / 'facts.json'), numbers=atelier.numbers())
