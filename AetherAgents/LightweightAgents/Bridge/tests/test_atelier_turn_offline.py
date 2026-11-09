"""Focused offline turn test for the atelier.

Demonstrates:
- One heavyweight lead and two staff members take turns in an atelier on a disposable fixture.
- JourneyProvider acts as the local scripted model provider over HTTP.
- Lead plans and requests roster; operator approves with "go".
- Member writes files via tool calls and delivers them; files are hashed in daybook.
- Reviewer writes test files, runs test verification, and posts PASS verdicts.
- Operator accepts; closures are filed into lead's cartridge & continuity, and members' staff homes.
- Restart re-reads the atelier daybook and rebuilds exact state.
- A second commission wakes the lead with its own recall of prior closures.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from aether.atelier import (
    Atelier,
    Budget,
    Daybook,
    Participant,
)
from aether.atelier.place import Place
from aether.atelier.postmaster import Postmaster
from aether.atelier.turn import run_turn
from aether.backends.model import LMStudio
from aether.mailbox import Mailbox
from aether.memory.fabric import memory_fabric
from aether.memory import session as session_memory
from tests.journey_provider import JourneyProvider

OPERATOR = dict(id='michel', kind='operator', name='Michel')
REPORTS_PY = """def daily_report(rows, start, end):
    return [r for r in rows if start <= r["at"] < end]
"""
TEST_REPORTS_PY = """from reports import daily_report

def test_daily_report_half_open():
    rows = [{"at": 10}, {"at": 20}, {"at": 30}]
    assert len(daily_report(rows, 10, 30)) == 2
"""


def test_atelier_turn_journey_offline(tmp_path, monkeypatch):
    # 1. Environment & storage setup
    import tests.journey_provider as jp
    monkeypatch.setattr(
        jp,
        'ROLE_MARKERS',
        (
            ('lead', ('Harbor Steward', 'lead')),
            ('guide', ('Harbor Guide',)),
            ('minder', ('Minder',)),
        )
        + jp.ROLE_MARKERS,
    )

    minds_dir = tmp_path / 'minds'
    monkeypatch.setenv(session_memory.ENV_MINDS, str(minds_dir))

    share = tmp_path / 'share'
    homes = tmp_path / 'homes'
    for d in (share, homes):
        d.mkdir(parents=True, exist_ok=True)

    lead = Participant.author(
        homes / 'harbor-steward',
        id='harbor-steward',
        kind='lead',
        name='Harbor Steward',
        system='You are Harbor Steward, lead.',
        mailboxes=share,
    )
    guide = Participant.author(
        homes / 'harbor-guide',
        id='Harbor Guide',
        kind='member',
        name='Harbor Guide',
        system='You are Harbor Guide, member.',
        mailboxes=share,
    )
    minder = Participant.author(
        homes / 'minder',
        id='Minder',
        kind='reviewer',
        name='Minder',
        system='You are Minder, reviewer.',
        mailboxes=share,
    )

    # 2. Start scripted JourneyProvider
    with JourneyProvider() as provider:
        client = LMStudio(
            endpoint=provider.endpoint, model=provider.model, timeout_secs=10
        )

        place = Place(share, [lead.home, guide.home, minder.home], client=client)
        atelier = Atelier(share, 'commission-alpha', budget=Budget(model_calls=20))
        work_dir = share / 'work' / 'commission-alpha'

        def run_mind_turn(mind):
            post = Postmaster(atelier, share)
            post.exchange(place.kinds(), place.names())
            box = Mailbox(share, mind.record())
            sent = run_turn(mind, atelier, box, client, work_dir, place=place.name)
            if sent:
                post.collect([mind.id])
            return sent

        # 3. Operator opens the atelier
        atelier.open(
            OPERATOR,
            'Deliver daily settlement report function for Harbor Ledger: half-open'
            ' UTC window, USD only, with tests. Budget: 20 model calls.',
        )

        # 4. Turn 1: Master (Lead) plans
        provider.arm(
            'lead',
            [{
                'content': (
                    'Here is the plan for this commission.\n'
                    'Sprint 1: implementation\n'
                    '- implement daily_report in reports.py (Harbor Guide)\n'
                    '- verify daily_report with pytest (Minder)\n'
                    'I will hire Harbor Guide as implementer and Minder as reviewer.'
                )
            }],
        )
        assert run_mind_turn(lead)
        facts = atelier.facts()
        assert len(facts['plan']['sprints']) == 1
        assert facts['plan']['sprints'][0]['number'] == 1
        assert len(facts['roster']) >= 2

        # Operator approves sprint 1
        atelier.post(OPERATOR, 'go')
        assert atelier.facts()['approvals'][0]['sprint'] == 1

        # 5. Turn 2: Assistant (Guide) implements and delivers reports.py
        provider.arm(
            'guide',
            [
                {
                    'content': '',
                    'tool_calls': [{
                        'id': 'tc-write-1',
                        'type': 'function',
                        'function': {
                            'name': 'write_file',
                            'arguments': json.dumps({
                                'path': 'reports.py',
                                'content': REPORTS_PY,
                            }),
                        },
                    }],
                },
                {
                    'content': (
                        'Implemented daily_report with half-open UTC window. The'
                        ' deliverable is `reports.py`.'
                    )
                },
            ],
        )
        assert run_mind_turn(guide)
        assert (work_dir / 'reports.py').is_file()
        assert (work_dir / 'reports.py').read_text(encoding='utf-8') == REPORTS_PY
        assert any(
            d['name'] == 'reports.py' for d in atelier.facts()['deliverables']
        )

        # 6. Turn 3: Critic (Minder) tests and reviews
        provider.arm(
            'minder',
            [
                {
                    'content': '',
                    'tool_calls': [{
                        'id': 'tc-write-2',
                        'type': 'function',
                        'function': {
                            'name': 'write_file',
                            'arguments': json.dumps({
                                'path': 'test_reports.py',
                                'content': TEST_REPORTS_PY,
                            }),
                        },
                    }],
                },
                {
                    'content': (
                        'Review of reports.py.\n'
                        'requirement 1: half-open window - PASS: interval [start,'
                        ' end) correctly handled.\n'
                        'requirement 2: automated test suite - PASS: tests cover'
                        ' boundaries.'
                    )
                },
            ],
        )
        assert run_mind_turn(minder)
        assert (work_dir / 'test_reports.py').is_file()
        verdicts = atelier.facts()['verdicts']
        assert len(verdicts) >= 2
        assert all(v['verdict'] == 'PASS' for v in verdicts)

        # 7. Execute tests in work_dir to prove deliverable validity
        proc = subprocess.run(
            [sys.executable, '-m', 'pytest', 'test_reports.py', '-q'],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f'Tests failed in work_dir:\n{proc.stderr}'

        # 8. Operator accepts the commission
        atelier.post(OPERATOR, 'Accepted. Outstanding work team.')
        assert atelier.facts()['acceptance'] is not None

        # 9. Closure filings into participant containers
        lead_res = lead.file_closure(
            'Delivered daily_report with half-open UTC window.',
            commission_id='commission-alpha',
            tags=['reports', 'window'],
        )
        assert (
            'Delivered daily_report'
            in (lead.home / 'workspace' / 'SESSION_CURRENT.txt').read_text(
                encoding='utf-8'
            )
        )

        guide_res = guide.file_closure(
            'Implemented daily_report. Carry: ensure half-open bounds.',
            commission_id='commission-alpha',
        )
        assert (
            'Implemented daily_report'
            in (guide.home / 'workspace' / 'SESSION_CURRENT.txt').read_text(
                encoding='utf-8'
            )
        )

        minder_res = minder.file_closure(
            'Reviewed and verified reports. Carry: test edge cases.',
            commission_id='commission-alpha',
        )
        assert (
            'Reviewed and verified'
            in (minder.home / 'workspace' / 'SESSION_CURRENT.txt').read_text(
                encoding='utf-8'
            )
        )

        atelier.closure(
            lead.record(), 'Delivered daily_report with half-open UTC window.'
        )
        atelier.filed(lead.record(), str(lead.home))
        atelier.closure(
            guide.record(),
            'Implemented daily_report. Carry: ensure half-open bounds.',
        )
        atelier.filed(guide.record(), str(guide.home))
        atelier.closure(
            minder.record(), 'Reviewed and verified reports. Carry: test edge cases.'
        )
        atelier.filed(minder.record(), str(minder.home))

        # 10. Verify Daybook integrity and derived state
        verify_report = atelier.daybook.verify()
        assert verify_report['ok'] is True
        nums = atelier.numbers()
        assert nums['refusals_per_hundred'] == 0.0
        assert nums['approvals_per_sprint'] == 1.0

        facts = atelier.facts()
        assert len(facts['closures']) == 3
        assert all(c['filed'] for c in facts['closures'])

        # 11. Restart: a new Atelier instance on same root sees identical state
        restart = Atelier(share, 'commission-alpha')
        assert restart.daybook.verify()['ok'] is True
        assert restart.facts() == facts

        # 12. Second commission: lead wakes to its own prior closures
        commission_b = Atelier(share, 'commission-beta')
        commission_b.open(
            OPERATOR,
            'Deliver weekly summary variant of daily_report. Budget: 15 calls.',
        )
        wake_b = commission_b.wake_context(lead.record())
        assert len(wake_b['prior_closures']) >= 1
        assert wake_b['prior_closures'][0]['campaign_id'] == 'commission-alpha'
        assert 'daily_report' in wake_b['prior_closures'][0]['text']
