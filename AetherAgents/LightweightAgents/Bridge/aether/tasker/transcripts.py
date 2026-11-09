"""Keep full turn evidence in each mind's home, with only references in sprint records.

Never put loaded identities or authored memory updates into sprint transcripts/ or duplicate
closure arguments in its turn records. Preserve the whole conversation, with credential redaction.
"""
from pathlib import Path
from ._files import write_json
from . import staff as staff_home
from aether.keys.redaction import redact


def _home(sprint, mind):
    if mind == 'overseer':
        return sprint.data_root / 'minds' / sprint.overseer
    return sprint.data_root / 'staff' / mind


def _memory_arguments(cost, identity_path=None):
    cost['tools'] = [dict(c, arguments=dict(stored_in_home_transcript=True,
                                          **({'retry': (c.get('arguments') or {}).get('retry') is True}
                                             if c.get('name') == 'identity_close' and 'closure_outcome' in c else {})))
                     if c.get('name') in ('identity_close', 'give_closing_words') else c
                     for c in cost.get('tools') or []]
    if identity_path:
        cost['tools'] = [dict(c, result='Identity read; full result in home transcript.')
                         if c.get('name') == 'read_file' and
                         Path((c.get('arguments') or {}).get('path') or '.').resolve() == identity_path.resolve()
                         else c for c in cost['tools']]


def summary_cost(cost):
    result = {key: value for key, value in cost.items() if key != 'transcript'}
    _memory_arguments(result)
    return result


def save(sprint, number, mind, kind, cost):
    """Move the returned transcripts to home records and return their paths for the sprint receipt."""
    refs = []
    messages = cost.pop('transcript', None)
    if messages is not None:
        path = _home(sprint, mind) / 'transcripts' / sprint.id / f'{number:04d}.json'
        write_json(path, redact(dict(turn=number, mind=mind, kind=kind, messages=messages)))
        refs.append(str(path.resolve()))
    for index, consult in enumerate(cost.get('librarian') or []):
        nested = consult.get('cost') or {}
        messages = nested.pop('transcript', None)
        if messages is not None:
            agent = sprint.record().get('librarian_id') or 'librarian'
            path = sprint.data_root / 'minds' / agent / 'transcripts' / sprint.id / f'{number:04d}-{index:02d}.json'
            write_json(path, redact(dict(turn=number, mind=agent, kind='CONSULT', messages=messages)))
            refs.append(str(path.resolve()))
        _memory_arguments(nested)
    identity_path = staff_home.home_file(sprint.data_root, mind) if mind != 'overseer' else None
    _memory_arguments(cost, identity_path)
    return refs
