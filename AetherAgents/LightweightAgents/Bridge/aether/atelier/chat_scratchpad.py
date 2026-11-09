"""Own the server-selected scratchpad for one signed-in human account.

Never accept an owner/path from HTTP input, expose peer scratchpads, or write
agent memory. Files are retained across chats, restarts and account deletion.
"""
from pathlib import Path
import re
import uuid

from .chat_files import direct


class ChatScratchpad:
    def __init__(self, container, owner):
        if not isinstance(owner, str) or not re.fullmatch(r'[0-9a-f]{32}', owner):
            raise ValueError('A scratchpad requires a stable account owner.')
        self.root = Path(container).absolute() / owner

    def folder(self):
        # Check existing ancestors before creating anything; never follow a junction.
        ancestor = self.root
        while not ancestor.exists() and not ancestor.is_symlink():
            ancestor = ancestor.parent
        direct(ancestor, trusted_root=self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        direct(self.root, trusted_root=self.root)
        return dict(root=str(self.root), editable=False, retrieval=False,
                    revision=uuid.uuid4().hex, kind='scratchpad')
