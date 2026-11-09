"""Persist terminal failures and typed exit outcomes; never resume work, change task state or author mind content."""
from ._files import file_lock, read_json, write_json

class Outcome(str):
    def __new__(cls, text, exit_code=0):
        value=super().__new__(cls,text);value.exit_code=exit_code;return value

def stop(sprint,kind,detail,**context):
    folder=sprint.root/'timer';folder.mkdir(exist_ok=True)
    with file_lock(folder/'recovery.lock'):
        path=folder/'RECOVERY_STOP.json'
        if not path.exists():write_json(path,dict(kind=kind,detail=detail,**context))
        (folder/'STOP').touch(exist_ok=True)

def failure(sprint):
    path=sprint.root/'timer/RECOVERY_STOP.json'
    if not path.exists():return None
    try:
        value=read_json(path)
        if not isinstance(value,dict) or not all(isinstance(value.get(k),str) and value[k] for k in ('kind','detail')):
            raise ValueError('invalid terminal receipt')
        return Outcome('incomplete: '+value['kind']+': '+value['detail'],2)
    except (OSError,ValueError):
        return Outcome('incomplete: terminal receipt unreadable; preserve evidence',2)
