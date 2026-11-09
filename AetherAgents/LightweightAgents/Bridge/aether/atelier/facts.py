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

# "Sprint 1 is approved and Scribe holds it." reports on a sprint; it does not plan one (rehearsal 3, O3).
SPRINT_RE = re.compile(r'^\s*(?:[-*•]\s*)?(?:#+\s*)?sprint\s+(\d+)(?!\s+(?:is|was|has|had|are|were|will|now|stays|remains)\b)\s*[:.\-–—]?\s*(.+?)\s*$', re.I)
TASK_RE = re.compile(r'^\s*(?:[-*•]|\d+[.)])\s*(?:[A-Za-z]{0,3}\d+\.\d+\s*[:.)\-]?\s*)?(?:task\s*[:\-]?\s*)?(.+?)\s*(?:\(([^)]+)\))?\s*$', re.I)
ROLE_RE = re.compile(r'([A-Z][\w\-]*(?: [A-Z][\w\-]*)*)\s*(?:=|:|\bas\b|\bis\b)\s*(?:the\s+|an?\s+|our\s+)?([a-z][\w\-]*)')
# A verdict line names its subject, then a separator, then PASS or FAIL: "requirement 3: PASS, because",
# "| 4 | Half-open window | PASS | why |", "- Exact keys - FAIL: missing ids". Prose that merely mentions the words
# ("a FAIL if it isn't") is not a verdict.
VERDICT_RE = re.compile(r'^\s*(?:[-*•]\s*)?(?:\|\s*)?(?:(?:requirement|req\.?|r)?\s*(\d+)\s*[:.\-–|]\s*)?([^|]{0,160}?)\s*[:\-–|]\s*\**\s*(PASS|FAIL)\b\**\s*(.*)$', re.I)
# A whole review in one sentence: "PASS on all eighteen requirements I checked", "All 12 requirements PASS" (run a74),
# "Verdicts: R1-R19 all PASS.", "Verdicts, all PASS on my own evidence:" (run a75), "R1–R16 all PASS, unchanged" (run a84),
# "**Verdicts:** all 18 requirements PASS —" (run a90).
OVERALL_RE = re.compile(r'^\s*(?:[-*•]\s*)?\**\s*(?:(PASS|FAIL)\s+(?:on|for)\s+(?:all|every|each)\b[^.;]{0,40}?\brequirements?\b'
                        r'|(?:all|every)\s+(?:\w+\s+){0,2}requirements?\s*(?:are\s+|is\s+|[:\-]\s*)?(PASS|FAIL)\b'
                        r'|verdicts?\b[^.;]{0,40}?\ball\s+(?:\w+\s+){0,2}?(?:requirements?\s+)?(?:are\s+)?(PASS|FAIL)\b'
                        r'|[^.;]{0,60}?\b(?:requirements?\s+|req\.?\s*|r)?\d+\s*[–—\-]\s*(?:requirements?\s+|req\.?\s*|r)?\d+\s*(?:are\s+|is\s+)?all\s+\**((?-i:PASS|FAIL))\b)(.*)$', re.I)
# A re-review moves a verdict: "R16 `docs/OPERATOR_GUIDE.md`: FAIL → **PASS**." The verdict is the one it moved to (run a78).
NUMBERED_SUBJECT_RE = re.compile(r'^\W*(?:requirements?|req\.?|r)\s*(\d+)(?:\s*[–—\-]\s*(?:requirements?|req\.?|r)?\s*(\d+))?\b', re.I)
ALL_SUBJECT_RE = re.compile(r'^\W*(?:all|every)\b(?:\s+\w+){0,2}?\s*requirements?\W*$', re.I)
MOVED_RE = re.compile(r'^\s*(?:→|->|=>|⇒|➜|now|to|becomes?)\s*\**\s*(PASS|FAIL)\b\**\s*(.*)$', re.I)
FILE_RE = re.compile(r'`([^`\s]+\.[A-Za-z0-9]{1,8})`')
# The operator's word may open the note or a later sentence ("Budget: 50 calls. Accepted.").
GO_RE = re.compile(r'(?:^|[.!?\n]\s*)(go|approved?|proceed|yes,? go)\b', re.I)
ACCEPT_RE = re.compile(r'(?:^|[.!?\n]\s*)(accepted?|accept(?:ed)? the deliverable|done,? accepted)\b', re.I)
CHANGE_RE = re.compile(r'^\s*(change|revise|not yet|please change)\b', re.I)
BUDGET_RE = re.compile(r'budget\s*(?:[:=]|\bof\b|\bis\b)?\s*(?:about|around|up to|at most)?\s*(\d+)\s*(model calls|calls)', re.I)
DEFAULT_BUDGET = 60     # model calls for a project whose brief names none; the host says so once, and a note can change it
DELIVER_RE = re.compile(r'\bdeliverable\b[^`\n]*`([^`\s]+)`', re.I)
LEARNED_RE = re.compile(r'\b(learned|learnt|lessons?|next time|carry)\b', re.I)
# The operator's ruling, on a line of its own: "Decision: F2 stays.", "Decided (F1): its own ticket.", "Decision on F2: ...".
DECISION_RE = re.compile(r'^\s*(?:[-*•]\s*)?(?:decision|decided)\s*(?:\(([^)]{1,40})\)|\bon\s+([^:]{1,40}))?\s*:\s*(.{4,})$', re.I)
# Capitalised words that open a sentence without naming anyone: "Nothing is accepted until..." (rehearsal 3, F10b).
NOT_PEOPLE = {'nothing', 'nobody', 'none', 'no one', 'everyone', 'everybody', 'everything', 'someone', 'somebody',
              'something', 'anyone', 'anything', 'this', 'that', 'these', 'those', 'it', 'i', 'we', 'you', 'they', 'he',
              'she', 'there', 'here', 'all', 'each', 'which', 'what', 'who', 'the work', 'the plan', 'the review',
              'me', 'myself', 'us', 'ourselves'}                 # a lead naming itself in its plan: "T10 Me: verify"


def _person(name: str) -> bool:
    return bool(name) and name.strip().lower() not in NOT_PEOPLE


def _unlabel(name: str) -> str:
    """'T2 Scribe' is task T2's holder, Scribe (automated run a72: a closing invitation went to "T2 Scribe")."""
    return re.sub(r'^[A-Za-z]{1,3}\d+(?:[.\-]\d+)*\s+(?=\S)', '', name.strip())


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
                                   request_kind=request_kind if request_kind in ('library', 'chat') else 'project')
            if request_kind == 'chat':                    # a direct chat names the one agent it is with
                facts['opened']['chat_with'] = (message.get('facts') or {}).get('chat_with')
        else:
            m = BUDGET_RE.search(text)
            if m:
                facts['budget_model_calls'] = int(m.group(1))
        chat = (state.get('campaign', {}).get('request_kind') == 'chat'
                or facts.get('opened', {}).get('request_kind') == 'chat')
        if chat:
            pass                                          # a chat has no sprint to approve and nothing to accept
        elif state.get('campaign', {}).get('opened_by') and ACCEPT_RE.search(text):
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
                    name = _unlabel(name)
                    if role.lower().endswith('ed'):
                        continue                         # "X is accepted": a state, not a role
                    if name.lower() not in ('roster', 'sprint') and _person(name) and name not in seen:
                        seen.add(name); hires.append((name, role))
        for sprint in sprints:                                   # whoever holds a task is on the roster
            for task in sprint['tasks']:
                who = task.get('who')
                if not who:
                    m = re.match(r'\s*([A-Z][\w\-]*(?: [A-Z][\w\-]*)*)\s*:\s*\S', task['title'])
                    who = m.group(1) if m else None
                if who:
                    who = _unlabel(who)
                if who and re.fullmatch(r'[A-Za-z]{1,3}\d+(?:[.\-]\d+)*', who):
                    who = None                           # a task label such as T1 or S2.1, not a person (rehearsal 2, F10)
                if who and _person(who) and who not in seen and who.lower() not in ('sprint', 'roster', 'budget', 'note', 'deliverable', 'deliverables'):
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
    # What a mind said with its signal tools (record_verdicts, mark_ready, give_closing_words) is read first and exactly;
    # the prose below is the fallback for a mind that did not use them.
    signals = ((message.get('facts') or {}).get('memory') or {}).get('signals') or {}
    if kind == 'lead':                                    # the lead recording the operator's words with its tool
        filed = ((message.get('facts') or {}).get('memory') or {}).get('decisions_filed') or []
        if filed:
            facts['decisions'] = [dict(words=d['words'], subject=d.get('subject'), recorded_by_lead=True) for d in filed if d.get('words')]
        if signals.get('ready'):
            facts['ready'] = dict(summary=str(signals['ready'])[:600], by_tool=True)
    if kind == 'reviewer' and signals.get('verdicts'):
        facts['verdicts'] = [dict(subject=(f"requirement {v['requirement']}" if str(v['requirement']).isdigit() else str(v['requirement'])),
                                  verdict=str(v['verdict']).upper(), why=str(v.get('why') or ''), by_tool=True)
                             for v in signals['verdicts'] if str(v.get('verdict', '')).upper() in ('PASS', 'FAIL') and v.get('requirement')]
    if kind == 'reviewer' and 'verdicts' not in facts:
        verdicts = []
        for line in _lines(text):
            m = VERDICT_RE.match(line)
            if m and m.group(3) and not re.search(r'\b(if|unless|would|will|whether)\b', line.split(m.group(3), 1)[0], re.I):
                subject = (m.group(2) or '').strip(' -:') or (f'requirement {m.group(1)}' if m.group(1) else '')
                if m.group(1) and re.match(r'(?:r|req\.?|requirements?)?\s*\d+\b', subject, re.I):
                    subject = f'R{m.group(1)}–{subject}'          # "R1–R12 code: PASS" is a range, not "R12 code" (run a83)
                verdict, why = m.group(3), m.group(4)
                moved = MOVED_RE.match(why)
                if moved:
                    verdict, why = moved.groups()
                if subject:
                    verdicts.append(dict(subject=subject, verdict=verdict.upper(), why=why.strip(' :,-	')))
        if not verdicts:
            for line in _lines(text):
                m = OVERALL_RE.match(line)
                if m and not re.search(r'\b(if|unless|would|whether)\b', line, re.I):       # a conditional is not a ruling
                    verdicts.append(dict(subject='every requirement', verdict=(m.group(1) or m.group(2) or m.group(3) or m.group(4)).upper(),
                                         why=m.group(5).strip(' :,-')[:300]))
                    break
        if verdicts:
            facts['verdicts'] = verdicts
    if (signals.get('closing') and state.get('acceptance') and kind != 'operator') or 'closure' in message.get('facts', {}) \
            or (state.get('acceptance') and kind != 'operator' and LEARNED_RE.search(text)):
        facts['closure'] = dict(participant=message['sender'], text=text.strip())
    return facts


def rebuild(daybook) -> dict:
    """Fold the daybook into the derived state. Pure: the same daybook always gives the same facts."""
    state: dict = dict(campaign=dict(id=daybook.campaign_id, opened_by=None, deliverable=None, budget_model_calls=None),
                       plan=dict(sprints=[], awaiting_approval=None), roster=[], approvals=[], change_requests=[],
                       deliverables=[], verdicts=[], acceptance=None, closures=[], decisions=[], questions_asked=[], withheld=0,
                       failed_turns=0, last_message_id=None, messages=0, library_reply=None, library_participants=[],
                       participants=[])
    for message in daybook.read():
        state['messages'] += 1
        state['last_message_id'] = message['id']
        # Every mind that has written here takes part, whether or not the plan names it: a team assigned on the Team
        # page is not in the plan's roster (rehearsal 2: no closing words were asked of an accepted team).
        sender = message.get('sender') or {}
        if (message['kind'] == 'message' and sender.get('kind') not in ('operator', 'host', None)
                and not any(p['id'] == sender.get('id') for p in state['participants'])):
            state['participants'].append(dict(id=sender.get('id'), kind=sender.get('kind'), name=sender.get('name')))
        if message['kind'] == 'withheld':
            state['withheld'] += 1
        if message['kind'] == 'turn_failed':
            state['failed_turns'] += 1
        if message['kind'] == 'host' and message.get('facts', {}).get('question'):
            state['questions_asked'].append(dict(message_id=message['id'], **message['facts']['question']))
        facts = extract(message, state) if message['kind'] == 'message' else dict(message.get('facts', {}))
        if 'opened' in facts:
            state['campaign'].update(opened_by=message['sender'], **facts['opened'])
            if state['campaign'].get('request_kind') == 'project' and state['campaign'].get('budget_model_calls') is None:
                state['campaign'].update(budget_model_calls=DEFAULT_BUDGET, budget_default=True)
        if state['campaign'].get('request_kind') == 'library' and message['kind'] == 'message':
            if message['sender']['kind'] == 'operator' and not state['acceptance'] and 'acceptance' not in facts:
                state['library_reply'] = None
            if message['sender']['kind'] == 'librarian':
                state['library_reply'] = message['id']
                if not any(p['id'] == message['sender']['id'] for p in state['library_participants']):
                    state['library_participants'].append(message['sender'])
        if 'budget_model_calls' in facts:
            state['campaign'].update(budget_model_calls=facts['budget_model_calls'], budget_default=False)
        approved = {a.get('sprint') for a in state['approvals']}
        if 'plan' in facts and state['plan']['sprints'] and all(s['number'] in approved for s in facts['plan']['sprints']):
            pass        # "Sprint 4 - lead verification. S4.1 done" reports on approved sprints; the plan stands (rehearsal 4)
        elif 'plan' in facts:
            # A plan said again keeps the approvals already given for its sprints (rehearsal 3, O3: the operator was
            # asked to approve sprint 1 twice).
            state['plan'] = dict(sprints=facts['plan']['sprints'], awaiting_approval=_next_sprint(facts['plan']['sprints'], state['approvals']),
                                 message_id=message['id'])
            state.setdefault('plan_notes', []).append(message['id'])
        if 'hires' in facts:
            for hire in facts['hires']:
                state['roster'].append(dict(hire, requested_in=message['id']))
        if 'hired' in facts:
            state['roster'].append(dict(facts['hired'], hired_in=message['id']))
        if 'approval' in facts:
            # One "go" approves the plan as it stands: every sprint not yet approved, one record each, sharing this
            # message's id. The minds work at their own pace; the operator is asked again only for sprints a later
            # plan adds (First Principles 1 and 2; rehearsals 1-4, O1: minds ran ahead of each per-sprint "go").
            covered = _awaiting(state['plan']['sprints'], state['approvals']) if facts['approval'].get('sprint') is not None else []
            for n in covered or [facts['approval'].get('sprint')]:
                state['approvals'].append(dict(facts['approval'], sprint=n, message_id=message['id']))
            state['plan']['awaiting_approval'] = _next_sprint(state['plan']['sprints'], state['approvals'])
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
                state['verdicts'].append(dict(v, by=message['sender'], message_id=message['id'], at_message=state['messages']))
        if message['kind'] == 'message' and sender.get('kind') == 'reviewer':
            state.setdefault('review_files', []).extend(f['name'] for f in message.get('files', ())
                                                        if f.get('name') and _is((f.get('written_by') or {}), sender))
        if (message['kind'] == 'message' and sender.get('kind') in ('lead', 'member')
                and message['id'] not in (state.get('plan_notes') or ())         # writing the plan is not work moving
                # nor is writing into the reviewer's own file (run a98: the lead added its verdict table to REVIEW.md)
                and any(_is((f.get('written_by') or {}), sender) and f.get('name') not in (state.get('review_files') or ())
                        for f in message.get('files', ()))):
            state['work_changed_at'] = state['messages']        # the work moved: a standing FAIL may be stale (run a80)
        if message['kind'] == 'message' and sender.get('kind') == 'lead':
            state['lead_spoke_at'] = state['messages']
        if 'ready' in facts:
            state['ready'] = dict(facts['ready'], message_id=message['id'], at_message=state['messages'])
        if (message['kind'] == 'message' and sender.get('kind') == 'reviewer' and 'verdicts' not in facts
                and (state.get('work_changed_at') or any(not (isinstance(d.get('by'), dict) and _is(d['by'], sender)) for d in _work(state)))
                and (re.search(r'\b(?:PASS|FAIL)\b', message.get('text') or '')
                     or (state.get('work_changed_at') and any(_is((f.get('written_by') or {}), sender) for f in message.get('files', ()))))):
            # It ruled in a form not read (run a84: "R1–R16 all PASS"), or only in the review file it wrote about others'
            # work (run a92); never about its own review alone (rehearsal 2, F9), and never before others' work is in
            # (run b01: "I will write PASS/FAIL per requirement" was read as a ruling, and asked for one before any work).
            state['review_unread'] = True
        if message['kind'] == 'host' and (facts.get('quiet') or {}).get('kind') == 'lead':
            state['lead_quiet_at'] = state['messages']          # woken, read the notes, had nothing to add
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
    state['plan']['awaiting_sprints'] = _awaiting(state['plan']['sprints'], state['approvals'])
    return state


def _work(state: dict) -> list:
    """Deliverables that are work for review: not the files a plan note named (run b01: the lead's PLAN.md read as "the
    work under review" before anyone had worked), and not a reviewer's own files."""
    plans = set(state.get('plan_notes') or ())
    return [d for d in state['deliverables'] if not (isinstance(d, dict) and (d.get('message_id') in plans
            or (isinstance(d.get('by'), dict) and d['by'].get('kind') == 'reviewer')))]


def _awaiting(sprints: list, approvals: list) -> list:
    """The plan's sprints not approved yet, lowest number first."""
    approved = {a.get('sprint') for a in approvals}
    return sorted(s['number'] for s in sprints if s['number'] not in approved)


def _next_sprint(sprints: list, approvals: list):
    """The lowest-numbered sprint of the plan not approved yet, or None."""
    waiting = _awaiting(sprints, approvals)
    return waiting[0] if waiting else None


def go_notes(state: dict) -> int:
    """How many times the operator said "go": one note may approve several sprints."""
    return len({a.get('message_id') for a in state['approvals']})


def _span(numbers: list) -> str:
    return f'Sprint {numbers[0]}' if len(numbers) == 1 else f'Sprints {numbers[0]}-{numbers[-1]}' \
        if numbers == list(range(numbers[0], numbers[-1] + 1)) else 'Sprints ' + ', '.join(map(str, numbers))


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


def _is(a: dict, b: dict) -> bool:
    """The same mind: equal ids, or equal names; loosely only when one side has no id (a plan names minds in words).
    _same alone is too loose here: mind_demo_lead_001 and mind_demo_author_001 share "mind" and "demo"."""
    low = lambda v: str(v or '').strip().lower()
    if a.get('id') and b.get('id') and low(a['id']) == low(b['id']):
        return True
    if a.get('name') and b.get('name') and low(a['name']) == low(b['name']):
        return True
    if a.get('id') and b.get('id') and low(a['id']) != low(a.get('name')) and low(b['id']) != low(b.get('name')):
        return False
    return any(_same(x, y) for x in (a.get('id'), a.get('name')) for y in (b.get('id'), b.get('name')))


def _team(state: dict) -> list:
    """The plan's roster, the librarian who answered, and every other mind that has written here, each once. A mind that
    wrote here replaces the plan's name for it, so a question reaches its id (run a87: the plan said "Lead", and the
    closing-words invitation went to an id "Lead" that no mind has)."""
    low = lambda v: str(v or '').strip().lower()
    # The same mind for certain: one id, one name, or the plan naming it by its kind ("Lead" for the lead). A looser
    # likeness ("Harbor Steward" and "Harbor Guide") never drops or merges a mind that has an id.
    certain = lambda a, b: ((a.get('id') and low(a.get('id')) == low(b.get('id'))) or (a.get('name') and low(a.get('name')) == low(b.get('name')))
                            or (not a.get('id') and low(a.get('name')) in {low(b.get('kind')), low(b.get('role'))} - {''}))
    team = []
    for member in state['roster'] + state.get('library_participants', []) + state.get('participants', []):
        match = next((i for i, m in enumerate(team) if certain(m, member) or certain(member, m)), None)
        if match is not None:
            if member.get('id') and not team[match].get('id'):
                team[match] = {**team[match], **member}
        elif not any(_is(member, m) for m in team) or (member.get('id') and not any(m.get('id') and _is(member, m) for m in team)):
            team.append(member)
    return team


def subject_keys(subject: str) -> list:
    """What a verdict is about, as keys: "R13 ...", "Requirement 13 (...)" and "requirement 13" are one requirement, and
    "R1–R15 (code)" is fifteen (run a83: a re-review said "Requirement 13" where the review had said "R13")."""
    m = NUMBERED_SUBJECT_RE.match(subject)
    if not m:
        return [' '.join(subject.lower().split())]
    first, last = int(m.group(1)), int(m.group(2) or m.group(1))
    return [f'requirement {n}' for n in range(first, last + 1)] if 0 < last - first < 100 else [f'requirement {first}']


def standing(verdicts: list) -> dict:
    """The verdicts that stand, by requirement: the latest on each, and a verdict on every requirement replaces all
    before it."""
    latest = {}
    for v in verdicts:
        subject = str(v.get('subject') or '')
        if ALL_SUBJECT_RE.match(subject):          # "every requirement", "All requirements", "All 18 requirements"
            latest = {}
        for key in subject_keys(subject):
            latest[key] = v
    return latest


def waiting_on(state: dict) -> str:
    """One line for every wake: whose move it is, so a quiet room always has an owner. Said, never enforced."""
    name = lambda m: m.get('name') or m.get('id') or '?'
    if state['acceptance']:
        open_ = [name(m) for m in _team(state) if m.get('id') and not any(_is(m, c['participant']) for c in state['closures'])]
        return ('closing words from ' + ', '.join(open_) + ' (give_closing_words)') if open_ else 'nothing: the commission is accepted and closed'
    if not state['plan']['sprints']:
        return "the lead's plan: one line per sprint, one per task"
    if state['plan'].get('awaiting_approval') is not None:
        return f"the operator's \"go\" on sprint {state['plan']['awaiting_approval']}"
    if not _work(state) and not state['verdicts']:
        return 'the work in the plan: whoever holds a task names its deliverable when it is in'
    held = standing(state['verdicts'])
    if not held:
        # The plan orders what follows the work (run a98: "the reviewer's verdicts" sent the reviewer to the live tree
        # before the lead's planned freeze; the lead had said "nothing for you yet").
        return "the plan's next step now that work is in; the reviewer rules (record_verdicts) once the plan hands the work over"
    failed = list(dict.fromkeys(v['subject'] for v in held.values() if v['verdict'] == 'FAIL'))
    last_verdict = max(v.get('at_message', 0) for v in state['verdicts'])
    if failed and state.get('work_changed_at', 0) > last_verdict:
        return f"the reviewer's re-review: FAIL on {'; '.join(failed[:4])} predates changed work (record_verdicts)"
    if (state.get('ready') or {}).get('at_message', 0) > last_verdict:
        # Ready with a FAIL disclosed is the operator's to rule on, not a correction anyone owes (run a98: "corrections for
        # FAIL" stood after the lead said the miss could not be corrected, and the reviewer repeated it).
        return "the operator's ruling: accept, or say what to change" + (f" (the lead says ready with FAIL on {'; '.join(failed[:4])} disclosed)" if failed else '')
    if failed:
        return f"corrections for FAIL on {'; '.join(failed[:4])}, then the reviewer's re-review (record_verdicts)"
    if state.get('lead_spoke_at', 0) > last_verdict:
        return "the operator's ruling if the lead's latest note says the work is ready; otherwise the lead's next step"
    return "the lead: every requirement passes; is the work ready for the operator's ruling? (mark_ready)"


def questions(state: dict) -> list:
    """What the host still needs, and whom to ask. Each is {to, text, about}."""
    asked = {(q['to'], q['about']) for q in state['questions_asked']}
    out = []
    if (state['campaign']['opened_by'] and not state['plan']['sprints'] and not state['acceptance']
            and state['campaign'].get('request_kind') not in ('library', 'chat')):
        out.append(dict(to='lead', about='plan', text='What is the plan? One line per sprint, one per task.'))
        if any(p.get('kind') == 'lead' for p in state['participants']):
            # The lead wrote, yet no plan was read: "The plan is in the room" with the plan elsewhere (run a76).
            out.append(dict(to='lead', about='plan unread', text='I could not read a plan in your note, so nothing is approved '
                            'yet and no one should start. Write the plan in the note itself: one line per sprint ("Sprint 1 - '
                            '..."), then one line per task under it.'))
    if state['campaign'].get('budget_default') and not state['acceptance']:
        out.append(dict(to='operator', about='budget', text=f'The brief names no budget, so this project is limited to '
                        f'{DEFAULT_BUDGET} model calls. To change it, write "Budget: N model calls."'))
    if state['campaign']['opened_by'] and not state['acceptance'] and state['campaign'].get('request_kind') != 'chat':
        letter = ' '.join((state['campaign'].get('deliverable') or '').split())[:300].rstrip(' .')
        out.append(dict(to='librarian', about='shelf', text=f'A project opened: {letter}. Anything in the library for it? '
                        'Lend what fits (it lands in library/ under the project) and say what you lent and what you left.'))
    if state['plan']['sprints'] and state['plan']['awaiting_approval'] is not None and not state['acceptance']:
        n, waiting = state['plan']['awaiting_approval'], _awaiting(state['plan']['sprints'], state['approvals'])
        out.append(dict(to='operator', about=f'approve sprint {n}',
                        text=f'{_span(waiting)} {"is" if len(waiting) == 1 else "are"} planned. Say "go" to approve '
                             f'{"it" if len(waiting) == 1 else "the plan"}, or what to change.'))
    by_note = {}
    for approval in state['approvals']:                  # one "go" may approve several sprints: one message for them
        by_note.setdefault(approval.get('message_id'), []).append(approval.get('sprint'))
    for numbers in by_note.values():
        sprints = [sp for n in numbers for sp in state['plan']['sprints'] if sp['number'] == n]
        if not sprints:
            continue
        work = ' | '.join(f"Sprint {sp['number']}: " + ('; '.join(t['title'] + (f" ({t['who']})" if t.get('who') else '')
                                                               for t in sp['tasks']) or sp['title']) for sp in sprints)
        if len(work) > 1500:
            work = work[:1500].rstrip() + '...'
        label = _span([sp['number'] for sp in sprints])
        for pid in ('lead', 'member', 'reviewer'):
            out.append(dict(to=pid, about=f"start sprint {sprints[0]['number']}",
                            text=f'{label} {"is" if len(sprints) == 1 else "are"} approved: {work}. Work in order at your '
                                 'own pace. Whoever holds a task: go ahead, and name the deliverable when it is in.'))
    team = _team(state)
    # Role or kind: a plan that gives the reviewer a task makes its role "assignee", and its kind still says reviewer.
    critics = [m for m in team if any(str(x or '').lower() in ('critic', 'reviewer', 'review') for x in (m.get('role'), m.get('kind')))]
    if state['verdicts'] and not state['acceptance']:
        # A FAIL given before the work moved still stands until the reviewer looks again; nobody else can lift it, and
        # a team that thinks itself done waits on the operator while the operator waits on a PASS (run a80).
        for critic in critics:
            mine = [v for v in state['verdicts'] if isinstance(v.get('by'), dict) and _is(v['by'], critic)]
            failed = list(dict.fromkeys(v['subject'] for v in standing(mine).values() if v['verdict'] == 'FAIL'))
            if failed and state.get('work_changed_at', 0) > mine[-1].get('at_message', 0):
                out.append(dict(to=critic.get('id') or critic.get('name'), about=f"re-review {mine[-1]['message_id']}",
                                text=f'Your latest verdicts still say FAIL on {"; ".join(failed[:4])}, and the work has '
                                     'changed since. Look again and reply with one line per requirement, such as '
                                     '"Requirement 16: PASS, because ...", or one line "All requirements: PASS" if every one passes.'))
        # Every requirement passes and the lead, woken since, had nothing to add: only the lead can say whether the work is
        # ready, and without that the operator has nothing to rule on (run a82: 14 PASS, then a quiet room).
        mine = [v for v in state['verdicts'] if any(isinstance(v.get('by'), dict) and _is(v['by'], c) for c in critics)]
        held = standing(mine)
        since = mine[-1].get('at_message', 0) if mine else 0
        if (held and all(v['verdict'] == 'PASS' for v in held.values())
                and state.get('lead_spoke_at', 0) < since < state.get('lead_quiet_at', 0)):
            out.append(dict(to='lead', about=f"ready {mine[-1]['message_id']}",
                            text='The reviewer passes every requirement. Is the work ready for the operator\'s ruling? Say so, '
                                 'or say what is left and who holds it.'))
    if (_work(state) or state.get('review_unread')) and not state['verdicts'] and not state['acceptance']:
        for critic in critics:
            pid = critic.get('id') or critic.get('name')
            # A reviewer is asked about others' work, never its own review (rehearsal 2, F9), nor about the plan (run b01).
            theirs = [d for d in _work(state)[-6:] if not (isinstance(d, dict) and isinstance(d.get('by'), dict) and _is(d['by'], critic))]
            if not theirs and not state.get('review_unread'):
                continue
            files = ', '.join(d.get('name') or d.get('path') if isinstance(d, dict) else str(d) for d in theirs) or 'the work under review'
            # Said plainly, so a reviewer whose prose verdict was not read does not think it already answered (run a75).
            out.append(dict(to=pid, about='verdicts', text=f'Deliverables are in ({files}). I have no verdict from you that I can '
                            'read yet. Reply with one line per requirement, such as "Requirement 1: PASS, because ...", or one '
                            'line "All requirements: PASS" if every one passes.'))
    if state['acceptance']:
        for member in team:
            pid = member.get('id') or member.get('name')
            if pid and not any(_is(member, c['participant']) for c in state['closures']):
                text = 'The operator accepted. In your own words: what did you do, what did you learn, what should your future self carry, and what should your next self skip?'
                if str(member.get('kind') or member.get('role') or '').lower() == 'lead':
                    # An invitation, not a filing done for the lead: what binds next time stays the lead's call (rehearsal 2, T07).
                    # Accepting the work is not itself a ruling (rehearsal 3, O4: the lead filed "Accepted." as a decision).
                    text += (' If the operator ruled on anything during this commission, beyond accepting it, you may record it with '
                             'file_decision in their words, and say in what you carry whether it should guide similar work.')
                out.append(dict(to=pid, about='closure', text=text))
    return [q for q in out if (q['to'], q['about']) not in asked]


def write(daybook, state: dict) -> Path:
    """facts.json is derived from the daybook, so a write that loses a race with another place is retried and,
    failing that, skipped: the next rebuild writes it again. Nothing in the record depends on it."""
    import os
    import time
    import uuid
    path = daybook.root / 'facts.json'
    # One temporary file per write: the server's request thread and its hosting thread share a process, and with a
    # per-process name one renamed the other's file away, so an operator's note failed with a 409 (run a96).
    tmp = path.with_suffix(f'.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding='utf-8')
    for attempt in range(10):
        try:
            tmp.replace(path)
            return path
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
        except FileNotFoundError:
            return path                                  # lost the race outright: the next rebuild writes it again
    try:
        tmp.unlink()
    except OSError:
        pass
    return path
