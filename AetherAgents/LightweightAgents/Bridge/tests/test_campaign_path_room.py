"""Lane F finding (2026-10-10): with the default managed location, a campaign's deepest closure record
exceeded Windows' 260-character limit and the closure died mid-run. Managed folders are now short,
and creation refuses a location too deep for the deepest record when long paths are disabled."""
import os
import uuid

import pytest

from aether.roadmaps import project_setup as ps


def test_managed_folder_is_short_and_derived_from_the_request():
    rid = str(uuid.uuid4())
    assert ps.managed_folder(rid) == 'c-' + uuid.UUID(rid).hex[:12]
    assert len(ps.managed_folder(rid)) == 14


@pytest.mark.skipif(os.name != 'nt', reason='the limit applies on Windows only')
def test_a_too_deep_location_is_refused_with_the_room_available(monkeypatch, tmp_path):
    monkeypatch.setattr(ps, 'windows_long_paths_enabled', lambda: False)
    deep = tmp_path / ('x' * 120)
    with pytest.raises(ValueError, match='Choose a folder path of at most 69 characters'):
        ps.require_path_room(deep)


@pytest.mark.skipif(os.name != 'nt', reason='the limit applies on Windows only')
def test_the_default_location_for_a_typical_user_fits(monkeypatch):
    monkeypatch.setattr(ps, 'windows_long_paths_enabled', lambda: False)
    typical = 'C:\\Users\\someone12\\AppData\\Local\\Aether\\Projects\\' + ps.managed_folder(str(uuid.uuid4()))
    assert ps.require_path_room(typical) == typical
