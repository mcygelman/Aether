"""Fresh path metadata must still refuse linked ancestors and unreadable paths."""
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from aether_bridge.project_setup import checked_path


def test_missing_descendants_are_allowed_but_relative_paths_are_not(tmp_path):
    target = tmp_path/'not-created'/'leaf'
    assert checked_path(target) == target.resolve()
    assert not target.exists()
    with pytest.raises(ValueError, match='absolute'):
        checked_path('relative/leaf')


@pytest.mark.parametrize('mode,attributes', [(stat.S_IFLNK, 0), (stat.S_IFDIR, 0x400)])
def test_link_metadata_refused_without_following_target(tmp_path, monkeypatch, mode, attributes):
    target = tmp_path/'link'; original = Path.lstat
    def metadata(path):
        if path == target:
            return SimpleNamespace(st_mode=mode, st_file_attributes=attributes)
        return original(path)
    monkeypatch.setattr(Path, 'lstat', metadata)
    with pytest.raises(ValueError, match='Linked'):
        checked_path(target/'missing')


def test_unreadable_metadata_fails_closed(tmp_path, monkeypatch):
    target = tmp_path/'private'; original = Path.lstat
    def metadata(path):
        if path == target: raise PermissionError('Metadata denied')
        return original(path)
    monkeypatch.setattr(Path, 'lstat', metadata)
    with pytest.raises(PermissionError, match='Metadata denied'):
        checked_path(target/'missing')


def test_actual_junction_ancestor_rejected_for_missing_child(tmp_path):
    import _winapi
    target = tmp_path/'outside'; target.mkdir()
    marker = target/'keep.txt'; marker.write_text('untouched')
    link = tmp_path/'junction'
    _winapi.CreateJunction(str(target), str(link))
    try:
        with pytest.raises(ValueError, match='Linked'):
            checked_path(link/'not-created'/'leaf')
        assert marker.read_text() == 'untouched'
        assert list(target.iterdir()) == [marker]
    finally:
        link.rmdir()
