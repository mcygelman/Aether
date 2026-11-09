"""Interrupt a real place mid-turn and restart it (v0.1 recovery evidence).

The place runs as a separate process (`tools/atelier_place.py serve --once`) against a scripted model provider served
over HTTP by this process, through the production adapter. The environment names a fault point (aether/atelier/faults.py)
and the child dies there with no cleanup, as a killed task would. The place is then started again without the fault,
and the state before and after is recorded: daybook rows and notes, outbox and sent, inbox read flags, the place
record, the provider's count of model calls, and the files under the commission root.

    py tools/v01_recovery/interrupted_place.py --out <evidence dir>            # every fault point, one scratch folder each
    py tools/v01_recovery/interrupted_place.py --point after_send --out <dir>

No live provider, no resident home: a disposable mind authored under a scratch folder, removed afterwards unless --keep.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BRIDGE))

from aether.atelier import Atelier, Budget, Participant          # noqa: E402
from aether.atelier.faults import EXIT_CODE, POINTS                # noqa: E402
from aether.mailbox import Mailbox                                 # noqa: E402
import tests.journey_provider as jp                                # noqa: E402

OPERATOR = dict(id='operator', kind='operator', name='Operator')
LETTER = 'Write outputs/result.md saying hello, then tell the room which file is the deliverable. Budget: 10 model calls.'
CASES = ('none',) + tuple(p for p in POINTS if p != 'after_closure_filed')   # closure filing is covered in-process


def _tool(call_id, name, **arguments):
    return dict(role='assistant', content='', tool_calls=[dict(id=call_id, type='function',
                                                             function=dict(name=name, arguments=json.dumps(arguments)))])


def turn_script():
    """One turn: write the file, then the note that names it."""
    return [_tool('w1', 'write_file', path='outputs/result.md', content='hello\n'),
            dict(role='assistant', content='Done. The deliverable is `outputs/result.md`.',
                 _usage=dict(prompt_tokens=120, completion_tokens=30, total_tokens=150))]


def snapshot(share: Path, home: Path, cid: str, provider) -> dict:
    atelier = Atelier(share, cid)
    rows = atelier.daybook.read()
    box = Mailbox(share, dict(id='reader', kind='member'))
    inbox = box.inbox(unread_only=False, commission=cid)
    record = json.loads((share / 'places' / 'probe.json').read_text(encoding='utf-8')) if (share / 'places' / 'probe.json').exists() else {}
    work = share / 'work' / cid
    return dict(
        daybook_rows=len(rows),
        reader_notes=sum(1 for r in rows if r['kind'] == 'message' and r['sender']['id'] == 'reader'),
        interrupted_records=sum(1 for r in rows if (r.get('facts') or {}).get('interrupted')),
        host_rows=sum(1 for r in rows if r['sender']['kind'] == 'host'),
        recorded_model_calls=atelier.cost()['model_calls'],
        outbox=len(list((box.root / 'outbox').glob('*.json'))), sent=len(list((box.root / 'sent').glob('*.json'))),
        inbox_unread=sum(1 for n in inbox if not n.get('read')), inbox_total=len(inbox),
        place_working=record.get('working'), place_stopped=bool(record.get('stopped')),
        provider_posts=len(provider.posts()), provider_remaining=provider.remaining(),
        files=sorted(p.relative_to(work).as_posix() for p in work.rglob('*') if p.is_file()) if work.exists() else [],
        daybook_chain=atelier.daybook.verify()['ok'],
    )


def serve_once(share: Path, home: Path, profiles: Path, *, fault: str | None, python: str, log: Path) -> int:
    env = dict(os.environ, PYTHONPATH=str(BRIDGE), PYTHONIOENCODING='utf-8')
    env.pop('AETHER_ATELIER_FAULT', None)
    if fault:
        env['AETHER_ATELIER_FAULT'] = fault
    command = [python, '-X', 'utf8', str(BRIDGE / 'tools' / 'atelier_place.py'), 'serve', '--share', str(share), '--homes', str(home),
               '--profiles', str(profiles), '--once', '--place', 'probe', '--interval', '0.1']
    with log.open('ab') as sink:
        sink.write(f'\n$ {" ".join(command)}  (fault={fault})\n'.encode('utf-8'))
        sink.flush()
        proc = subprocess.run(command, cwd=str(BRIDGE), env=env, stdout=sink, stderr=subprocess.STDOUT, timeout=180)
    return proc.returncode


def run_case(point: str, root: Path, *, python: str = sys.executable) -> dict:
    """Interrupt at `point` ('none' for an uninterrupted baseline), restart, and report both states."""
    root = Path(root)
    share, home = root / 'share', root / 'home'
    jp.ROLE_MARKERS = (('reader', ('Reader',)),) + tuple(m for m in jp.ROLE_MARKERS if m[0] != 'reader')
    with jp.JourneyProvider() as provider:
        provider.arm('reader', turn_script() + turn_script())            # enough for a repeated turn, so a replay is visible
        Participant.author(home, id='reader', kind='member', name='Reader', system='You are Reader, a careful member.',
                           mailboxes=share, roots={'outputs': 'write'})
        profiles = root / 'profiles.json'
        profiles.write_text(json.dumps(dict(profiles=dict(research=dict(model=provider.model, endpoint=provider.endpoint,
                                                                         contextWindow=131072, maxOutputTokens=512, timeoutSeconds=20)))),
                            encoding='utf-8')
        cid = 'recovery-' + point.replace('_', '-')
        Atelier(share, cid, budget=Budget(model_calls=10)).open(OPERATOR, LETTER)
        log = root / 'place.log'
        report = dict(point=point, commission=cid, started=time.strftime('%Y-%m-%dT%H:%M:%S'))
        report['first_exit'] = serve_once(share, home, profiles, fault=None if point == 'none' else point, python=python, log=log)
        report['after_interruption'] = snapshot(share, home, cid, provider)
        report['restart_exit'] = serve_once(share, home, profiles, fault=None, python=python, log=log)
        report['after_restart'] = snapshot(share, home, cid, provider)
        report['unexpected_provider_requests'] = len(provider.unexpected)
        report['expected_first_exit'] = 0 if point == 'none' else EXIT_CODE
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--point', choices=CASES, help='one case; default: every case')
    ap.add_argument('--out', type=Path, required=True, help='evidence folder for the JSON reports and place logs')
    ap.add_argument('--keep', action='store_true', help='keep the scratch folders')
    ap.add_argument('--python', default=sys.executable)
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    results = []
    for point in ([a.point] if a.point else CASES):
        scratch = Path(tempfile.mkdtemp(prefix=f'v01rec_{point}_'))
        try:
            report = run_case(point, scratch, python=a.python)
            shutil.copy(scratch / 'place.log', a.out / f'{point}.place.log')
        finally:
            if not a.keep:
                shutil.rmtree(scratch, ignore_errors=True)
        (a.out / f'{point}.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
        ok = report['first_exit'] == report['expected_first_exit'] and report['restart_exit'] == 0
        print(f"{point:<20} first exit {report['first_exit']:>3} restart exit {report['restart_exit']:>2}  "
              f"notes {report['after_interruption']['reader_notes']}->{report['after_restart']['reader_notes']}  "
              f"model calls {report['after_interruption']['provider_posts']}->{report['after_restart']['provider_posts']}  "
              f"interrupted records {report['after_restart']['interrupted_records']}  {'ok' if ok else 'UNEXPECTED EXIT'}")
        results.append(report)
    (a.out / 'summary.json').write_text(json.dumps(results, indent=1), encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
