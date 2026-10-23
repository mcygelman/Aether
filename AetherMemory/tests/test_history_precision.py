import random
import json
import pytest
from memory_fabric import MemoryEngine

def memory_fabric():
    import memory_fabric as fabric
    return fabric

def test_d1_quote_roundtrips_representative_finite_values():
    engine=MemoryEngine.create(':memory:', 'review')
    conn=engine.cart.conn
    values=[0.30000000000000004,1/3,1e-300,1e300,0.9999999999999999]
    rng=random.Random(1008)
    values += [rng.random() for _ in range(5000)]
    bad=[]
    for value in values:
        quoted=conn.execute('select memory_real_text(?)',(value,)).fetchone()[0]
        if float(quoted)!=value:bad.append((value,quoted))
    engine.close()
    assert not bad,bad[:5]

def test_d1_engine_reconstruct_and_restore_exact_strength():
    from memory_fabric import history
    engine=memory_fabric().MemoryEngine.create(':memory:','review')
    value=.9999999999999999
    try:
        a=engine.remember('A','a','lesson'); b=engine.remember('B','b','lesson')
        before=engine.revision
        engine.link(a,b,strength=value); linked=engine.revision
        assert history.reconstruct(engine.cart,before)['links']==[]
        def change(action,**kw):
            return engine.tools().dispatch('memory_maintain',dict(action=action,
                expected_revision=engine.revision,reason='review',**kw))
        change('unlink',id=a,to_id=b)
        retained=history.reconstruct(engine.cart,linked)['links'][0]['strength']
        change('restore',id=a,at_revision=linked)
        restored=engine.cart.conn.execute('select strength from links').fetchone()[0]
        assert (retained,restored)==(value,value),f'{value!r} -> retained {retained!r}, restored {restored!r}'
    finally:engine.close()


def test_recall_with_a_huge_unbroken_token_does_not_crash(tmp_path):
    """10-08 confirmation: a ~100K-character token used to exceed SQLite's LIKE pattern limit."""
    from memory_fabric import MemoryEngine
    with MemoryEngine.create(str(tmp_path / "m.sqlite"), "owner", name="T") as mind:
        mind.remember("Deploy script needs the venv", "Activate it first.", "lesson", ["deploy script"])
        assert mind.recall(["x" * 100_000]).headers == []
        assert [h.title for h in mind.recall(["deploy"]).headers] == ["Deploy script needs the venv"]


def test_t00_r3_huge_word_fallback_still_finds_the_useful_word(tmp_path):
    with MemoryEngine.create(str(tmp_path / "memory.sqlite"), "owner") as mind:
        note = mind.remember("Deployment lesson", "Use the venv.", "lesson", ["deploy script"])
        result = mind.recall(["x" * 100_000 + " deploy"])
        assert result.ids() == [note]
        assert result.headers[0].via.startswith("word:")


@pytest.mark.parametrize("word_fallback", [False, True])
def test_t00_r3_long_tag_matches_without_sqlite_pattern_limits(tmp_path, word_fallback):
    with MemoryEngine.create(str(tmp_path / "memory.sqlite"), "owner") as mind:
        note = mind.remember("Long tag", "Retain legitimate matches.", "lesson", ["x" * 100_000])
        key = "x" * 60_000
        query = key + " absentword" if word_fallback else key
        result = mind.recall([query], budget_tokens=100_000)
        assert result.ids() == [note]
        assert result.headers[0].via.startswith("word:" if word_fallback else "phrase:")


def test_t00_r3_literal_search_preserves_compacted_phrase_semantics(tmp_path):
    with MemoryEngine.create(str(tmp_path / "memory.sqlite"), "owner") as mind:
        note = mind.remember("Local model", "Known setup.", "lesson", ["LMStudio loaded state"])
        mind.remember("Unrelated", "Other setup.", "lesson", ["deployment"])
        assert mind.recall(["LM-STUDIO"]).ids() == [note]
