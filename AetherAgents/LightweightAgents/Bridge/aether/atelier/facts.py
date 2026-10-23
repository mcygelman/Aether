"""Facts the host derives from what was said. Tolerant, rebuildable, never a refusal.

Every extractor reads one message and returns the facts it can see. `rebuild(daybook)` folds them into the
campaign's derived state; `questions(facts)` names what the host still needs and whom to ask. When an extractor
cannot see something, the answer is a question in the atelier, not a held output.

The patterns are deliberately loose: a lead writes a plan the way a person would (a line per sprint, a line per
task); a reviewer says PASS or FAIL per requirement in prose; an operator says "go" or "accepted" in their own
words. The exact words are kept in the daybook; the facts only point at them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SPRINT_RE = re.compile(r'^\s*(?:#+\s*)?sprint\s+(\d+)\s*[:.\-–—]?\s*(.+?)\s*$', re.I)
TASK_RE = re.compile(r'^\s*(?:[-*•]|\d+[.)])\s*(?:\d+\.\d+\s*[:.)\-]?\s*)?(?:task\s*[:\-]?\s*)?(.+?)\s*(?:\(([^)]+)\))?\s*$', re.I)
ROLE_RE = re.compile(r'([A-Z][\w\-]*(?: [A-Z][\w\-]*)*)\s*(?:=|:|\bas\b|\bis\b)\s*(?:the\s+|an?\s+|our\s+)?([a-z][\w\-]*)')
# A verdict line names its subject, then a separator, then PASS or FAIL: "requirement 3: PASS, because",
# "| 4 | Half-open window | PASS | why |", "- Exact keys - FAIL: missing ids". Prose that merely mentions the words
# ("a FAIL if it isn't") is not a verdict.
VERDICT_RE = re.compile(r'^\s*(?:[-*•]\s*)?(?:\|\s*)?(?:(?:requirement|req\.?|r)?\s*(\d+)\s*[:.\-–|]\s*)?([^|]{0,160}?)\s*[:\-–|]\s*\**\s*(PASS|FAIL)\b\**\s*(.*)$', re.I)
FILE_RE = re.compile(r'`([^`\s]+\.[A-Za-z0-9]{1,8})`')
# The operator's word may open the note or a later sentence ("Budget: 50 calls. Accepted.").
GO_RE = re.compile(r'(?:^|[.!?\n]\s*)(go|approved?|proceed|yes,? go)\b', re.I)
ACCEPT_RE = re.compile(r'(?:^|[.!?\n]\s*)(accepted?|accept(?:ed)? the deliverable|done,? accepted)\b', re.I)
CHANGE_RE = re.compile(r'^\s*(change|revise|not yet|please change)\b', re.I)
BUDGET_RE = re.compile(r'budget\s*[:=]?\s*(\d+)\s*(model calls|calls)', re.I)
DELIVER_RE = re.compile(r'\bdeliverable\b[^`\n]*`([^`\s]+)`', re.I)
LEARNED_RE = re.compile(r'\b(learned|lesson|next time|carry)\b', re.I)
# The operator's ruling, on a line of its own: "Decision: F2 stays.", "Decided (F1): its own ticket.", "Decision on F2: ...".
DECISION_RE = re.compile(r'^\s*(?:[-*•]\s*)?(?:decision|decided)\s*(?:\(([^)]{1,40})\)|\bon\s+([^:]{1,40}))?\s*:\s*(.{4,})$', re.I)


def _lines(text: str):
    return [_clean(line) for line in text.splitlines()]


def _clean(line: str) -> str:
    '''A line as a person meant it: markdown emphasis and heading marks dropped, long dashes made short.'''
    line = re.sub(r'\*{1,3}', '', line)                              # bold and italics
    line = re.sub(r'(?<![\w`/.])_{1,3}|_{1,3}(?![\w`/.])', '', line)  # underscore emphasis, never inside a name
    line = line.replace('—', '-').replace('–', '-')
    return re.sub(r'^\s*#+\s*', '', line).rstrip()


def extract(message: dict, state: dict) -> dict:
    """Facts visible in one message, given the facts so far (for roles and sprint numbering)."""
    kind, text = message['sender']['kind'], message.get('text', '')
    facts: dict = {}
    files = [f['name'] for f in message.get('files', ())]
    if kind == 'operator':
        if not state.get('campaign', {}).get('opened_by'):
            m = BUDGET_RE.search(text)
            request_kind = (message.get('facts') or {}).get('request_kind', 'project')
            facts['opened'] = dict(deliverable=text.strip(), budget_model_calls=int(m.group(1)) if m else None,
                                   request_kind='library' if request_kind == 'library' else 'project')
        else:
            m = BUDGET_RE.search(text)
            if m:
                facts['budget_model_calls'] = int(m.group(1))
        if state.get('campaign', {}).get('opened_by') and ACCEPT_RE.search(text):
            facts['acceptance'] = dict(text=text.strip())
        elif GO_RE.search(text):
            facts['approval'] = dict(sprint=state.get('plan', {}).get('awaiting_approval'), text=text.strip())
        elif CHANGE_RE.match(text):
            facts['change_request'] = dict(text=text.strip())
        decisions = []                                    # the operator's rulings, a line each (decision memory, 10-19)
        for line in _lines(text):
            m = DECISION_RE.match(line)
            if m:
                decisions.append(dict(words=m.group(3).strip(), subject=(m.group(1) or m.group(2) or '').strip() or None))
        if decisions:
            facts['decisions'] = decisions
    if kind == 'lead':
        sprints, current = [], None
        for line in _lines(text):
            m = SPRINT_RE.match(line)
            if m:
                current = dict(number=int(m.group(1)), title=m.group(2).strip(), tasks=[])
                sprints.append(current)
                continue
            t = TASK_RE.match(line)
            if current is not None and t and not line.strip().lower().startswith(('budget', 'roster', 'note')):
                current['tasks'].append(dict(title=t.group(1).strip(), who=(t.group(2) or '').strip() or None))
        if sprints:
            facts['plan'] = dict(sprints=sprints)
        hires, seen = [], set()
        lines = _lines(text)
        roster_lines = [l for l in lines if re.match(r'\s*roster\b', l, re.I)]       # a roster line is read whole
        for sentence in roster_lines + re.split(r'[.\n]', '\n'.join(lines)):
            roster_line = re.match(r'\s*roster\b', sentence, re.I) is not None
            if roster_line or re.search(r'\b(hire|hiring|bring in|bringing in|add|adding)\b', sentence, re.I):
                for name, role in ROLE_RE.findall(sentence):
                    name = name.strip()
                    if name.lower() not in ('roster', 'sprint') and name not in seen:
                        seen.add(name); hires.append((name, role))
        for sprint in sprints:                                   # whoever holds a task is on the roster
            for task in sprint['tasks']:
                who = task.get('who')
                if not who:
                    m = re.match(r'\s*([A-Z][\w\-]*(?: [A-Z][\w\-]*)*)\s*:\s*\S', task['title'])
                    who = m.group(1) if m else None
                if who and who not in seen and who.lower() not in ('sprint', 'roster', 'budget', 'note', 'deliverable', 'deliverables'):
                    seen.add(who); hires.append((who, 'assignee'))
        if hires:
            facts['hires'] = [dict(name=n, role=r) for n, r in hires]
    if kind in ('member', 'lead'):
        named = DELIVER_RE.findall(text)
        block = False                                     # "Deliverables:" then a list of backticked files
        for line in _lines(text):
            if re.match(r'\s*deliverables?\s*[:\-]', line, re.I):
                block = True
                named += [f for f in FILE_RE.findall(line) if f not in named]
                continue
            if block:
                if not line.strip() or not re.match(r'\s*(?:[-*•]|\d+[.)])', line):
                    block = False
                    continue
                named += [f for f in FILE_RE.findall(line) if f not in named]
        if named:
            facts['deliverables'] = named
        elif files:
            facts['files_without_deliverable'] = files
    if kind == 'lead':                                    # the lead recording the operator's words with its tool
        filed = ((message.get('facts') or {}).get('memory') or {}).get('decisions_filed') or []
        if filed:
            facts['decisions'] = [dict(words=d['words'], subject=d.get('subject'), recorded_by_lead=True) for d in filed if d.get('words')]
    if kind == 'reviewer':
        verdicts = []
        for line in _lines(text):
            m = VERDICT_RE.match(line)
            if m and m.group(3) and not re.search(r'\b(if|unless|would|will|whether)\b', line.split(m.group(3), 1)[0], re.I):
                subject = (m.group(2) or '').strip(' -:') or (f'requirement {m.group(1)}' if m.group(1) else '')
                if subject:
                    verdicts.append(dict(subject=subject, verdict=m.group(3).upper(), why=m.group(4).strip(' :,-	')))
        if verdicts:
            facts['verdicts'] = verdicts
    if 'closure' in message.get('facts', {}) or (state.get('acceptance') and kind != 'operator' and LEARNED_RE.search(text)):
        facts['closure'] = dict(participant=message['sender'], text=text.strip())
    return facts


def rebuild(daybook) -> dict:
    """Fold the daybook into the derived state. Pure: the same daybook always gives the same facts."""
    state: dict = dict(campaign=dict(id=daybook.campaign_id, opened_by=None, deliverable=None, budget_model_calls=None),
                       plan=dict(sprints=[], awaiting_approval=None), roster=[], approvals=[], change_requests=[],
                       deliverables=[], verdicts=[], acceptance=None, closures=[], decisions=[], questions_asked=[], withheld=0,
                       failed_turns=0, last_message_id=None, messages=0, library_reply=None, library_participants=[])
    for message in daybook.read():
        state['messages'] += 1
        state['last_message_id'] = message['id']
        if message['kind'] == 'withheld':
            state['withheld'] += 1
        if message['kind'] == 'turn_failed':
            state['failed_turns'] += 1
        if message['kind'] == 'host' and message.get('facts', {}).get('question'):
            state['questions_asked'].append(dict(message_id=message['id'], **message['facts']['question']))
        facts = extract(message, state) if message['kind'] == 'message' else dict(message.get('facts', {}))
        if 'opened' in facts:
            state['campaign'].update(opened_by=message['sender'], **facts['opened'])
        if state['campaign'].get('request_kind') == 'library' and message['kind'] == 'message':
            if message['sender']['kind'] == 'operator' and not state['acceptance'] and 'acceptance' not in facts:
                state['library_reply'] = None
            if message['sender']['kind'] == 'librarian':
                state['library_reply'] = message['id']
                if not any(p['id'] == message['sender']['id'] for p in state['library_participants']):
                    state['library_participants'].append(message['sender'])
        if 'budget_model_calls' in facts:
            state['campaign']['budget_model_calls'] = facts['budget_model_calls']
        if 'plan' in facts:
            state['plan'] = dict(sprints=facts['plan']['sprints'], awaiting_approval=facts['plan']['sprints'][0]['number'],
                                 message_id=message['id'])
        if 'hires' in facts:
            for hire in facts['hires']:
                state['roster'].append(dict(hire, requested_in=message['id']))
        if 'hired' in facts:
            state['roster'].append(dict(facts['hired'], hired_in=message['id']))
        if 'approval' in facts:
            state['approvals'].append(dict(facts['approval'], message_id=message['id']))
            done = facts['approval'].get('sprint')
            numbers = [s['number'] for s in state['plan']['sprints'] if s['number'] > (done or 0)]
            state['plan']['awaiting_approval'] = numbers[0] if numbers else None
        if 'change_request' in facts:
            state['change_requests'].append(dict(facts['change_request'], message_id=message['id']))
        if 'deliverables' in facts:
            by_name = {f['name']: f for f in message.get('files', ())}
            for name in facts['deliverables']:
                sha = by_name.get(name, {}).get('sha256')
                if any(d['name'] == name and d.get('sha256') == sha for d in state['deliverables']):
                    continue                                   # the same file, mentioned again
                state['deliverables'].append(dict(name=name, sha256=sha,
                                                  by=message['sender'], message_id=message['id']))
        if 'decisions' in facts:
            for i, d in enumerate(facts['decisions']):
                state['decisions'].append(dict(d, message_id=message['id'], index=i, by=message['sender'], at=message.get('at')))
        if 'verdicts' in facts:
            for v in facts['verdicts']:
                state['verdicts'].append(dict(v, by=message['sender'], message_id=message['id']))
        if 'acceptance' in facts:
            state['acceptance'] = dict(facts['acceptance'], message_id=message['id'], by=message['sender'])
        if 'closure' in facts:
            # One closure per mind: later words supersede earlier ones; the same words again change nothing.
            pid = facts['closure']['participant']['id']
            earlier = next((c for c in state['closures'] if c['participant']['id'] == pid), None)
            if earlier is None:
                state['closures'].append(dict(facts['closure'], message_id=message['id'], filed=False))
            elif earlier['text'] != facts['closure']['text']:
                earlier.update(text=facts['closure']['text'], message_id=message['id'], filed=False)
        if message['kind'] == 'host' and facts.get('filed'):
            pid = facts['filed']['participant']['id']
            for closure in reversed(state['closures']):
                if closure['participant']['id'] == pid:
                    closure.update(filed=True, filed_where=facts['filed'].get('where'))
                    break
    return state


def _same(a: str, b: str) -> bool:
    '''Guide is Harbor Guide; harbor-guide is Harbor Guide; case does not matter.'''
    a, b = str(a or '').lower(), str(b or '').lower()
    if not a or not b:
        return False
    if a == b:
        return True
    wa = {w for w in re.split(r'[\s\-_]+', a) if len(w) >= 3}
    wb = {w for w in re.split(r'[\s\-_]+', b) if len(w) >= 3}
    return bool(wa & wb)


def questions(state: dict) -> list:
    """What the host still needs, and whom to ask. Each is {to, text, about}."""
    asked = {(q['to'], q['about']) for q in state['questions_asked']}
    out = []
    if (state['campaign']['opened_by'] and not state['plan']['sprints'] and not state['acceptance']
            and state['campaign'].get('request_kind') != 'library'):
        out.append(dict(to='lead', about='plan', text='What is the plan? One line per sprint, one per task.'))
    if state['campaign']['opened_by'] and not state['acceptance']:
        letter = ' '.join((state['campaign'].get('deliverable') or '').split())[:300]
        out.append(dict(to='librarian', about='shelf', text=f'A commission opened: {letter}. Anything on the shelf for it? '
                        'Lend what fits (it lands in library/ under the commission) and say what you lent and what you left.'))
    if state['plan']['sprints'] and state['plan']['awaiting_approval'] is not None and not state['acceptance']:
        n = state['plan']['awaiting_approval']
        out.append(dict(to='operator', about=f'approve sprint {n}', text=f'Sprint {n} is planned. Say "go" to approve it, or what to change.'))
    for approval in state['approvals']:
        n = approval.get('sprint')
        sprint = next((sp for sp in state['plan']['sprints'] if sp['number'] == n), None)
        if sprint is None:
            continue
        tasks = '; '.join(t['title'] + (f" ({t['who']})" if t.get('who') else '') for t in sprint['tasks']) or sprint['title']
        for pid in ('lead', 'member', 'reviewer'):
            out.append(dict(to=pid, about=f'start sprint {n}', text=f'Sprint {n} is approved: {tasks}. Whoever holds a task: go ahead, and name the deliverable when it is in.'))
    if state['deliverables'] and not state['verdicts'] and not state['acceptance']:
        critics = [m for m in state['roster'] if str(m.get('role', '')).lower() in ('critic', 'reviewer', 'review')]
        for critic in critics:
            pid = critic.get('id') or critic.get('name')
            files = ', '.join(d.get('name') or d.get('path') if isinstance(d, dict) else str(d) for d in state['deliverables'][-6:])
            out.append(dict(to=pid, about='verdicts', text=f'Deliverables are in ({files}). Your own verdict, please: PASS or FAIL per requirement, with why.'))
    if state['acceptance']:
        closed = [(c['participant'].get('id'), c['participant'].get('name')) for c in state['closures']]
        for member in state['roster'] + state.get('library_participants', []):
            pid = member.get('id') or member.get('name')
            if pid and not any(_same(pid, cid) or _same(pid, cname) for cid, cname in closed):
                out.append(dict(to=pid, about='closure', text='The operator accepted. In your own words: what did you do, what did you learn, what should your future self carry, and what should your next self skip?'))
    return [q for q in out if (q['to'], q['about']) not in asked]


def write(daybook, state: dict) -> Path:
    """facts.json is derived from the daybook, so a write that loses a race with another place is retried and,
    failing that, skipped: the next rebuild writes it again. Nothing in the record depends on it."""
    import os
    import time
    path = daybook.root / 'facts.json'
    tmp = path.with_suffix(f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding='utf-8')
    for attempt in range(10):
        try:
            tmp.replace(path)
            return path
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
    try:
        tmp.unlink()
    except OSError:
        pass
    return path
