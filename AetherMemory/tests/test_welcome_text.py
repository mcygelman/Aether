"""The Welcome as a mind reads it: concise lines with complete coverage, continuity first, and seeds that stay Aether's."""
import hashlib
import pytest

from memory_fabric import MemoryEngine, lifecycle
from memory_fabric.dashboard import CATALOGS, CONTINUITY, PROVIDERS, layout, render_text
from memory_fabric.workspace import SEED_AUTHOR, SEED_BODIES


def _seed_notes(engine):
    return {r[0]: r for r in engine.cart.conn.execute(
        "SELECT title, written_by, body FROM notes WHERE source_ref LIKE 'aether:memory-foundations%' ORDER BY title")}


def test_seeds_are_written_by_aether_not_the_mind(engine):
    seeds = _seed_notes(engine)
    assert set(seeds) == {'Aether Ecosystem Guide', 'Aether Ecosystem TeachFrame'}
    assert {row[1] for row in seeds.values()} == {SEED_AUTHOR}
    # The mind's own lesson about the Guide is its own note, not a refresh of the Guide.
    own = engine.remember('Aether Ecosystem Guide: what it leaves out', 'My lesson about the guide.', 'standing_lesson')
    assert engine.cart.conn.execute('SELECT written_by FROM notes WHERE note_id=?', (own,)).fetchone()[0] == 'grace'
    assert len(_seed_notes(engine)) == 2


def test_a_misattributed_unedited_seed_is_given_back_to_aether_on_open(tmp_path, clock):
    path = str(tmp_path / 'mind.sqlite')
    with MemoryEngine.create(path, 'owner', clock=clock) as e:
        ids = {r[0]: r[1] for r in e.cart.conn.execute(
            "SELECT title, note_id FROM notes WHERE source_ref LIKE 'aether:memory-foundations%'")}
        guide, frame = ids['Aether Ecosystem Guide'], ids['Aether Ecosystem TeachFrame']
        with e.cart.transaction(operation='test', description='the first schema-4 engine filed the seeds as the owner'):
            e.cart.conn.execute("UPDATE notes SET written_by='owner' WHERE note_id IN (?,?)", (guide, frame))
        # The owner adapted the TeachFrame in their own words: that copy stays theirs.
        e.workspace('revise', dict(record_type='teachframes', record_id=frame,
                                   note={'body': SEED_BODIES['teachframes'] + '\n\nMy addition.'}))
    with MemoryEngine.open(path, read_only=True) as e:   # a preview repairs nothing
        assert e.cart.conn.execute('SELECT written_by FROM notes WHERE note_id=?', (guide,)).fetchone()[0] == 'owner'
    with MemoryEngine.open(path, clock=clock) as e:
        authors = dict(e.cart.conn.execute('SELECT note_id, written_by FROM notes WHERE note_id IN (?,?)', (guide, frame)))
        assert authors == {guide: SEED_AUTHOR, frame: 'owner'}


def test_builtin_layout_leads_with_continuity_then_catalogs(engine):
    keys = [key for key, _ in PROVIDERS]
    assert tuple(keys[:len(CONTINUITY)]) == CONTINUITY
    assert tuple(keys[len(CONTINUITY):]) == CATALOGS
    assert [e['provider_key'] for e in layout(engine.cart)['elements']] == keys


def test_empty_welcome_is_short_prose_not_json(engine):
    sid = engine.begin_session('first-morning')
    text = render_text(engine.dashboard(session_id=sid))
    lines = text.splitlines()
    assert lines[1].startswith('Previous closure: none')
    assert 'From your previous session' in lines[2]
    assert '{' not in text and '":' not in text            # prose, not the JSON projection
    assert 'Available Guides: 1 (Aether Ecosystem Guide [' in text
    assert 'ask memory_workspace list record_type=guides' in text
    assert 'Available Note Types: ' in text and 'core_knowledge' in text
    assert text.endswith('explains access.')
    assert len(text) < 1400          # was 4,444 characters as JSON for the same empty dashboard


def _close_with_carry(engine, carry):
    first = engine.begin_session('yesterday')
    nid = engine.remember('Closing words, yesterday', carry, 'project_fact')
    lifecycle.append(engine.cart, 'closed', first, source_system='test', source_event_id='close-1', verified=True,
                     closure_id='c-1', note_id=nid, evidence_ref='closure/c-1.json', evidence_sha256='a' * 64,
                     details={'carry_sha256': hashlib.sha256(carry.encode('utf-8')).hexdigest()},
                     summary='Session closed')
    return engine.begin_session('today', previous_session_id=first)


def test_carry_excerpt_and_catalogs_remain_visible(engine):
    carry = 'What I did. ' + 'Built the Welcome as lines. ' * 10 + '\nWhat my next self should carry. Read the carry first.'
    today = _close_with_carry(engine, carry)
    text = render_text(engine.dashboard(session_id=today))
    assert 'Previous closure: PASS at ' in text
    assert 'From your previous session (Known previous session, closed ' in text
    assert 'Built the Welcome as lines.' in text and 'Read the carry first.' in text
    assert '  What my next self should carry.' in text      # the carry's own lines, indented under its card
    assert 'Available Guides: 1' in text                       # within 600 tokens nothing is withheld
    assert render_text(engine.dashboard(session_id=today), budget_tokens=150) == text


def test_old_budget_cannot_hide_cards_or_shorten_existing_carry_excerpt(engine):
    carry = 'Line. ' * 400
    today = _close_with_carry(engine, carry)
    result = engine.dashboard(session_id=today)
    result['budget_tokens'] = 150  # an older projection/caller cannot drop information
    text = render_text(result, budget_tokens=150)
    assert 'Catalogs withheld' not in text
    assert carry[:800] in text
    assert 'excerpt 800/2400 characters' in text
    for _, title in PROVIDERS:
        assert title in text
    assert 'whole note' in text and 'refreshes this view' in text
    assert 'welcome_budget_tokens' not in engine.cart.settings()


@pytest.mark.parametrize('edit', [
    {'title': 'My renamed guide'}, {'tags': ['my-guide']}, {'source_ref': 'my:guide'},
    {'body': SEED_BODIES['guides'] + '\nMy experience'},
])
def test_seed_adaptations_keep_owner_across_reopens(tmp_path, edit):
    path = str(tmp_path / 'mind.sqlite')
    with MemoryEngine.create(path, 'owner') as e:
        gid = e.cart.conn.execute('SELECT guide_id FROM guides').fetchone()[0]
        args = dict(record_type='guides', record_id=gid, note=edit, operation_id='adapt')
        first = e.workspace('revise', args)
        assert e.workspace('revise', args) == first
        # Even putting the supplied words back must not erase the adaptation's history.
        if 'body' in edit:
            e.workspace('revise', dict(record_type='guides', record_id=gid, note={'body': SEED_BODIES['guides']}))
        rev = e.revision
    for _ in range(2):
        with MemoryEngine.open(path) as e:
            note = e.cart.conn.execute('SELECT * FROM notes WHERE note_id=?', (gid,)).fetchone()
            assert note['written_by'] == 'owner'
            assert e.revision == rev
            for key in ('title', 'source_ref'):
                if key in edit:
                    assert note[key] == edit[key]
            if 'tags' in edit:
                assert 'my-guide' in [r[0] for r in e.cart.conn.execute('SELECT tag FROM tags WHERE note_id=?', (gid,))]


def test_full_history_and_unknown_scaffold_evidence_survive_prose(engine):
    a = engine.begin_session('unclosed')
    b = engine.begin_session('today')
    result = engine.dashboard(session_id=b)
    text = render_text(result)
    assert 'UNKNOWN' in text and a in text
    assert 'sessions without a recorded close' in text and 'Full session history:' in text
    result['elements'].append(dict(name='external', title='External scaffold', status='unknown', value=None,
        uncertain_sources=[dict(title='External instructions', scaffold_id='s-external')]))
    text = render_text(result)
    assert 'External scaffold: unknown' in text and 's-external' in text


def test_all_returned_catalog_references_and_historic_carry_are_visible(engine):
    for n in range(10):
        engine.workspace('create', dict(record_type='projects', title=f'Project {n}', body='project', note_id=f'p{n}'))
    text = render_text(engine.dashboard())
    page = engine.workspace('list', dict(record_type='projects', limit=8))
    for record in page['records']:
        assert record['project_id'] in text
    assert 'offset=8 for next page' in text
    result = dict(title='Welcome', retrieved_at='today', elements=[dict(name='carry', title='Carry', status='ok',
        value=dict(title='Old carry', note_id='old-note', text='Historical words', at_revision=42))])
    text = render_text(result)
    assert 'old-note at revision 42 (memory_maintain reconstruct)' in text
