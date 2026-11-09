"""A mind's cartridge in the atelier: authored with one, closes into lessons, warnings and facts,
wakes with headers, loads what it chooses, and an older home moves in. No model, no network."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aether.atelier import mind_memory   # noqa: E402
from aether.atelier.participants import Participant   # noqa: E402
from aether.atelier.turn import wake_messages, run_turn   # noqa: E402
from aether.atelier import Atelier   # noqa: E402
from aether.atelier.postmaster import Postmaster   # noqa: E402
from aether.mailbox import Mailbox   # noqa: E402

CLOSING = """**Closing words — Harbor Guide, harbor-ledger-a**

**What I did.** I implemented `daily_report(rows, start, end)` in `outputs/repository/harbor/reports.py` and reviewed it as a stranger would. The function validates every row before it selects any, including rows outside the interval, rejecting duplicate IDs, non-USD currencies, malformed amounts and inverted or naive intervals with ValueError; it sums in integer minor units, returns exactly count, total_minor and ids, and never touches caller inputs. I wrote sixteen tests and a separate edge-case file, the operator guide mirrored at outputs/GUIDE.md, IMPLEMENTATION.md, and a separately authored REVIEW.md with six findings and their dispositions. 35 tests pass. The last thing I checked was the byte count of GUIDE.md, which was 6236.

**What I learned.**
- The review was worth more than the code: writing REVIEW.md against my own patch surfaced F2, a total cap CONVENTIONS.md never asks for.
- Validate first, select second; keep it visible in the code and locked by a test.

**What my future self should carry.**
- In this repo, a cap on amounts is not a cap on totals. Don't infer one; if you add one, disclose it and make it deletable in one line.
- Write the review as if a stranger wrote the patch.
"""


def _mind(tmp_path, cartridge=True):
    return Participant.author(tmp_path / 'homes' / 'guide', id='guide', kind='member', name='Guide', system='You are Guide.',
                              roots={'outputs': 'write'}, mailboxes=tmp_path / 'share', cartridge=cartridge)


def test_an_authored_mind_is_born_with_seeds_and_wakes_to_them(tmp_path):
    p = _mind(tmp_path)
    assert (p.home / 'memory' / 'mind.sqlite').is_file() and p.cartridge()['owner'] == 'guide'
    wake = p.wake_memory(['anything'])
    assert 'your first morning' in wake and 'Example warning' in wake
    engine = mind_memory._open_read(p.home)
    try:
        authors = {r[0] for r in engine.cart.conn.execute('SELECT written_by FROM notes').fetchall()}
        n = engine.cart.conn.execute('SELECT count(*) FROM notes').fetchone()[0]
    finally:
        engine.close()
    assert authors == {'Aether'} and n == 8  # six examples plus two ecosystem copies, all Aether's; nothing is the mind's own yet
    from aether.atelier import seeds
    assert seeds.plant(p.home, 'guide', 'Guide') == []          # planted once
    q = _mind(tmp_path / 'other', cartridge=False)
    assert q.cartridge() is None and q.wake_memory(['anything']) is None


def test_closing_words_become_lessons_warnings_and_facts(tmp_path):
    p = _mind(tmp_path)
    written = p.file_closure(CLOSING, commission_id='harbor-ledger-a', tags=['harbor'])
    kinds = sorted(k for k, _, _ in written['cartridge_notes'])
    assert kinds == ['project_fact', 'project_fact', 'safety_warning', 'standing_lesson', 'standing_lesson', 'standing_lesson'], kinds
    assert all(k != 'refreshed' for k, _, _ in written['cartridge_notes'])      # the seeds' example titles did not swallow the mind's own
    titles = {t for _, _, t in written['cartridge_notes']}
    assert any('stranger' in t for t in titles) and any('cap on amounts' in t for t in titles)
    assert (p.home / 'workspace' / 'SESSION_CURRENT.txt').read_text(encoding='utf-8').startswith('Carry after harbor-ledger-a')


def test_the_wake_shows_headers_not_bodies_and_load_reads_one(tmp_path):
    p = _mind(tmp_path)
    p.file_closure(CLOSING, commission_id='harbor-ledger-a')
    heads = p.recall(['review', 'stranger'])
    assert heads and all('body' not in h for h in heads)
    chosen = next(h for h in heads if 'stranger' in h['title'] and not h['title'].startswith('Example'))
    assert chosen['kind'] == 'standing_lesson'
    body = p.load_memory([chosen['id']])
    assert body and 'stranger wrote the patch' in body[0]['body']
    facts = dict(campaign=dict(id='harbor-ledger-b', deliverable='Project B: weekly report with a review written as a stranger would'),
                 plan=dict(sprints=[]), roster=[], approvals=[], acceptance=None, verdicts=[], deliverables=[])
    context = dict(unread=[], unread_total=0, budget_left={}, prior_closures=[], questions_for_me=[])
    user = wake_messages(p, context, facts)[1]['content']
    assert 'YOUR MEMORY (headers' in user and 'stranger' in user
    assert 'byte count of GUIDE.md' not in user          # the closing words are not pasted whole into the wake
    assert 'YOUR LAST WORDS' in user and 'YOUR CARRY (what your last self left you)' not in user


def test_an_older_home_moves_in(tmp_path):
    p = _mind(tmp_path, cartridge=False)
    p.file_closure(CLOSING, commission_id='harbor-ledger-a')
    p.file_closure('I passed the result. Lesson: cite the file in every verdict.', commission_id='harbor-ledger-b')
    assert p.cartridge() is None
    report = p.move_in()
    assert p.cartridge() and len(report['filed']) == 2 and not report['skipped'] and len(report['seeds']) == 6
    again = p.move_in()
    assert not again['filed'] and len(again['skipped']) == 2
    assert any('cite the file' in h['title'] for h in p.recall(['verdict']))


def test_words_are_plain_and_bounded():
    assert mind_memory.words('The weekly settlement report, EUR as well as USD, for the Harbor Ledger', 4) == ['weekly', 'settlement', 'report', 'well']


class _ReadsOneMemory:
    """A scripted model: loads the first memory its wake offered, writes a file, logs a decision, then writes its note."""
    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            import re
            wake = messages[1]['content']
            recall = wake.split('YOUR MEMORY (headers chosen', 1)[1].split('\n\n', 1)[0]
            first = re.search(r'\[(n-[0-9a-f]+)\]', recall).group(1)
            return dict(role='assistant', content='', tool_calls=[dict(id='m1', type='function', function=dict(name='load_memory', arguments='{"ids": ["%s"]}' % first))])
        if self.calls == 2:
            return dict(role='assistant', content='', tool_calls=[
                dict(id='w1', type='function', function=dict(name='write_file', arguments='{"path": "outputs/result.md", "content": "# Result\\nDone.\\n"}')),
                dict(id='l1', type='function', function=dict(name='log_action', arguments='{"line": "decided to keep the cap per currency", "tags": ["cap", "decision"]}'))])
        return dict(role='assistant', content='I read my memory and I am on it.', _usage=dict(prompt_tokens=10, completion_tokens=5))


def test_the_daybook_keeps_a_recall_receipt(tmp_path):
    share = tmp_path / 'share'
    p = _mind(tmp_path)
    p.file_closure(CLOSING, commission_id='harbor-ledger-a')
    atelier = Atelier(share, 'harbor-ledger-b')
    atelier.open(dict(id='operator', kind='operator', name='Operator'), 'Project B: a weekly report, reviewed as a stranger would. Budget: 20 model calls.')
    post = Postmaster(atelier, share)
    post.exchange({p.id: p.kind}, {p.id: p.name}, only=[p.id])
    root = share / 'work' / 'harbor-ledger-b'; root.mkdir(parents=True)
    sent = run_turn(p, atelier, Mailbox(share, p.record()), _ReadsOneMemory(), root)
    assert sent is not None
    post.exchange({p.id: p.kind}, {p.id: p.name}, only=[p.id])
    note = [n for n in atelier.daybook.read() if n['sender'].get('id') == 'guide'][-1]
    receipt = note['facts']['memory']
    assert receipt['offered'] and receipt['loaded'] and receipt['loaded'][0]['id'] == receipt['offered'][0]
    assert receipt['loaded'][0]['kind'] in ('standing_lesson', 'safety_warning', 'project_fact')


def test_the_side_log_keeps_one_row_per_action_and_the_next_wake_shows_it(tmp_path):
    share = tmp_path / 'share'
    p = _mind(tmp_path)
    p.file_closure(CLOSING, commission_id='harbor-ledger-a')
    atelier = Atelier(share, 'harbor-ledger-b')
    atelier.open(dict(id='operator', kind='operator', name='Operator'), 'Project B: a weekly report. Budget: 20 model calls.')
    post = Postmaster(atelier, share)
    post.exchange({p.id: p.kind}, {p.id: p.name}, only=[p.id])
    root = share / 'work' / 'harbor-ledger-b'; root.mkdir(parents=True)
    assert run_turn(p, atelier, Mailbox(share, p.record()), _ReadsOneMemory(), root) is not None
    rows = p.log_recent('harbor-ledger-b', limit=10)
    kinds = [r['kind'] for r in rows]
    assert kinds[0] == 'posted' and 'wrote' in kinds and 'read-memory' in kinds and 'noted' in kinds, kinds
    noted = next(r for r in rows if r['kind'] == 'noted')
    assert noted['by'] == 'self' and 'cap' in noted['tags'] and noted['line'].startswith('decided')
    wrote = next(r for r in rows if r['kind'] == 'wrote')
    assert wrote['pointer'].startswith('sha256:') and 'result.md' in wrote['line']
    assert p.log_search(['cap'])[0]['kind'] == 'noted' and p.log_search(['nothing-like-this']) == []
    facts = dict(campaign=dict(id='harbor-ledger-b', deliverable='Project B'), plan=dict(sprints=[]), roster=[], approvals=[], acceptance=None, verdicts=[], deliverables=[])
    context = dict(unread=[], unread_total=0, budget_left={}, prior_closures=[], questions_for_me=[])
    user = wake_messages(p, context, facts)[1]['content']
    assert 'YOUR LOG, this commission' in user and 'decided to keep the cap' in user
    written = p.file_closure('**Closing words**\n\n**What I learned.**\n- Say the acceptance criteria in the plan before anyone writes code.\n', commission_id='harbor-ledger-b')
    did = next(t for k, _, t in written['cartridge_notes'] if k == 'project_fact' and t == 'what I did')
    body = p.load_memory([next(i for k, i, t in written['cartridge_notes'] if t == 'what I did')])[0]['body']
    assert 'From the side log' in body and 'wrote outputs/result.md' in body


def test_reviews_by_others_and_the_acceptance_enter_the_record_once(tmp_path):
    guide = _mind(tmp_path)
    minder = Participant.author(tmp_path / 'homes' / 'minder', id='minder', kind='reviewer', name='Minder', system='You are Minder.',
                                mailboxes=tmp_path / 'share')
    by_minder = dict(id='minder', kind='reviewer', name='Minder'); operator = dict(id='operator', kind='operator', name='Operator')
    facts = dict(verdicts=[dict(verdict='FAIL', subject='req 1', why='missing', by=by_minder, message_id='m1'),
                           dict(verdict='PASS', subject='req 1', why='fixed', by=by_minder, message_id='m2'),
                           dict(verdict='PASS', subject='req 2', why='fine', by=by_minder, message_id='m2')],
                 acceptance=dict(text='Accepted.', message_id='m3', by=operator), plan=dict(sprints=[dict(number=1, title='x', tasks=[])]))
    record = guide.file_record(facts, 'harbor-ledger-b')
    assert record['reviews'] and record['reviews'][0]['reviewer'] == 'Minder' and record['reviews'][0]['rating'] == 5.0 and record['reviews'][0]['verdicts'] == 2
    assert record['outcome'] == dict(specialty='implementation', result='success', level=1)
    again = guide.file_record(facts, 'harbor-ledger-b')
    assert again['reviews'] == [] and again['outcome'] is None          # filed once
    mine = minder.file_record(facts, 'harbor-ledger-b')
    assert mine['reviews'] == [] and mine['outcome']['specialty'] == 'review'   # a reviewer gets no review from itself
    assert guide.card_line().startswith('YOUR CARD: implementation level 1;')
    level = mind_memory.record_outcome(guide.home, specialty='implementation', task_ref='oracle:harbor-ledger-c', result='failure', difficulty=3, verified_by='oracle')
    assert level == 1                                                    # a failure adds no points and takes none away


REPHRASED = """**Closing words — Harbor Guide, harbor-ledger-b**

**What I learned.**
- Validate first, select second — keep that order visible in the code and locked by a test.
- A green suite does not keep prose honest: re-verify every count the docs state at sign-off.

**What my future self should carry.**
- In this repo, the cap on amounts is not a cap on totals. Don't infer one from the other.
- Write the review as if a stranger wrote the patch.
"""


def test_the_same_lesson_in_other_words_is_reinforced_not_filed_twice(tmp_path):
    p = _mind(tmp_path)
    first = p.file_closure(CLOSING, commission_id='harbor-ledger-a')
    second = p.file_closure(REPHRASED, commission_id='harbor-ledger-b')
    kinds = [k for k, _, _ in second['cartridge_notes']]
    refreshed = [t for k, _, t in second['cartridge_notes'] if k == 'refreshed']
    assert any('Validate first' in t for t in refreshed), refreshed          # a longer phrasing of a filed lesson
    assert any('cap on amounts' in t for t in refreshed), refreshed          # "a cap" and "the cap"
    assert any('stranger' in t for t in refreshed), refreshed                # the same words
    assert kinds.count('standing_lesson') == 1                               # the one genuinely new lesson (the docs' counts)
    engine = mind_memory._open_read(p.home)
    try:
        own = engine.cart.conn.execute("SELECT count(*) FROM notes WHERE profile IN ('standing_lesson', 'safety_warning') AND written_by = 'guide'").fetchone()[0]
        seeds_n = engine.cart.conn.execute("SELECT count(*) FROM notes WHERE written_by = 'Aether'").fetchone()[0]
        cap = engine.cart.conn.execute("SELECT note_id FROM notes WHERE profile = 'safety_warning' AND written_by = 'guide'").fetchall()
        tags = {r[0] for r in engine.cart.conn.execute('SELECT tag FROM tags WHERE note_id = ?', (cap[0][0],)).fetchall()}
    finally:
        engine.close()
    assert own == 5 and seeds_n == 8 and len(cap) == 1           # 4 lessons + 1 warning of its own; the eight seeds untouched
    assert 'infer' in tags                                       # the second phrasing's words reach the one warning


def test_discovery_surfaces_a_lesson_phrased_unlike_the_letter(tmp_path):
    p = _mind(tmp_path)
    p.file_closure('''**Closing words**

**What I learned.**
- Comparing the paper figures beats capping them: every batch, before summing.
- Keep the tests deterministic.
''', commission_id='harbor-ledger-a')
    # the letter says "compare"; the lesson's words say "comparing": no tag in common, one stem in common (the Porter
    # stemmer does not unify "reconcile" and "reconciliation": that pair is a true miss, and the quiz may show more)
    heads = p.recall(mind_memory.words('Project C: compare the week against hand totals', 10))
    hit = next((h for h in heads if h['title'].startswith('Comparing the paper figures')), None)
    assert hit is not None, [h['title'] for h in heads]
    assert hit['via'] == 'discovery' and hit['kind'] == 'standing_lesson' and hit['strength'] > 0
    assert all(h['via'] != 'discovery' or h['id'] not in {x['id'] for x in heads if x['via'] != 'discovery'} for h in heads)


def test_skips_are_filed_from_the_closing_words_and_from_the_log_and_shown_at_wake(tmp_path):
    p = _mind(tmp_path)
    p.log([dict(kind='ran', pointer='tests', tags=['tests'], line='ran tests tests: 10 passed')] * 3 +
          [dict(kind='read-memory', pointer='n-abc', tags=['memory'], line='read memory: the cap')] * 2 +
          [dict(kind='wrote', pointer='sha256:1', tags=['file'], line='wrote outputs/x.md')] * 2, commission_id='harbor-ledger-b')
    written = p.file_closure('''**Closing words**

**What I learned.**
- The guard suite is what makes the shared validator safe.

**What my next self should skip.**
- Re-reading the whole Project A delivery before planning; the carry and the guide are enough.
- Running the full suite after every edit; once after the last edit.
''', commission_id='harbor-ledger-b')
    titles = [t for k, _, t in written['cartridge_notes'] if k == 'task_obligation']
    assert len(titles) == 4, written['cartridge_notes']                      # two said by the mind, two counted from the log
    assert any('Re-reading the whole Project A' in t for t in titles) and any('ran the tests at tests 3 times' in t for t in titles)
    assert any('read memory n-abc 2 times' in t for t in titles) and not any('wrote' in t for t in titles)
    engine = mind_memory._open_read(p.home)
    try:
        authors = dict(engine.cart.conn.execute("SELECT written_by, count(*) FROM notes WHERE profile = 'task_obligation' GROUP BY written_by").fetchall())
    finally:
        engine.close()
    assert authors == {'guide': 2, 'Aether': 2}                              # the log's observations are the host's words, labelled so
    wake = p.wake_memory(['anything'])
    assert 'YOUR SKIPS' in wake and 'Running the full suite after every edit' in wake


def test_any_heading_is_read_by_its_words_and_file_memory_files_now(tmp_path):
    parts = mind_memory.parse_closing('''# Closing note, Harbor Minder

## What I built
I reviewed the patch and ran the suite myself.

## Lessons from this one
- A passing test can encode a questionable decision.

### Things to remember next time
- Separate defect from judgement call in the verdict.

**What to avoid.**
- Re-running the whole suite after every edit.
''')
    assert parts['did'].startswith('I reviewed') and parts['learned'] == ['A passing test can encode a questionable decision.']
    assert parts['carry'] == ['Separate defect from judgement call in the verdict.'] and parts['skip'] == ['Re-running the whole suite after every edit.']
    p = _mind(tmp_path)
    filed = p.file_now('warning', 'Never hold the paper total to the amount cap', 'The operator\'s figure is compared, not validated like a row.', ['cap'], commission_id='harbor-ledger-c')
    assert filed['kind'] == 'safety_warning' and filed['id']
    again = p.file_now('warning', 'Never hold the paper total to the amount cap.', '', [], commission_id='harbor-ledger-c')
    assert again['id'] == filed['id'] and 'refreshed' in again['text']
    assert any(h['id'] == filed['id'] for h in p.recall(['paper', 'cap']))
    page = mind_memory.render_page(p.home)
    assert page.is_file() and page.stat().st_size > 10_000
