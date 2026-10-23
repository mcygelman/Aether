import pytest

from memory_fabric import MemoryEngine
from memory_fabric.export import ExportError


def _agent(tmp_path, clock):
    eng = MemoryEngine.create(str(tmp_path / "grace.sqlite"), "grace", clock=clock)
    secret = eng.remember("Client price list", "SECRET-PRICE-TOKEN 1234", "project fact",
                          ["pricing"], proprietary=True)
    derived = eng.remember("Pricing insight", "Clients accept 10% uplift", "standing lesson",
                           ["pricing"], links=[(secret, "derived", 0.6)])
    general = eng.remember("Check contrast", "Keep contrast above 4.5:1", "standing lesson",
                           ["contrast"])
    eng.add_review("kirito", "overseer", "Solid.", 4)
    return eng, secret, derived, general


@pytest.mark.parametrize("include_flagged", [False, True])
def test_clean_export_refused_without_deleting_or_disclosing(tmp_path, clock, include_flagged):
    eng, secret, derived, general = _agent(tmp_path, clock)
    from memory_fabric.cartridge import fingerprint
    before = fingerprint(eng.cart.conn)
    destination = tmp_path / "share.sqlite"
    with pytest.raises(ExportError, match="preservation policy"):
        eng.export_clean(str(destination), include_flagged=include_flagged)
    assert not destination.exists()
    assert fingerprint(eng.cart.conn) == before
    assert eng.note(secret)["body"] == "SECRET-PRICE-TOKEN 1234"
    eng.close()


def test_export_refuses_to_overwrite(tmp_path, clock):
    eng, *_ = _agent(tmp_path, clock)
    with pytest.raises(ExportError):
        eng.export_clean(str(tmp_path / "grace.sqlite"))
    (tmp_path / "exists.sqlite").write_bytes(b"x")
    with pytest.raises(ExportError):
        eng.export_clean(str(tmp_path / "exists.sqlite"))
    eng.close()
