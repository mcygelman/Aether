"""Run a place, or speak as the operator (Fable, 2026-10-18).

  py -3.10 tools/atelier_place.py open  --share S --commission C "letter text"      the operator opens a commission
  py -3.10 tools/atelier_place.py say   --share S --commission C "go"               the operator posts a note
  py -3.10 tools/atelier_place.py author --share S --home H --id ID --kind member --name NAME --system "who it is" [--roots inputs=read outputs=write] [--no-cartridge]
  py -3.10 tools/atelier_place.py move-in --homes H1 H2                              give existing homes a cartridge; file their experiences
  py -3.10 tools/atelier_place.py serve --share S --homes H1 H2 --profiles runtime_profiles.json [--place NAME] [--once|--rounds N] [--fake] [--take]
  py -3.10 tools/atelier_place.py show  --share S --commission C                    the daybook as a transcript
  py -3.10 tools/atelier_place.py places --share S                                  every place's record, age, alive
  py -3.10 tools/atelier_place.py stop  --share S --place NAME                       ask a place to stop after its turn in flight

--fake answers every mind with a scripted reply (no model), for a smoke run of the whole loop.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aether.atelier import Atelier   # noqa: E402
from aether.atelier.participants import Participant   # noqa: E402
from aether.atelier.place import Place, PlaceClaimed   # noqa: E402

OPERATOR = dict(id='operator', kind='operator', name='Operator')


class FakeClient:
    """A scripted model for smoke runs: reacts to the role named in the system prompt and to the notes shown."""
    def chat(self, messages, tools=None):
        system, user = messages[0]['content'], messages[-1]['content'] if messages[-1]['role'] == 'user' else messages[1]['content']
        if 'Write your note to the atelier now' in user:      # the budget or the rounds ran out: answer from the wake
            user = messages[1]['content']
            if any(m.get('role') == 'tool' for m in messages):
                return dict(role='assistant', content='Done. The deliverable is `outputs/result.md`.', _usage=dict(prompt_tokens=100, completion_tokens=40))
        if 'ran out of tool rounds' in user: return dict(role='assistant', content='(nothing to add)')
        if 'the librarian of this commission' in system:
            results = [m.get('content') or '' for m in messages if m.get('role') == 'tool']
            def call(cid, name, **kw):
                return dict(role='assistant', content='', tool_calls=[dict(id=cid, type='function', function=dict(name=name, arguments=json.dumps(kw)))])
            if 'Anything on the shelf' in user:
                if not results: return call('s1', 'catalog_search', query='attachment svg cat walking fence transform')
                if len(results) == 1:
                    hits = json.loads(results[0]) if results[0].startswith('[') else []
                    if not hits: return dict(role='assistant', content='Nothing on the shelf fits this commission; I will watch for deposits.')
                    self.lent = hits[0]['id']; return call('l1', 'shelf_lend', item_id=hits[0]['id'], to='Assistant')
                text = f'Lent `{getattr(self, "lent", "?")}` into library/ for the Assistant; the rest of the shelf I left, nothing else spoke to the letter.'
            elif 'deposit' in user.lower():
                if not results: return call('d1', 'list_deposits')
                if len(results) == 1:
                    deps = json.loads(results[0]) if results[0].startswith('[') else []
                    if not deps: return dict(role='assistant', content='(nothing to add)')
                    return call('f1', 'file_deposit', deposit_id=deps[0]['id'], title='Michel: critique of M04', kind='critique',
                                summary='The cat floats a body-height above the rail; the shadow is on the rail, the feet are not.', tags=['svg-cat', 'critique', 'michel'])
                text = 'Filed the deposit on the shelf as `svgcat-critique-m04-michel`: the floating cat, in Michel\'s words.'
            else: text = '(nothing to add)'
            return dict(role='assistant', content=text, _usage=dict(prompt_tokens=100, completion_tokens=40))
        if 'the lead of this commission' in system:
            if 'PLAN SO FAR:\n(none yet)' in user:
                text = ('Sprint 1: implementation\n- implement the deliverable with a test (Assistant)\nSprint 2: review\n- review it (Critic)\n'
                        'I will hire Assistant as implementer and Critic as critic.')
            elif '[Operator]\ngo' in user and 'ACCEPTED: no' in user: text = 'To: Assistant\nYou are on: write the deliverable now.'
            elif 'ACCEPTED: yes' in user and 'Filed' not in user: text = 'We delivered it. What I learned: say the acceptance criteria in the plan. Carry: ask the critic first.'
            else: text = '(nothing to add)'
        elif 'the member of this commission' in system:
            if any(m.get('role') == 'tool' for m in messages): text = 'Done. The deliverable is `outputs/result.md`.'
            elif 'future self carry' in user: text = 'I wrote the result. Lesson: read the plan first. Carry: keep outputs small.'
            elif 'You are on' in user and 'DELIVERABLES:\n(none yet)' in user:
                return dict(role='assistant', content='', tool_calls=[dict(id='w1', type='function', function=dict(name='write_file', arguments='{"path": "outputs/result.md", "content": "# Result\\nDone.\\n"}'))])
            else: text = '(nothing to add)'
        else:
            if 'DELIVERABLES:\n(none yet)' not in user and 'VERDICTS:\n(none yet)' in user: text = 'requirement 1: result exists - PASS, outputs/result.md is present'
            elif 'future self carry' in user: text = 'I passed the result. Lesson: cite the file in every verdict.'
            else: text = '(nothing to add)'
        return dict(role='assistant', content=text, _usage=dict(prompt_tokens=100, completion_tokens=40))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0]); sub = ap.add_subparsers(dest='cmd', required=True)
    for stream in (sys.stdout, sys.stderr):      # the minds write arrows and dashes; a cp1252 console must not crash the tool
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    for name in ('open', 'say'):
        s = sub.add_parser(name); s.add_argument('--share', type=Path, required=True); s.add_argument('--commission', required=True); s.add_argument('text')
        s.add_argument('--files', nargs='*', type=Path, default=[])
    s = sub.add_parser('author'); s.add_argument('--share', type=Path, required=True); s.add_argument('--home', type=Path, required=True)
    s.add_argument('--id', required=True); s.add_argument('--kind', required=True); s.add_argument('--name', required=True); s.add_argument('--system', required=True)
    s.add_argument('--deployment', default='research'); s.add_argument('--roots', nargs='*', default=[]); s.add_argument('--library')
    s.add_argument('--no-cartridge', action='store_true', help='folder memory only (the default gives the mind a Memory Fabric cartridge)')
    s = sub.add_parser('move-in'); s.add_argument('--homes', type=Path, nargs='+', required=True)
    s = sub.add_parser('serve'); s.add_argument('--share', type=Path, required=True); s.add_argument('--homes', type=Path, nargs='+', required=True)
    s.add_argument('--profiles', type=Path); s.add_argument('--once', action='store_true'); s.add_argument('--rounds', type=int); s.add_argument('--fake', action='store_true')
    s.add_argument('--interval', type=float, default=3.0); s.add_argument('--place'); s.add_argument('--take', action='store_true')
    s = sub.add_parser('show'); s.add_argument('--share', type=Path, required=True); s.add_argument('--commission', required=True)
    s = sub.add_parser('places'); s.add_argument('--share', type=Path, required=True)
    s = sub.add_parser('stop'); s.add_argument('--share', type=Path, required=True); s.add_argument('--place', required=True)
    a = ap.parse_args(argv)
    if a.cmd in ('open', 'say'):
        atelier = Atelier(a.share, a.commission)
        if a.cmd == 'open':
            note = atelier.open(OPERATOR, a.text)
        else:
            refs = [atelier.daybook.store_file(f.name, f.read_bytes(), OPERATOR) for f in a.files]
            to, text = None, a.text
            first, _, rest = a.text.partition('\n')
            if first.lower().startswith('to:'):
                name = first[3:].strip().strip('.:-'); to = dict(id=name, kind='member', name=name); text = rest.strip() or a.text
            note = atelier.post(OPERATOR, text, files=refs, to=to)
        print('posted note', note['n'], 'to', a.commission); return 0
    if a.cmd == 'author':
        roots = dict(r.split('=', 1) for r in a.roots)
        p = Participant.author(a.home, id=a.id, kind=a.kind, name=a.name, system=a.system, deployment=a.deployment, roots=roots, mailboxes=a.share, library=a.library,
                               cartridge=not a.no_cartridge)
        print('authored', p.name, 'at', p.home, '' if a.no_cartridge else '(with a cartridge)'); return 0
    if a.cmd == 'move-in':
        for home in a.homes:
            p = Participant.load(home); r = p.move_in()
            print(p.name, 'moved in:', len(r['filed']), 'experience(s) filed', f"({sum(len(f['notes']) for f in r['filed'])} notes)", len(r['skipped']), 'already filed;', r['cartridge'])
        return 0
    if a.cmd == 'serve':
        place = Place(a.share, a.homes, name=a.place, client=FakeClient() if a.fake else None, profiles_path=a.profiles,
                      interval=a.interval, take=a.take)
        try:
            place.serve(rounds=1 if a.once else a.rounds)
        except PlaceClaimed as claimed:
            print('not started:', claimed); return 3
        return 0
    if a.cmd == 'places':
        probe = Place(a.share, [], name='_probe')
        for rec in probe.others():
            state = 'alive' if rec['alive'] else ('stopped' if rec.get('stopped') else 'silent')
            print(f"{rec['name']:<16} {state:<8} age {rec['age']:>7}s  pid {rec.get('pid')}  host {rec.get('host')}  minds {rec.get('minds')}")
        return 0
    if a.cmd == 'stop':
        flag = a.share / 'places' / f'{a.place}.stop'; flag.parent.mkdir(parents=True, exist_ok=True); flag.touch()
        print('stop asked of', a.place, '(it finishes the turn in flight, then leaves)'); return 0
    if a.cmd == 'show':
        atelier = Atelier(a.share, a.commission)
        for n in atelier.daybook.read():
            who = n['sender'].get('name') or n['sender']['id']; to = (' -> ' + (n['to'].get('name') or n['to']['id'])) if n.get('to') else ''
            files = ''.join(f'  [file {f["name"]}]' for f in n.get('files', ()))
            at = f" @{n['place']}" if n.get('place') else ''
            mem = (n.get('facts') or {}).get('memory')
            receipt = f"  [memory: {len(mem.get('loaded') or [])} read of {len(mem.get('offered') or [])} offered, {len(mem.get('recalled') or [])} lookups]" if mem else ''
            print(f"{n['n']:3d} {n['kind']:<11} {who}{to}{at}: {n['text'][:160].replace(chr(10), ' / ')}{files}{receipt}")
        print('facts:', {k: (len(v) if isinstance(v, list) else v) for k, v in atelier.facts().items() if k in ('approvals', 'deliverables', 'verdicts', 'closures', 'acceptance')})
        print('numbers:', atelier.numbers()); return 0
    return 1


if __name__ == '__main__':
    sys.exit(main())
