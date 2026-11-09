"""Own the explicit public GUI asset boundary and synchronous shell assembly.

Inputs: the packaged Bridge root. Outputs: MIME allowlist and navigation bytes.
Never: enumerate a directory to authorize files, accept caller-selected paths,
execute JavaScript, or implement application workflows.
"""
from pathlib import Path


JAVASCRIPT = (
    'tasker-project.js',
    'data-sources.js', 'chat-sources.js',
    'key-assignments.js', 'operator-roles.js', 'ui.js', 'kit.js', 'project-data.js', 'artifact-data.js',
    'sprints.js', 'teach.js', 'creations.js', 'staff.js', 'dashboards.js',
    'settings.js', 'help.js', 'help-data.js', 'specialist-catalog.js', 'specialist-create.js', 'chat-agents.js', 'technical-learning.js',
    'shell-theme.js', 'shell-help-data.js', 'shell-help.js', 'shell-readiness.js',
    'visuals.js', 'access.js', 'chat_files.js', 'chat-dashboard.js', 'chat-personal.js', 'recovery.js',
    'welcome.js',
)
ASSETS = {
    'tasker-project.css': 'text/css; charset=utf-8',
    'data-sources.css': 'text/css; charset=utf-8',
    **dict.fromkeys(JAVASCRIPT, 'application/javascript; charset=utf-8'),
    'ui.css': 'text/css; charset=utf-8',
    'chat.css': 'text/css; charset=utf-8',
    'chat-files.css': 'text/css; charset=utf-8',
    'chat-personal.css': 'text/css; charset=utf-8',
    'technical-learning.css': 'text/css; charset=utf-8',
    'specialist-catalog.css': 'text/css; charset=utf-8',
    'specialist-create.css': 'text/css; charset=utf-8',
    'recovery.css': 'text/css; charset=utf-8',
    'welcome.css': 'text/css; charset=utf-8',
    'theme.css': 'text/css; charset=utf-8',
    'lexicon.json': 'application/json; charset=utf-8',
}
SHELL_PARTS = ('shell-theme.js', 'shell-help-data.js', 'shell-help.js', 'shell-readiness.js')

# The editor has its own URL root and handler; these names are relative to its web folder.
PUBLIC_EDITOR_ASSETS = {
    '': ('public.html', 'text/html; charset=utf-8'),
    'css/studio.css': ('css/studio.css', 'text/css; charset=utf-8'),
    'js/studio.js': ('js/studio.js', 'text/javascript; charset=utf-8'),
    'config/studio-config.json': ('config/studio-config.json', 'application/json'),
    'css/public.css': ('css/public.css', 'text/css; charset=utf-8'),
    'js/public-editor.js': ('js/public-editor.js', 'text/javascript; charset=utf-8'),
    'js/custom-templates.js': ('js/custom-templates.js', 'text/javascript; charset=utf-8'),
}


def navigation_script(bridge_root: Path) -> bytes:
    """Keep existing pages' immediate aetherNav/aetherHelp access synchronous.

    Each part owns its own factory/data; concatenation only preserves the legacy
    script endpoint and load order. Workspace area modules use native imports.
    """
    asset_root = bridge_root / 'aether_bridge' / 'web' / 'workspace_assets'
    parts = [(asset_root / name).read_bytes() for name in SHELL_PARTS]
    parts.append((bridge_root / 'atelier_nav.js').read_bytes())
    return b'\n;\n'.join(parts)
