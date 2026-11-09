"""A bounded heavyweight Librarian turn, with private continuity and shared-source tools.

Never assigns staff, reads another mind's memory, or starts nested helpers. Only
an explicit build request offers knowledge_add; consults leave the corpus alone.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import uuid

from aether.integrity.sanctum import verify
from aether.keys.redaction import redact as redact_record
from aether.tasker._files import file_lock, write_json
from aether.tasker.model import ContextCapacityError, tool_loop
from aether.tasker.sprint import TurnResult
from aether.tasker.staff_turn import redact
from aether.tasker.tools import _f
from aether.tasker.transcripts import summary_cost
from . import continuity, memory, owner_memory
from .homes import create_heavyweight_home, home_path
from .knowledge import Sources
from .accounting import AccountedClient, failure_cost

MAX_ROUNDS = 8
S = {'type': 'string'}
SCHEMAS = [
    _f('search_sources', 'Search shared source metadata; empty query lists candidates.', {'query': S}, []),
    _f('read_source', 'Read a chosen source id, in pages of 8000 characters.',
       {'id': S, 'offset': {'type': 'integer', 'minimum': 0}}, ['id']),
    _f('recall_memory', 'Recall your personal retrieval lessons.', {'words': {'type': 'array', 'items': S}}, ['words']),
    _f('load_memory', 'Load selected personal memories.', {'ids': {'type': 'array', 'items': S}}, ['ids']),
    _f('give_closing_words', 'After managing your memory, publish exact carry from this consultation.',
       {'text': S, 'carry_note_id': S}, ['text']),
]
BUILD_SCHEMA = _f('knowledge_add', 'Select an existing source for knowledge.sqlite; exact text and provenance are retained.', {'id': S}, ['id'])


def run_librarian(sprint, question, *, build=False, client=None, stopped=None):
    meter = None
    if not isinstance(question, str) or not question.strip() or len(question) > 6000:
        return TurnResult(False, '', {}, 'ValueError: give the Librarian a bounded question (1–6000 characters)')
    agent = sprint.record().get('librarian_id') or 'librarian'
    session_ref = sprint.id + ':librarian:' + uuid.uuid4().hex
    try:
        home = home_path(sprint.data_root, agent)
        if not home.exists():
            try:
                create_heavyweight_home(sprint.data_root, agent, 'Librarian', deployment='librarian')
            except FileExistsError:
                pass
        if client is None:
            from .turn import build_client
            client = build_client(sprint, agent)
        with file_lock(home / 'logs/.librarian-turn.lock'):
            continuity.activate(home, session_ref)
            own_memory = owner_memory.OwnerMemory(home, session_ref, context_window=getattr(client, 'context_window', None))
            sources = Sources(sprint)
            selected, closure = [], []
            def call(name, args):
                try:
                    if name.startswith('memory_'):
                        return own_memory.call(name, args)
                    if name == 'search_sources':
                        return json.dumps(dict(sources=sources.search(args.get('query', '')), warnings=sources.warnings), ensure_ascii=False)
                    if name == 'read_source':
                        return json.dumps(sources.read(args['id'], args.get('offset', 0)), ensure_ascii=False)
                    if name == 'knowledge_add' and build:
                        result = sources.select(args['id'])
                        selected.append(result)
                        return json.dumps(result, ensure_ascii=False)
                    if name == 'recall_memory':
                        return memory.render_headers(memory.recall(home, args['words']))
                    if name == 'load_memory':
                        return json.dumps(memory.load(home, args['ids'], session=session_ref), ensure_ascii=False)
                    if name == 'give_closing_words':
                        result = continuity.close(home, session_ref, args['text'], args.get('carry_note_id'))
                        closure.append(result['note_id'])
                        return json.dumps(result, ensure_ascii=False)
                    return 'This tool is unavailable for this Librarian request.'
                except Exception as exc:
                    return redact(f'{type(exc).__name__}: {exc}')
            parts = [(home / 'identity/System_0.txt').read_text(encoding='utf-8'),
                     'INTEGRITY (advisory): ' + json.dumps(verify(home).to_dict()),
                     continuity.welcome(home, session_ref), owner_memory.GUIDANCE, memory.render_headers(memory.recall(home, memory.words(question))),
                     (Path(__file__).parent / 'directives/LIBRARIAN_DIRECTIVE.txt').read_text(encoding='utf-8'),
                     'REQUEST MODE: ' + ('BUILD KNOWLEDGE' if build else 'CONSULT ONLY')]
            messages = [dict(role='system', content='\n'.join(parts)), dict(role='user', content=question)]
            meter = AccountedClient(client)
            text, cost, calls = tool_loop(meter, messages, SCHEMAS + owner_memory.schemas() + ([BUILD_SCHEMA] if build else []), call,
                                          max_rounds=MAX_ROUNDS, stopped=stopped)
            cost.update(tools=calls, sources=list(sources.reads.values()), selected=selected, closure_notes=closure)
            receipt = sprint.root / 'overseer/librarian' / (session_ref.rsplit(':', 1)[1] + '.json')
            # The full conversation travels to the Timer's scrubbed transcript; do not also persist it here.
            receipt_cost = summary_cost(cost)
            write_json(receipt, redact_record(dict(session=session_ref, agent=agent, question=question, build=build,
                                                   text=text, cost=receipt_cost, closure_filed=bool(closure))))
            citations = '\n'.join(f"- [{r['id']}] {r['source']} (SHA256 {r['sha256']})" for r in sources.reads.values())
            response = text + '\n\nSources read:\n' + (citations or '(none; this answer has no retrieved source evidence)')
            if not closure:
                response += '\nLibrarian closure was not filed; its session remains open.'
            return TurnResult(True, response, cost)
    except ContextCapacityError as exc:
        return TurnResult(False, '', failure_cost(meter), redact(f'ContextCapacityError: {exc}'))
    except Exception as exc:
        return TurnResult(False, '', failure_cost(meter), redact(f'{type(exc).__name__}: {exc}'))
