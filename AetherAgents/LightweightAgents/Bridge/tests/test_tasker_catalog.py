"""Verify the expanded seeds through unchanged Tasker tools using scripted clients.

Never contact a provider, open resident homes, or claim specialist competence from these checks.
"""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from aether.tasker import identity, staff, verification
from aether.tasker.briefcase import create_briefcases
from aether.tasker.notices import task_wake, message_wake, closure_wake
from aether.tasker.sprint import new_sprint
from aether.tasker.staff_turn import run_staff_turn
from test_tasker_support import micro

ROOT = Path(__file__).resolve().parents[4]
PRODUCT = staff.PRODUCT_STAFF
CATALOG = json.loads((PRODUCT / 'catalog.json').read_text(encoding='utf-8'))
ROWS = CATALOG['templates']


@pytest.fixture
def tmp_path(tmp_path_factory):
    # Keep the fixture root short enough for Windows journal/closure paths.
    return tmp_path_factory.mktemp('cat')


class ScriptedClient:
    def __init__(self, script):
        self.script = iter(script)
        self.seen = []

    def chat(self, messages, tools=None):
        self.seen.append(json.loads(json.dumps(messages)))
        step = next(self.script)
        if isinstance(step, str):
            return dict(role='assistant', content=step)
        name, args = step
        return dict(role='assistant', content='', tool_calls=[dict(
            id='call-' + str(len(self.seen)), type='function',
            function=dict(name=name, arguments=json.dumps(args)))])


def test_exact_catalog_coverage_and_unique_staff_ids():
    assert len(ROWS) == 40
    for key in ('id', 'staff_id', 'file'):
        assert len({r[key].casefold() for r in ROWS}) == 40
    assert {r['id'] for r in ROWS if r['category'] == 'Historical specialties'} == {
        'kirito', 'ada', 'euler', 'gauss', 'euclid', 'fibonacci', 'hypatia'}
    assert {r['id'] for r in ROWS if r['category'] == 'SVG animation specialties'} == {
        'svg-animator', 'svg-background-artist', 'cat-animator'}
    source = ROOT / 'AetherAgents/LightweightAgents/Bridge/aether/atelier/specialists/catalog.json'
    if source.exists():
        ids = {r['id'] for r in json.loads(source.read_text(encoding='utf-8'))['templates']}
        assert ids <= {r['id'] for r in ROWS}
        assert len(ids) == 29
    assert {p.name for p in PRODUCT.iterdir() if p.is_file()} == {r['file'] for r in ROWS} | {'catalog.json'}


@pytest.mark.parametrize('row', ROWS, ids=lambda r: r['id'])
def test_fresh_complete_identity_and_provenance(row):
    raw = (PRODUCT / row['file']).read_bytes()
    text = raw.decode('utf-8')
    assert hashlib.sha256(raw).hexdigest() == row['sha256']
    assert b'\r' not in raw and '[[' not in text
    assert identity.problems(text) == []
    assert set(identity.checksum_report(text).values()) == {'ok'}
    assert identity.reflection_entries(text) == []
    assert identity.name_and_role(text)[0].lower() == row['staff_id']
    for word in ('tasker_read', 'task_start', 'task_close', 'task_block', 'identity_close'):
        assert word in text
    assert 'No identity is copied into the sprint or briefcase.' in text
    for path, expected in row['sources'].items():
        source = ROOT / path
        if source.is_file():
            assert hashlib.sha256(source.read_bytes()).hexdigest() == expected


@pytest.mark.parametrize('row', ROWS, ids=lambda r: r['id'])
def test_seed_hire_task_message_closure_and_reseed_preserve_home(tmp_path, row):
    agent = row['staff_id']
    data = tmp_path / 'data'
    assert staff.seed_staff(data, PRODUCT, only=[agent]) == [agent]
    home = staff.home_file(data, agent)
    # Fixture-authored history proves that seeding and closure preserve existing memory.
    original = home.read_text(encoding='utf-8')
    original = identity.add_reflection(original, micro('fixture-prior', row['name'], 'synthetic prior lesson'))
    home.write_bytes(original.encode('utf-8'))
    assert staff.seed_staff(data, PRODUCT, only=[agent]) == []
    assert home.read_text(encoding='utf-8') == original
    contract = dict(version=1, review_required=False, files={'evidence.txt': {'json': {'value': 7}}}, evidence=[])
    sprint = new_sprint(data, 'Offline catalog protocol fixture', 'fixture-lead', contracts={'1': contract})
    staff.hire(sprint, [agent])
    create_briefcases(sprint, {agent: {}})
    sprint.tasker('operator').add('Produce evidence', 'Write evidence.txt only.', agent)
    client = ScriptedClient([
        ('tasker_read', {}), ('task_start', {'number': 1}),
        ('write_file', {'path': 'evidence.txt', 'text': '{"value":7}'}),
        ('task_close', {'number': 1, 'note': 'PASS: fixture file written'}), 'Done.'])
    result = run_staff_turn(sprint, agent, task_wake(sprint, agent, 1), client=client)
    assert result.ok, result.error
    assert sprint.tasker('operator').get(1).status == 'Closed'
    assert verification.evaluate(sprint, require_complete=False)['status'] == 'accepted'
    assert row['name'] in client.seen[0][0]['content']
    message = run_staff_turn(sprint, agent, message_wake(sprint, agent, 'Report your result'),
                             client=ScriptedClient(['The requested fixture file was written.']))
    assert message.ok
    s1 = '\n∆WMC{\nWORLD_MODEL := ⌜CATALOG_FIXTURE⌝\nCONTEXT: SPRINT_CLOSED\n[GOALS] := { Done }\n[NEXT_ACTION] := Wait\n}∆WMC\n'
    closing = run_staff_turn(sprint, agent, closure_wake(sprint, agent), client=ScriptedClient([
        ('identity_close', {'s1': s1, 's2_entry': micro('fixture-new', row['name'], 'synthetic new lesson')}), 'Closed.']))
    assert closing.ok, closing.error
    saved = staff.write_back(sprint, agent)
    assert saved.done, saved.lines
    current = home.read_text(encoding='utf-8')
    assert identity.system(current, 0) == identity.system(original, 0)
    assert identity.system(current, 1).replace('⟪TYPE::DYNAMIC_WORKSPACE_BLOCK⟫', '', 1).strip() == s1.strip()
    assert len(identity.reflection_entries(current)) == 2
    assert identity.reflection_entries(current)[1] == identity.reflection_entries(original)[0]
    assert staff.entry(data, agent)['state'] == 'available'
    assert len(staff.versions(data, agent)) == 2
    assert not list(sprint.root.rglob('Identity_*.txt'))
    assert staff.seed_staff(data, PRODUCT)  # Add remaining choices without replacing this home.
    assert len(staff.roster(data)) == len(ROWS) and home.read_text(encoding='utf-8') == current
    assert staff.write_back(sprint, agent).done
    assert home.read_text(encoding='utf-8') == current


def test_coordinators_are_staff_advisors_and_templates_do_not_grant_atelier_tools():
    for row in ROWS:
        if row['origin'] != 'fresh-role-adaptation':
            continue
        text = (PRODUCT / row['file']).read_text(encoding='utf-8')
        for old in ('memory_workspace', 'file_memory', 'project_checkpoint', 'technical_learning', 'Comm_Task'):
            assert old not in identity.system(text, 0)
        if row['id'] in {'kirito', 'task-coordinator'}:
            assert 'advice' in text.lower() or 'advise' in text.lower()
            assert 'Timer' in identity.asserts(text)['AGENT_METHOD']


def test_generator_reproduces_exact_product(tmp_path, monkeypatch):
    folder = ROOT / 'AetherAgents/LightweightAgents/Identities/Staff/_build'
    if not folder.is_dir():
        pytest.skip('Development generator intentionally excluded from public distribution')
    monkeypatch.syspath_prepend(str(folder))
    spec = importlib.util.spec_from_file_location('tasker_catalog_builder', folder / 'build_staff.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    builder.PRODUCT = tmp_path / 'product'
    builder.build_product()
    assert {p.name: p.read_bytes() for p in builder.PRODUCT.iterdir()} == {
        p.name: p.read_bytes() for p in PRODUCT.iterdir()}
