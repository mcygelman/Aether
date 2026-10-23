"""The decision profile (2026-10-19): additive, topped up on open, no migration; slow decay only, visible in the fog."""
import sqlite3

from memory_fabric import MemoryEngine, kinds, schema
from memory_fabric.cartridge import fingerprint


def test_an_older_cartridge_receives_the_decision_profile_on_open(tmp_path, clock):
    path = str(tmp_path / 'm.sqlite')
    MemoryEngine.create(path, 'm', clock=clock).close()
    raw = sqlite3.connect(path)
    with raw:
        raw.execute("DELETE FROM profiles WHERE profile = 'decision'")            # as written by the engine before 10-19
        raw.execute("UPDATE meta SET value = ? WHERE key = 'seal'", (fingerprint(raw),))
    raw.close()
    with MemoryEngine.open(path, clock=clock) as eng:
        assert eng.open_report.status == 'sealed'
        profile = next(p for p in eng.profiles() if p['profile'] == 'decision')
        assert profile['importance'] >= eng.settings()['fog_importance_min']
        assert profile['slow_rate'] == profile['fast_rate']                      # no fast phase


def test_the_kind_words_and_a_long_life(tmp_path, clock):
    available = [p[0] for p in schema.DEFAULT_PROFILES]
    assert kinds.resolve('decision', available) == 'decision'
    assert kinds.resolve('ruling', available) == 'decision'
    path = str(tmp_path / 'd.sqlite')
    with MemoryEngine.create(path, 'd', clock=clock) as eng:
        nid = eng.remember('Operator decided: F2 stays as delivered', 'In their words: "F2 stays."', 'decision',
                           ['decision', 'f2'], written_by='operator')
        lesson = eng.remember('A scratch guess', 'gone soon', 'scratch', ['f2'])
    clock.advance(200)
    with MemoryEngine.open(path, clock=clock) as eng:
        strength = {r[0]: r[1] for r in eng.cart.conn.execute('SELECT note_id, strength FROM temp.note_state')}
        assert strength[nid] > 0.7 > strength[lesson]
