"""Own operator controls for agent availability and the shared native folder picker.

Never modify agent homes or select a folder without the operator's action.
"""
import os
import threading
from urllib.parse import urlparse

from aether.integrity.http_body import json_body
from aether.paths import WEB_DIR

API = '/api/agent-availability'
_picker_lock = threading.Lock()


def choose_folder(title='Choose a folder that holds agents (nothing in it is changed)'):
    if os.name != 'nt':
        raise ValueError('Enter the agent folder path on this platform.')
    if not _picker_lock.acquire(blocking=False):
        raise ValueError('A folder chooser is already open.')
    try:
        import ctypes
        from ctypes import wintypes
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HWND, wintypes.UINT,
                                          wintypes.LPARAM, wintypes.LPARAM)
        class BrowseInfo(ctypes.Structure):
            _fields_ = [('owner', wintypes.HWND), ('root', ctypes.c_void_p), ('display', wintypes.LPWSTR),
                        ('title', wintypes.LPCWSTR), ('flags', wintypes.UINT), ('callback', callback_type),
                        ('param', wintypes.LPARAM), ('image', ctypes.c_int)]
        shell, ole = ctypes.windll.shell32, ctypes.windll.ole32
        user = ctypes.windll.user32
        user.GetForegroundWindow.restype = wintypes.HWND
        user.SetForegroundWindow.argtypes = [wintypes.HWND]
        user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, wintypes.UINT]
        owner = user.GetForegroundWindow()

        @callback_type
        def bring_forward(window, message, param, data):
            if message == 1:  # BFFM_INITIALIZED: do not leave the chooser behind the browser.
                flags = 0x01 | 0x02 | 0x40  # NOSIZE, NOMOVE, SHOWWINDOW
                user.SetWindowPos(window, wintypes.HWND(-1), 0, 0, 0, 0, flags)
                user.SetForegroundWindow(window)
                user.SetWindowPos(window, wintypes.HWND(-2), 0, 0, 0, 0, flags)
            return 0
        shell.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BrowseInfo)]
        shell.SHBrowseForFolderW.restype = ctypes.c_void_p
        shell.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]
        ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        ole.CoInitialize(None)
        pidl = None
        try:
            display = ctypes.create_unicode_buffer(260)
            info = BrowseInfo(owner, None, ctypes.cast(display, wintypes.LPWSTR),
                              title, 0x51, bring_forward, 0, 0)
            pidl = shell.SHBrowseForFolderW(ctypes.byref(info))
            if not pidl:
                return ''
            path = ctypes.create_unicode_buffer(260)
            if not shell.SHGetPathFromIDListW(pidl, path):
                raise ValueError('Enter the folder path manually.')
            return path.value
        finally:
            if pidl:
                ole.CoTaskMemFree(pidl)
            ole.CoUninitialize()
    finally:
        _picker_lock.release()


def handle_agent_registry(handler, method, state):
    path = urlparse(handler.path).path
    if path != '/agents' and path != API and not path.startswith(API + '/'):
        return False
    origin = f'http://127.0.0.1:{handler.server.server_port}'
    if handler.headers.get('Host') != origin.removeprefix('http://') or (method != 'GET' and handler.headers.get('Origin') != origin):
        handler.settings_send(dict(error='Use this local Aether page.'), 403)
        return True
    registry = state['agent_registry']
    try:
        if method == 'GET' and path == '/agents':
            handler.settings_send((WEB_DIR / 'agent_availability.html').read_bytes(), html=True)
        elif method == 'GET' and path == API:
            handler.settings_send(registry.listing())
        elif method == 'POST' and path in {API + '/' + a for a in ('preview', 'confirm', 'cancel-eject', 'choose-folder', 'starter-team', 'folders')}:
            body = json_body(handler, 32768, 'Send the agent action as JSON.')
            if not isinstance(body, dict):
                raise ValueError('Send an object.')
            action = path.rsplit('/', 1)[-1]
            if action == 'choose-folder':
                value = dict(path=choose_folder())
            elif action == 'folders':
                value = registry.set_folders(body.get('folders'))
            elif action == 'starter-team':
                from .atelier_http import root_of
                from .starter_team import create_starter_team
                value = create_starter_team(registry, root_of(state), state['model_settings'])
            elif action == 'preview':
                value = registry.preview(body.get('action'), body.get('value'))
            elif action == 'confirm':
                value = registry.confirm(body.get('review'), body.get('token'))
            else:
                value = registry.cancel_eject(body.get('id'))
            handler.settings_send(value)
        else:
            handler.settings_send(dict(error='Not found'), 404)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        from aether.keys.redaction import redact
        handler.settings_send(dict(error=redact(str(exc))), 409)
    return True
