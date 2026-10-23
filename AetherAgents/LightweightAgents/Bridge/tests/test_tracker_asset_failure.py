"""Installed tracker asset failures stay actionable without disclosing host paths."""
from io import BytesIO
from types import SimpleNamespace

import pytest

from aether.roadmaps.tracker import tracker_http as http


def request(base):
    headers = {}
    handler = SimpleNamespace(path=base, server=SimpleNamespace(server_port=8765),
        headers={'Host': '127.0.0.1:8765'}, wfile=BytesIO(),
        send_header=lambda key, value: headers.update({key: value}), end_headers=lambda: None)
    handler.send_response = lambda status: setattr(handler, 'status', status)
    return handler, headers


@pytest.mark.parametrize('base', ['/', '/project-folders/Synthetic/tracker/'])
@pytest.mark.parametrize('failure', ['missing', 'permission'])
def test_unavailable_asset_returns_safe_service_error(tmp_path, monkeypatch, base, failure):
    root = tmp_path/'synthetic-install'; root.mkdir()
    monkeypatch.setattr(http, 'ROOT', root)
    if failure == 'permission':
        def refused(path):
            raise PermissionError(13, 'Synthetic access denied', str(path))
        monkeypatch.setattr(type(root), 'read_bytes', refused)
    handler, headers = request(base)
    assert http.handle_tracker(handler, 'GET', store=None, base=base)
    assert handler.status == 503
    raw = handler.wfile.getvalue()
    assert raw == b'{"error": "Tracker page assets are unavailable. Repair the application installation."}'
    assert str(root).encode() not in raw and b'synthetic-install' not in raw
    assert headers['Cache-Control'] == 'no-store'
    assert headers['X-Content-Type-Options'] == 'nosniff'


@pytest.mark.parametrize('base', ['/', '/project-folders/Synthetic/tracker/'])
def test_present_asset_keeps_served_bytes_and_headers(tmp_path, monkeypatch, base):
    raw = b'<!doctype html><title>Synthetic tracker</title>'
    (tmp_path/'index.html').write_bytes(raw)
    monkeypatch.setattr(http, 'ROOT', tmp_path)
    handler, headers = request(base)
    assert http.handle_tracker(handler, 'GET', store=None, base=base)
    assert handler.status == 200 and handler.wfile.getvalue() == raw
    assert headers['Content-Type'] == 'text/html; charset=utf-8'
    assert headers['Content-Length'] == str(len(raw))
