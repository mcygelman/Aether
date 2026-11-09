"""Expose a heavyweight's own Memory Fabric tools without interpreting its judgment.

Never select lessons, edit identities, touch another cartridge, or change the run
loop. Reads stay read-only; writes use the engine's owner binding and receipts.
"""
from copy import deepcopy
from functools import lru_cache
import hashlib
import json

from aether.memory.fabric import memory_fabric
from aether.memory.foundations import begin, current_session
from . import memory

GUIDANCE = (
    'You manage your own Memory Fabric. Use memory_workspace(action="dashboard") or '
    'action="schema", memory_discover, memory_maintain and memory_query to inspect it. '
    'Choose which memories to write, revise, link, bury or raise, and their titles, kinds, '
    'importance and useful lifetimes. Use memory_remember, memory_link and memory_settings '
    'as needed. No fixed number of memories or compulsory reorganization is required. '
    'Inspect before revising; retain the returned revision. Reuse operation_id and exact '
    'arguments after a lost reply; use a new operation_id for an intentional new action. '
    'Identical requests without an operation_id replay within this session. Loading is '
    'an intentional refresh; inspection and dashboard reads do not refresh. '
    'At closure, finish your chosen memory work, then give_closing_words with your exact '
    'carry text. You may supply carry_note_id for your own already-filed note with that '
    'exact body. Otherwise only one verbatim carry record is stored. The host does not '
    'extract lessons, choose associations or merge similar memories from your prose.'
    ' Older onboarding examples that describe three headings and automatic extraction '
    'describe the previous adapter; they remain history, not the current filing behavior.'
)


@lru_cache(maxsize=1)
def _schemas():
    # Schema generation must not open a live cartridge for writing.
    with memory_fabric().MemoryEngine.create(':memory:', 'schema', seed_resources=False) as engine:
        return engine.tools().schemas()


def schemas():
    return deepcopy(_schemas())


class OwnerMemory:
    def __init__(self, home, session_ref, *, context_window=None):
        self.home, self.session_ref = home, session_ref
        self.context_window = context_window
        self.observed_revision = None

    def call(self, name, arguments):
        memory_fabric()
        from memory_fabric.tools import MemoryTools, ToolLimits
        from memory_fabric.maintenance import READ_ACTIONS
        from memory_fabric.workspace import READ_ACTIONS as WORKSPACE_READS
        if name not in {s['function']['name'] for s in _schemas()}:
            raise ValueError('Unknown owner memory tool')
        args = deepcopy(arguments)
        writing = name not in ('memory_recall', 'memory_discover', 'memory_preflight', 'memory_query')
        nested = None
        if name == 'memory_workspace':
            nested = json.loads(args.get('arguments_json', '{}'))
            if not isinstance(nested, dict):
                raise ValueError('arguments_json must encode an object')
            writing = args.get('action') not in WORKSPACE_READS
        elif name == 'memory_maintain':
            writing = args.get('action') not in READ_ACTIONS
        elif name == 'memory_settings':
            writing = args.get('value') is not None
        elif name == 'memory_load':
            writing = args.get('refresh', True)
        if not writing:
            args.pop('operation_id', None)  # read-only operations have no retry receipt
        if writing:
            intent = nested if nested is not None else args
            key = intent.get('operation_id')
            if key is None:
                key = hashlib.sha256(json.dumps([name, args], sort_keys=True).encode()).hexdigest()
            intent['operation_id'] = 'heavyweight:' + hashlib.sha256(
                (self.session_ref + '\0' + str(key)).encode()).hexdigest()
            if nested is not None:
                args['arguments_json'] = json.dumps(nested)
        opener = memory._open_write(self.home, self.session_ref) if writing else memory._open_read(self.home)
        with opener as engine:
            owner = memory.pointer(self.home)['owner']
            engine.bind_session(owner, self.session_ref)
            if writing:
                begin(engine, self.session_ref, source_system='tasker-heavyweight', source_ref=self.session_ref)
            else:
                engine.logical_session_id = current_session(engine, self.session_ref, source_system='tasker-heavyweight')
            limits = (ToolLimits.for_context(self.context_window) if self.context_window else ToolLimits())
            tools = MemoryTools(engine, limits)
            if self.observed_revision is not None:
                tools.observed_revision = self.observed_revision
            result = tools.run(name, args)
            self.observed_revision = tools.observed_revision
            return json.dumps(result, ensure_ascii=False)
