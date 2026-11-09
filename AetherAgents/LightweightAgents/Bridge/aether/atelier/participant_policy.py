"""Own participant tool and path limits; never grant permission from model text or perform file operations."""
from pathlib import Path


def permits(participant, name):
    if participant.technical() and name in ('technical_learning','project_checkpoint'): return True
    allowed=getattr(participant,'allowed_tools',None)
    return allowed is None or name in allowed


def resolve(participant, root, rel, mode):
    root=Path(root).resolve()
    path=(root/rel).resolve()
    if not path.is_relative_to(root):
        raise PermissionError('outside the commission root')
    allowed=[]
    for name,access in participant.roots.items():
        base=(root/name).resolve()
        if base.is_relative_to(root) and path.is_relative_to(base) and (mode=='read' or access=='write'):
            allowed.append(base)
    if participant.roots and not allowed:
        raise PermissionError(f'{rel} is not under a root you may {mode}.')
    return path
