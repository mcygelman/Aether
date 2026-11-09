"""A turn: wake, think and act, post. Nothing else.

The mind wakes to its own identity and continuity, the commission's facts, the notes it has not read, the questions the
atelier has for it, and the budget left. It thinks with a model and acts with four tools inside the roots it was given
(read_file, write_file, list_dir, run_tests) and one memory tool (recall_memory). Then its reply is posted to the room
through its mailbox, with files attached by hash and its cost recorded. Never copy project context into a technical
specialist cartridge; project binding owns that boundary. A turn that fails is written down
by the place daemon as a failed turn and the mind is woken again. No preview, no confirm, no template: the reply is
the mind's own words, and the atelier's extractors make what they can of it.
"""
from __future__ import annotations

import re

import json
import math
from copy import deepcopy
import importlib.util
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from aether.mailbox import Mailbox

from .atelier import Atelier
from .participants import Participant
from . import faults
from .participant_policy import permits, resolve as resolve_participant_path

MAX_TOOL_ROUNDS = 40        # the budget is the cap; this only bounds one turn's loop
MAX_TOOL_RESULT = 24_000
# The context guard. Between turns the context is rebuilt from the daybook, so it is bounded;
# inside one turn up to 40 rounds of 24,000-character results were not. Before each model call the request is counted
# against the deployment's catalogued window less the output reserve and a margin. When the next round would not fit,
# results from earlier rounds (the mind has read them once) are set aside as stubs that say what and how long, the
# result just asked for is cut to what fits with a marker saying where to continue, and only when neither makes
# room does the turn end with the mind's note and what it would continue with next turn.
# Nothing refuses the mind a round; with no catalogued window the guard counts and does nothing.
CONTEXT_MARGIN = 0.20       # share of the window kept free beyond the output reserve (still a heuristic, not a tokenizer)
SET_ASIDE_KEEP = 300        # characters of a set-aside tool result that stay in the stub
RULES = """You are a mind in an atelier: a workshop where persistent AI minds work on a commission by writing notes to each
other, the way colleagues do. The operator opened the commission with a letter and will say "go" to approve a sprint and
"accepted" to accept the result. Say what you think in plain words. Address a note to one person by starting it with
"To: <name>"; otherwise it goes to the whole commission. When you deliver a file, write it with write_file and then say
which file is the deliverable, naming it in backticks. When you review, say PASS or FAIL per requirement, with why, and
record them with record_verdicts. When the operator has accepted and you are asked for your closing words, give them
with give_closing_words: what you did, what you learned, what your future self should carry, and what your next self
should skip. Only the operator accepts: until the wake says ACCEPTED: yes, nothing is accepted, so never write as if it
were and do not write closing words. When the work is ready and only the operator's ruling is missing, say so once (the
lead also calls mark_ready), then reply (nothing to add) until someone writes something new. THE ROOM IS WAITING ON, in
your wake, says whose move it is. Report a
test result only from a run_tests call you made; if tests could not be run, say exactly that. Nothing you write is
refused for its shape; the atelier asks you if it needs more.
A librarian keeps one collection: it answers "what do we have on X" in its own words, lends with shelf_lend (the item
lands in library/ under the commission) and says what it lent and what it left on the shelf, and files what minds hand
in with file_deposit, in its own words, or says why not with decline_deposit. Nothing is lent or filed silently.
A note to a person is a note: write it in your reply, not as a file. Files are for deliverables. Read with read_files
(several at once) rather than one read_file at a time; every call costs the commission's budget.
A decision is a ruling the operator gave; the wake shows the ones that bind here whole, under DECISIONS THAT BIND HERE.
Say so in the room if you disagree. Only the lead records the operator's words as a decision, with file_decision.
Your memory is yours: the wake shows headers of what your past selves filed, chosen by this commission's words; read
the ones that matter with load_memory (reading keeps a memory alive) and look further with recall_memory. Your side log
keeps one line per action you take (files written, tests run, memories read, notes posted), written for you; add a line
yourself with log_action when you decide something worth finding again, and look it up with recall_log. When you learn
something before the close, file it now with file_memory; it is yours from that moment. When something you remember
guides the work, say it comes from your memory; cite a project file only for what that file says."""


@dataclass
class Deployment:
    model: str
    endpoint: str
    api_key: str = field(default='', repr=False)
    max_tokens: int = 4096
    timeout_secs: int = 600
    temperature: float = 0.2
    dialect: str = 'auto'
    context_window: int | None = None
    thinking: str | None = None
    credential_ref: dict | None = field(default=None, repr=False)
    credential_bridge: Path | None = field(default=None, repr=False)

    @classmethod
    def load(cls, profiles_path: Path, name: str, *, bridge: Path | None = None) -> 'Deployment':
        """A deployment profile from runtime_profiles.json; the key is resolved from its credential reference and
        never shown."""
        raw = json.loads(Path(profiles_path).read_text(encoding='utf-8'))
        profiles = raw.get('profiles') or raw.get('document', {}).get('profiles') or {}
        return cls.from_profile(profiles[name], bridge=bridge)

    @classmethod
    def from_profile(cls, p: dict, *, bridge: Path | None = None) -> 'Deployment':
        """Build from one already-read profile snapshot, resolving credentials through the usual path."""
        key = ''
        reference = p.get('credential_ref')
        vault_reference = isinstance(reference, dict) and reference.get('kind') == 'vault'
        if reference and not vault_reference:
            from aether.keys.provider_credentials import resolve_reference
            key = resolve_reference(p['credential_ref'], bridge=bridge)
        return cls(model=p.get('requestedApiIdentifier') or p.get('model') or '', endpoint=p['endpoint'], api_key=key,
                   max_tokens=int(p.get('maxOutputTokens') or p.get('max_tokens') or 4096),
                   timeout_secs=int(p.get('timeoutSeconds') or p.get('timeoutSecs') or 600), temperature=float(p.get('temperature', 0.2)),
                   dialect=p.get('dialect') or 'auto', context_window=p.get('contextWindow'), thinking=p.get('thinking'),
                   credential_ref=deepcopy(reference) if vault_reference else None, credential_bridge=bridge)

    def client(self):
        """The production adapter, built the way the installed product builds it (dialect from the endpoint)."""
        from aether.backends.model import LMStudio, select_dialect
        kwargs = dict(endpoint=self.endpoint, model=self.model, timeout_secs=self.timeout_secs, api_key=self.api_key,
                      max_tokens=self.max_tokens, dialect=select_dialect(self.endpoint, self.dialect or 'auto'))
        if self.context_window:
            kwargs['context_window'] = int(self.context_window)
        if self.thinking:
            kwargs['thinking'] = self.thinking
        if self.credential_ref:
            kwargs.update(credential_ref=self.credential_ref, credential_bridge=self.credential_bridge)
        try:
            c = LMStudio(**kwargs)
        except TypeError:
            if self.credential_ref:
                raise  # Never downgrade a vault-backed client to an unauthenticated legacy client.
            c = LMStudio(endpoint=self.endpoint, model=self.model, timeout_secs=self.timeout_secs, api_key=self.api_key)
        try:
            c.temperature = self.temperature
        except Exception:
            pass
        return c


# -- tools -------------------------------------------------------------------------------------------------------

def tool_schemas(librarian: bool = False, lead: bool = False, technical: bool = False, reviewer: bool = False) -> list:
    def f(name, desc, props, req):
        return dict(type='function', function=dict(name=name, description=desc, parameters=dict(type='object', properties=props, required=req)))
    shelf = [f('catalog_search', 'Search your collection by words, optionally by kind or tags; returns catalog entries, not items.',
               dict(query=dict(type='string'), kind=dict(type='string'), tags=dict(type='array', items=dict(type='string'))), ['query']),
             f('shelf_lend', 'Lend an item: copy it into library/<item id>/ under the commission for the person named, and count it.',
               dict(item_id=dict(type='string'), to=dict(type='string')), ['item_id', 'to']),
             f('shelf_read', 'Read a file of an item on your shelf without lending it (to check a citation, write a summary, decide a filing).',
               dict(item_id=dict(type='string'), name=dict(type='string')), ['item_id']),
             f('list_deposits', 'What minds have handed in and you have not filed yet.', dict(), []),
             f('file_deposit', 'File a deposit as an item, in your own words.',
               dict(deposit_id=dict(type='string'), title=dict(type='string'), kind=dict(type='string'), summary=dict(type='string'),
                    tags=dict(type='array', items=dict(type='string')), item_id=dict(type='string')), ['deposit_id', 'title', 'summary']),
             f('decline_deposit', 'Decline a deposit, saying why; it is kept aside, not deleted.',
               dict(deposit_id=dict(type='string'), why=dict(type='string')), ['deposit_id', 'why'])] if librarian else []
    ruling = [f('file_decision', "Record the operator's ruling, in the operator's words, as a decision for every mind of this commission "
                "(it binds this project; 'standing' only when the operator's words say so). Accepting the work is not a ruling to record.",
                dict(words=dict(type='string'), subject=dict(type='string'), scope=dict(type='string', enum=['project', 'standing'])),
                ['words'])] if lead else []
    # The three signals that move a commission, said exactly. The host also reads them from prose; these leave no doubt
    # (runs a72-a92: every run met a new way of writing a verdict, and a finished room waited on words nobody could read).
    signals = ([f('record_verdicts', 'Record your verdict on each requirement, PASS or FAIL with why. Call it whenever you rule '
                  '(first review or re-review); the latest verdict on a requirement stands. Say them in your note too.',
                  dict(verdicts=dict(type='array', items=dict(type='object', properties=dict(
                      requirement=dict(type='string'), verdict=dict(type='string', enum=['PASS', 'FAIL']), why=dict(type='string')),
                      required=['requirement', 'verdict']))), ['verdicts'])] if reviewer else []) + \
              ([f('mark_ready', "Say the work is ready for the operator's ruling: call it once, when every requirement passes and "
                  'nothing is left, with what is done. Write a note as well.', dict(summary=dict(type='string')), ['summary'])] if lead else []) + \
              [f('give_closing_words', 'Your closing words, after the operator has accepted (the wake says ACCEPTED: yes): what you did, '
                 'what you learned, what your future self should carry, and what your next self should skip.',
                 dict(did=dict(type='string'), learned=dict(type='string'), carry=dict(type='string'), skip=dict(type='string')),
                 ['did', 'learned', 'carry'])]
    learning = [f('technical_learning','Your technical cartridge: direct filing, revision, paths, search and history. No approval needed. Use action=schema for fields. arguments_json is an object.',dict(action=dict(type='string'),arguments_json=dict(type='string')),['action']),
                f('project_checkpoint','Save unfinished work and project details with this project for restart recovery. Never copied into technical memory.',dict(text=dict(type='string')),['text'])] if technical else []
    return [f('read_file', 'Read a text file under the commission root.', dict(path=dict(type='string')), ['path']),
            f('write_file', 'Write a text file under one of your writable roots (creates folders).', dict(path=dict(type='string'), content=dict(type='string')), ['path', 'content']),
            f('list_dir', 'List files under a folder of the commission root.', dict(path=dict(type='string')), ['path']),
            f('read_files', 'Read several files at once (paths relative to the commission root). Cheaper than one read_file per file.', dict(paths=dict(type='array', items=dict(type='string'))), ['paths']),
            f('run_tests', 'Run the tests in a file or folder under the commission root (pytest when installed, otherwise unittest) and return the output.', dict(path=dict(type='string')), ['path']),
            f('recall_memory', 'Headers of your own memories by tags (title, kind, strength, one line). Looking changes nothing.', dict(tags=dict(type='array', items=dict(type='string'))), ['tags']),
            f('load_memory', 'Read the memories you choose by id; reading refreshes them.', dict(ids=dict(type='array', items=dict(type='string'))), ['ids']),
            f('memory_workspace', 'Projects, Scaffolds, Guides, TeachFrames, Threads and editable dashboards. '
              'Use action=schema for operations/fields; action=dashboard for Welcome. arguments_json encodes an object. '
              'Reuse operation_id in that object for edit retries. Reads never reinforce.',
              dict(action=dict(type='string'), arguments_json=dict(type='string')), ['action']),
            f('memory_query', 'Your own parameterized read-only SQLite SELECT/CTE; use memory_workspace(action=schema) '
              'for tables. parameters_json is an array or object; offset/limit page results.',
              dict(sql=dict(type='string'), parameters_json=dict(type='string'), offset=dict(type='integer'),
                   limit=dict(type='integer')), ['sql']),
            f('recall_log', 'Your side log: the actions you took, by words (empty for the newest).', dict(words=dict(type='array', items=dict(type='string'))), []),
            f('log_action', 'Add one line to your side log: a decision or a fact worth finding again, with a pointer and tags.',
              dict(line=dict(type='string'), pointer=dict(type='string'), tags=dict(type='array', items=dict(type='string'))), ['line']),
            f('file_memory', 'File a memory now, without waiting for the close: a lesson, a warning, a fact about this commission, or a to-do; one sentence as the title.',
              dict(kind=dict(type='string', enum=['lesson', 'warning', 'fact', 'todo']), title=dict(type='string'), body=dict(type='string'),
                   tags=dict(type='array', items=dict(type='string'))), ['kind', 'title'])] + shelf + ruling + signals + learning


class Tools:
    def __init__(self, participant: Participant, commission_root: Path, library=None, commission_id: str | None = None, delegation=None):
        self.p, self.root = participant, Path(commission_root).resolve()
        self.commission_id = commission_id or Path(commission_root).name
        if participant.technical():
            from .specialist_context import bind
            bind(participant,self.root,self.commission_id)
        self.written: dict = {}
        self.library = library                  # a Library when the mind is a librarian
        self.lent: list = []
        self.memory: dict = dict(recalled=[], loaded=[])   # the recall receipt: what the mind looked up and read this turn
        self.actions: list = []                           # the side log rows this turn (kind, pointer, tags, line, by)
        self.decisions: list = []                         # rulings the lead recorded this turn; filed into every mind when posted
        self.signals: dict = {}                           # verdicts, ready, closing words said with their tools; ride on the note
        self.delegation = delegation
        self.remaining_calls = 0
        self.persisted_actions = 0
        from aether.data_sources.service import agent_session
        self.data_sources = agent_session()

    def _resolve(self, rel: str, mode: str) -> Path:
        return resolve_participant_path(self.p,self.root,rel,mode)

    def call(self, name: str, args: dict) -> str:
        try:
            if not permits(self.p,name):
                return 'This participant is not allowed to use '+name+'.'
            if name == 'data_sources':
                if self.data_sources is None:
                    return 'No requesting operator data-source scope is available.'
                return json.dumps(self.data_sources.call(args.get('action'), json.loads(args.get('arguments_json', '{}'))), ensure_ascii=False)
            if self.p.technical() and name in ('technical_learning','project_checkpoint'):
                from . import specialist_learning, specialist_context
                result=(specialist_learning.call(self.p.home,args['action'],json.loads(args.get('arguments_json','{}')))
                        if name=='technical_learning' else specialist_context.checkpoint(self.p,args['text'],self.commission_id))
                return json.dumps(result,ensure_ascii=False)
            if name == 'delegate_ultralight':
                if self.delegation is None:
                    return 'Ultralight delegation is unavailable. Configure a model connection named ultralight.'
                result = self.delegation.call(args, remaining_calls=self.remaining_calls)
                receipt = self.delegation.receipts[-1]
                self.actions.append(dict(kind='ultralight', pointer=receipt['id'], tags=['delegation'],
                    line=f"{receipt['role']}: {receipt['status']}; {receipt['model_calls']} model call(s); parent review required"))
                return result
            if name in ('memory_workspace', 'memory_query'):
                from .mind_memory import workspace_call
                if not self.p.cartridge():
                    return 'Memory cartridge unavailable for this participant.'
                from .specialist_context import home as project_home
                memory_home=project_home(self.p,self.commission_id) if self.p.technical() else self.p.home
                if name == 'memory_query':
                    result = workspace_call(memory_home, self.commission_id, 'query', dict(sql=args['sql'],
                        parameters=json.loads(args.get('parameters_json', '[]')), offset=args.get('offset', 0),
                        limit=args.get('limit', 50)))
                else:
                    result = workspace_call(memory_home, self.commission_id, args['action'],
                                            json.loads(args.get('arguments_json', '{}')))
                return json.dumps(result, ensure_ascii=False)
            if name == 'read_file':
                return self._resolve(args['path'], 'read').read_text(encoding='utf-8', errors='replace')[:MAX_TOOL_RESULT]
            if name == 'list_dir':
                base = self._resolve(args.get('path') or '.', 'read')
                return '\n'.join(str(p.relative_to(self.root)) for p in sorted(base.rglob('*')) if p.is_file())[:MAX_TOOL_RESULT] or '(empty)'
            if name == 'write_file':
                path = self._resolve(args['path'], 'write'); path.parent.mkdir(parents=True, exist_ok=True)
                data = str(args.get('content', '')).encode('utf-8'); path.write_bytes(data)
                rel = str(path.relative_to(self.root)).replace('\\', '/')
                self.written[rel] = data
                import hashlib
                self.actions.append(dict(kind='wrote', pointer='sha256:' + hashlib.sha256(data).hexdigest()[:16], tags=[rel.rsplit('/', 1)[-1], 'file'],
                                         line=f'wrote {rel} ({len(data)} bytes)'))
                return f'wrote {len(data)} bytes to {args["path"]}'
            if name == 'read_files':
                out = []
                for rel in (args.get('paths') or [])[:40]:
                    out.append(f'=== {rel} ===\n' + self.call('read_file', dict(path=rel)))
                return '\n'.join(out) or 'No paths given.'
            if name == 'run_tests':
                target = self._resolve(args['path'], 'read')
                command, base, runner = test_command(target, self.root)
                proc = subprocess.run(command, cwd=str(base), env=test_environment(), capture_output=True, text=True, timeout=300)
                output = (proc.stdout + proc.stderr).strip()
                tail = (output.splitlines() or [''])[-1][:80]
                self.actions.append(dict(kind='ran', pointer=str(args['path']), tags=['tests', runner, 'pass' if proc.returncode == 0 else 'fail'],
                                         line=f'ran tests {args["path"]} with {runner}: {tail}'))
                where = base.relative_to(self.root).as_posix() if base != self.root else '.'
                return (f'[{runner} from {where}, exit code {proc.returncode}]\n' + output)[-MAX_TOOL_RESULT:]
            if name == 'recall_memory':
                heads = self.p.recall(list(args.get('tags') or []))[:8]
                self.memory['recalled'].append(dict(tags=list(args.get('tags') or []), ids=[h.get('id') for h in heads if h.get('id')]))
                return json.dumps(heads, ensure_ascii=False)[:MAX_TOOL_RESULT]
            if name == 'load_memory':
                notes = self.p.load_memory(list(args.get('ids') or []))
                self.memory['loaded'] += [dict(id=n.get('id'), title=(n.get('title') or '')[:120], kind=n.get('kind')) for n in notes if n.get('id')]
                for n in notes:
                    if n.get('id'):
                        self.actions.append(dict(kind='read-memory', pointer=n['id'], tags=[n.get('kind') or 'memory'], line=f"read memory: {(n.get('title') or '')[:100]}"))
                return json.dumps(notes, ensure_ascii=False)[:MAX_TOOL_RESULT]
            if name == 'recall_log':
                words = list(args.get('words') or [])
                rows = self.p.log_search(words) if words else self.p.log_recent(limit=10)
                return json.dumps([dict(at=r['at'], kind=r['kind'], line=r['line'], pointer=r['pointer'], tags=r['tags']) for r in rows], ensure_ascii=False)[:MAX_TOOL_RESULT] or '[]'
            if name == 'log_action':
                line = str(args.get('line') or '').strip()
                if not line:
                    return 'A log line needs words.'
                self.actions.append(dict(kind='noted', pointer=str(args.get('pointer') or ''), tags=list(args.get('tags') or []), line=line[:200], by='self'))
                return 'logged'
            if name == 'file_decision':
                if self.p.kind != 'lead':
                    from .mind_memory import ONLY_THEY_FILE
                    return ONLY_THEY_FILE
                said = str(args.get('words') or '').strip()
                if not said:
                    return "A decision needs the operator's words."
                self.decisions.append(dict(words=said[:600], subject=str(args.get('subject') or '').strip() or None,
                                           scope=str(args.get('scope') or 'project')))
                self.actions.append(dict(kind='recorded-decision', pointer='', tags=['decision'], line=f'recorded the operator: {said[:150]}'))
                return ('recorded: when your note is posted it is filed into every mind of this commission as "lead, recording the '
                        "operator\"; quote the operator's words in your note.")
            if name == 'record_verdicts':
                given = [dict(requirement=str(v.get('requirement') or '').strip()[:160], verdict=str(v.get('verdict') or '').strip().upper(),
                              why=str(v.get('why') or '').strip()[:300]) for v in (args.get('verdicts') or []) if isinstance(v, dict)]
                given = [v for v in given if v['requirement'] and v['verdict'] in ('PASS', 'FAIL')]
                if not given:
                    return 'Nothing recorded: give each requirement with PASS or FAIL.'
                self.signals.setdefault('verdicts', []).extend(given)
                self.actions.append(dict(kind='verdicts', pointer='', tags=['review'],
                                         line=f"recorded {len(given)} verdict(s): {sum(v['verdict'] == 'PASS' for v in given)} PASS, "
                                              f"{sum(v['verdict'] == 'FAIL' for v in given)} FAIL"))
                return f'recorded {len(given)} verdict(s); they are read when your note is posted.'
            if name == 'mark_ready':
                self.signals['ready'] = str(args.get('summary') or '').strip()[:600] or 'ready'
                self.actions.append(dict(kind='ready', pointer='', tags=['ready'], line='marked the work ready for the operator'))
                return "marked ready; the operator is told when your note is posted. Nothing is accepted until the operator says so."
            if name == 'give_closing_words':
                parts = [(k, str(args.get(k) or '').strip()) for k in ('did', 'learned', 'carry', 'skip')]
                self.signals['closing'] = {k: v[:1200] for k, v in parts if v}
                self.actions.append(dict(kind='closing', pointer='', tags=['closure'], line='gave closing words'))
                return 'recorded; they are filed into your home once your note is posted (and only after the operator has accepted).'
            if name == 'file_memory':
                filed = self.p.file_now(str(args.get('kind') or 'lesson'), str(args.get('title') or ''), str(args.get('body') or ''),
                                        list(args.get('tags') or []), commission_id=self.commission_id)
                if filed.get('id'):
                    self.actions.append(dict(kind='filed-memory', pointer=filed['id'], tags=[filed['kind']], line=f"filed {filed['kind']}: {filed['title'][:100]}"))
                return filed.get('text') or json.dumps(filed)
            if self.library is not None:
                if name == 'catalog_search':
                    hits = self.library.search(args.get('query', ''), kind=args.get('kind') or None, tags=args.get('tags') or (), limit=10)
                    return json.dumps([dict(id=h['id'], kind=h['kind'], title=h['title'], summary=h['summary'][:300], tags=h.get('tags', []),
                                            lends=h.get('lends', 0), files=[f['name'] for f in h.get('files', ())]) for h in hits],
                                      ensure_ascii=False)[:MAX_TOOL_RESULT] or '[]'
                if name == 'shelf_lend':
                    to = args.get('to') or 'the commission'
                    lent = self.library.lend(args['item_id'], dict(id=to, kind='member', name=to), self.root / 'library')
                    self.lent.append(args['item_id'])
                    # The operator can open the actual loan from the conversation, by its stored hash.
                    for item in lent['files']:
                        self.written[f"library/{args['item_id']}/{item['name']}"] = self.library.read(args['item_id'], item['name'])
                    self.actions.append(dict(kind='lent', pointer=args['item_id'], tags=['shelf', to], line=f"lent {args['item_id']} to {to}"))
                    return f"lent {args['item_id']} into library/{args['item_id']}/ for {to}: " + ', '.join(f['name'] for f in lent['files'])
                if name == 'shelf_read':
                    data = self.library.read(args['item_id'], args.get('name') or None)
                    return data.decode('utf-8', errors='replace')[:MAX_TOOL_RESULT]
                if name == 'list_deposits':
                    return json.dumps([dict(id=d['id'], from_=d['sender'].get('name'), note=d.get('note', '')[:300], files=[f['name'] for f in d['files']],
                                            commission=d.get('commission')) for d in self.library.deposits()], ensure_ascii=False)[:MAX_TOOL_RESULT] or '[]'
                if name == 'file_deposit':
                    e = self.library.file(args['deposit_id'], item_id=args.get('item_id'), title=args['title'], kind=args.get('kind') or 'note',
                                          summary=args.get('summary', ''), tags=args.get('tags') or (), by=self.p.record())
                    self.actions.append(dict(kind='filed', pointer=e['id'], tags=['shelf', e['kind']], line=f"filed deposit as {e['id']}: {e['title'][:80]}"))
                    return f"filed as {e['id']} ({e['kind']}): {e['title']}"
                if name == 'decline_deposit':
                    d = self.library.decline(args['deposit_id'], args.get('why', ''), by=self.p.record())
                    return f"declined {d['deposit']}: {d['why']}"
            return f'unknown tool {name}'
        except Exception as exc:
            return f'{type(exc).__name__}: {exc}'
        finally:
            if self.p.technical() and len(self.actions)>self.persisted_actions:
                self.p.log(self.actions[self.persisted_actions:],commission_id=self.commission_id)
                self.persisted_actions=len(self.actions)


UNITTEST = ("import sys, unittest; base, start, pattern = sys.argv[1:4]; sys.path.insert(0, base); "
            "unittest.main(module=None, argv=['unittest', 'discover', '-v', '-s', start, '-t', start, '-p', pattern])")


def test_command(target: Path, root: Path) -> tuple:
    """How to run a project's tests: from the project folder that holds them (the nearest folder above any tests
    folders), with pytest when this runtime has it and the standard library's unittest otherwise. The installed runtime
    ships without pytest and its path file keeps the working folder off sys.path, so the project folder is passed in."""
    root = Path(root).resolve()
    base = target if target.is_dir() else target.parent
    while base != root and base.name.lower().startswith('test'):
        base = base.parent
    if importlib.util.find_spec('pytest') is not None:
        return [sys.executable, '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--rootdir', str(base), str(target)], base, 'pytest'
    start, pattern = (str(target), 'test*.py') if target.is_dir() else (str(target.parent), target.name)
    return [sys.executable, '-B', '-c', UNITTEST, str(base), start, pattern], base, 'unittest'


TEST_ENVIRONMENT = ('SYSTEMROOT', 'WINDIR', 'COMSPEC', 'SYSTEMDRIVE', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'USERPROFILE',
                    'HOMEDRIVE', 'HOMEPATH', 'APPDATA', 'LOCALAPPDATA', 'PYTHONUTF8', 'PYTHONIOENCODING')


def test_environment(environ=None) -> dict:
    """The environment a mind's tests run in: the operating system's bootstrap variables only. Tests run code a model
    wrote, and the server's own environment may hold a provider key (an environment credential reference), so nothing
    else is passed on."""
    environ = os.environ if environ is None else environ
    return {k: v for k, v in environ.items() if k.upper() in TEST_ENVIRONMENT}


# -- the turn ----------------------------------------------------------------------------------------------------

def wake_messages(participant: Participant, context: dict, facts: dict) -> list:
    letter = facts['campaign'].get('deliverable') or ''
    from .mind_memory import words
    wake_tags = list(dict.fromkeys(list(participant.tags) + words(letter, 10) +
                                   words(' '.join(n['text'][:500] for n in context.get('unread', [])), 8) + ['carry']))
    memory = participant.wake_memory(wake_tags, commission_id=facts['campaign']['id'])
    welcome = ''
    if participant.cartridge() and not participant.technical():
        try:
            from .mind_memory import welcome as read_welcome
            welcome = read_welcome(participant.home, facts['campaign']['id']) + '\n\n'
        except Exception as exc:
            welcome = f'Welcome Dashboard unavailable: {type(exc).__name__}: {exc}\n\n'
    prior = ''
    if memory is not None:                              # a cartridge: headers, and the first lines of the last closing words
        carry = participant.continuity(max_experiences=0)['carry']
        from .mind_memory import render_log
        log_block = render_log(participant.log_recent(facts['campaign']['id'], limit=6), title='YOUR LOG, this commission')
        card = participant.card_line()
        cont_text = (f"{card}\n\n" if card else '') + memory + (f"\n\n{log_block}" if log_block else '') + (f"\n\nYOUR LAST WORDS (first lines; the whole note is in your memory):\n{carry[:600]}" if carry and not carry.startswith('New.') else '')
    else:                                               # folder memory: the carry and a few experiences, as before
        continuity = participant.continuity(tags=participant.tags)
        cont_text = (f"YOUR CARRY (what your last self left you):\n{continuity['carry']}\n\nYOUR INDEX:\n{continuity['index']}\n" +
                     ''.join(f"\n--- {name} ---\n{text}\n" for name, text in continuity['experiences']))
        prior = ''.join(f"\n- from commission {c['campaign_id']}: {c['text']}" for c in context.get('prior_closures', []))
    plan = '\n'.join(f"Sprint {s['number']}: {s['title']}" + ''.join(f"\n  - {t['title']}" + (f" ({t['who']})" if t.get('who') else '') for t in s['tasks']) for s in facts['plan']['sprints'])
    roster = ', '.join(f"{m.get('name') or m.get('id')} as {m.get('role', '?')}" for m in facts['roster'])
    verdicts = '\n'.join(f"- {v['verdict']} {v['subject']}: {v['why']}" for v in facts['verdicts'][-8:])
    deliverables = '\n'.join(f"- {d['name']} ({(d.get('sha256') or '')[:10]}) by {d['by'].get('name') or d['by']['id']}" for d in facts['deliverables'][-10:])
    unread = '\n\n'.join(f"[{n['sender'].get('name') or n['sender']['id']}{' to ' + (n['to'].get('name') or n['to']['id']) if n.get('to') else ''}]\n{n['text']}" +
                         (''.join(f"\n  (file {f['name']}, {f['bytes']} bytes)" for f in n.get('files', ())) if n.get('files') else '')
                         for n in context['unread'])
    questions = '\n'.join('- ' + q['text'] for q in context.get('questions_for_me', []))
    try:                                                 # whose move it is: a quiet room always has an owner (runs a80-a92)
        from .facts import waiting_on
        waiting = waiting_on(facts) if facts['campaign'].get('request_kind') not in ('chat', 'library') else ''
    except Exception:
        waiting = ''                                     # a line that cannot be worked out is left out, never a failed wake
    # The operator's own words lead the wake, so a long run of colleagues' notes cannot bury them (rehearsal F6).
    operator = '\n\n'.join(n['text'] for n in context['unread'] if (n.get('sender') or {}).get('kind') == 'operator')
    chat = ("THIS IS A DIRECT CHAT: the operator is talking with you, one to one. There is no plan, sprint, deliverable "
            "or acceptance unless they ask for one. Reply to them in plain words.\n\n"
            if facts['campaign'].get('request_kind') == 'chat' else '')
    user = chat + (f"THE COMMISSION ({facts['campaign']['id']}), opened by {(facts['campaign'].get('opened_by') or {}).get('name', 'the operator')}:\n{letter}\n\n"
            f"PLAN SO FAR:\n{plan or '(none yet)'}\n\nROSTER: {roster or '(none yet)'}\n"
            f"APPROVED SPRINTS: {', '.join(str(n) for n in sorted({a['sprint'] for a in facts['approvals'] if a.get('sprint') is not None})) or 'none yet'}   ACCEPTED: "
            f"{'yes' if facts['acceptance'] else 'no (the operator has not accepted; nothing is accepted until this says yes)'}\n"
            + (f"THE ROOM IS WAITING ON: {waiting}\n" if waiting else '')
            + (f"THE OPERATOR SAID, SINCE YOUR LAST TURN:\n{operator}\n\n" if operator else '')
            + f"DELIVERABLES:\n{deliverables or '(none yet)'}\n\nVERDICTS:\n{verdicts or '(none yet)'}\n\n"
            f"BUDGET LEFT: {json.dumps(context['budget_left'])}\n\n"
            f"FILES UNDER THE COMMISSION ROOT (read with read_files; write under your write roots):\n{context.get('tree') or '(no files yet)'}\n\n"
            + (f"YOUR SHELF ({context['shelf']['collection']}): {context['shelf']['items']} item(s), {context['shelf']['deposits']} deposit(s) waiting\n"
               f"{context['shelf']['listing']}\n\n" if context.get('shelf') else '')
            + f"NOTES YOU HAVE NOT READ ({context['unread_total']}):\n{unread or '(none)'}\n\n"
            f"THE ATELIER ASKS YOU:\n{questions or '(nothing)'}\n\n"
            f"YOUR CONTINUITY:\n{welcome}{cont_text}\n{('PRIOR COMMISSIONS, YOUR OWN CLOSING WORDS:' + prior) if prior else ''}\n\n"
            "Write your note to the room now. If you have nothing to add, reply exactly: (nothing to add)")
    system = f"{participant.system_text()}\n\n{RULES}\n\nYou are {participant.name}, the {participant.kind} of this commission."
    if participant.technical():
        from .specialist_learning import GUIDANCE
        system += "\n\n"+GUIDANCE
    return [dict(role='system', content=system), dict(role='user', content=user)]


def _tokens(value) -> int:
    """The estimate the fabric uses too: about four characters per token; nothing for nothing."""
    if not value:
        return 0
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    return len(value) // 4 + 1


def context_room(client) -> dict | None:
    """What one request may take: the catalogued window less the output reserve and the margin. None when the
    deployment carries no window (the guard then counts and does nothing)."""
    window = int(getattr(client, 'context_window', 0) or 0)
    if window <= 0:
        return None
    reserve = int(getattr(client, 'max_tokens', 0) or 0) or 4096
    return dict(window=window, reserve=reserve, room=max(0, window - reserve - int(window * CONTEXT_MARGIN)))


def request_estimate(messages: list, schemas, known: tuple | None = None) -> int:
    """Estimate the complete request, retaining provider calibration after compaction.

    Three-item observations include the raw estimate of the measured request. Its
    conservative ratio still applies after messages or tool schemas are removed.
    Two-item observations remain supported for existing diagnostic callers.
    """
    raw = sum(_tokens(m.get('content')) + _tokens(m.get('tool_calls')) + 4 for m in messages) + _tokens(schemas)
    if known and len(known) == 3:
        return math.ceil(raw * max(1.0, known[1] / max(1, known[2])))
    if known and known[0] <= len(messages):
        return int(known[1]) + sum(_tokens(m.get('content')) + _tokens(m.get('tool_calls')) + 4 for m in messages[known[0]:])
    return raw


def make_room(messages: list, room: int, schemas, known, *, final: bool = False) -> list:
    """Make the next request fit, changing only tool results. First the results of earlier rounds, largest first, are
    set aside as stubs (the mind read them once; a stub says what and how long). Then the results of the last round,
    which the mind has not seen yet, are cut to what fits, with a marker saying where to continue; when `final` (the
    note call, after the mind saw them) they are set aside too. Returns what was done, for the daybook."""
    last_round = max((i for i, m in enumerate(messages) if m.get('role') == 'assistant' and m.get('tool_calls')), default=-1)
    done = []

    def over():
        return request_estimate(messages, schemas, known) - room

    def candidates(newest: bool):
        return [(len(m.get('content') or ''), i) for i, m in enumerate(messages)
                if m.get('role') == 'tool' and not m.get('_aside') and len(m.get('content') or '') > SET_ASIDE_KEEP + 120
                and ((i > last_round) if newest else (i < last_round))]

    def set_aside(i):
        size = messages[i].get('_size') or len(messages[i]['content'])
        # Say which call the result came from, so the mind knows what it no longer holds (rehearsal 4, O8).
        what = _call_label(messages, messages[i].get('tool_call_id', ''))
        messages[i] = dict(messages[i], _aside=True, _size=size,
                           content=f'[{size} characters{" from " + what if what else ""} set aside to make room in your '
                                   'context. Note what you need from it in your reply; re-read it next turn if you must. '
                                   f'It began:]\n{messages[i]["content"][:SET_ASIDE_KEEP]}')
        done.append(dict(kind='set_aside', tool_call_id=messages[i].get('tool_call_id', ''), characters=size, kept=SET_ASIDE_KEEP))

    while over() > 0 and candidates(False):
        set_aside(max(candidates(False))[1])
    while over() > 0 and candidates(True):
        size, i = max(candidates(True))
        if final:
            set_aside(i)
            continue
        whole = messages[i].get('_size') or size                 # a result cut before keeps its original size
        keep = max(SET_ASIDE_KEEP, size - (over() + 40) * 4)
        messages[i] = dict(messages[i], _size=whole,                   # cut, not set aside: a later round may still set it aside
                           content=messages[i]['content'][:keep] + f'\n[cut here at {keep} of {whole} characters to fit your context; '
                                                                  f're-read next turn from there if you need the rest]')
        done.append(dict(kind='cut', tool_call_id=messages[i].get('tool_call_id', ''), characters=whole, kept=keep))
    return done


def _call_label(messages: list, tool_call_id: str) -> str:
    """The tool call that produced a result, as `name(arguments)`, shortened; '' when it cannot be found."""
    for m in messages:
        for tc in (m.get('tool_calls') or ()) if m.get('role') == 'assistant' else ():
            if tc.get('id') == tool_call_id:
                fn = tc.get('function') or {}
                args = ' '.join(str(fn.get('arguments') or '').split())
                return f"{fn.get('name', '')}({args[:160]}{'...' if len(args) > 160 else ''})"
    return ''


def _outbound(messages: list) -> list:
    return [{k: v for k, v in m.items() if not k.startswith('_')} for m in messages]


class ContextCapacityError(RuntimeError):
    """An unsent request; retains work without pretending the host authored closure."""
    def __init__(self, message, cost, transcript):
        super().__init__(message)
        self.cost, self.transcript = cost, transcript
        self.recorded = False


def think(client, messages: list, tools: Tools, max_calls: int | None = None) -> tuple:
    """Tool rounds followed by a note, checking capacity at every actual dispatch.

    Only tool-result copies are compacted. If even the note cannot fit, the host
    retains the full transcript and costs through run_turn's existing failure path.
    No identity, instruction or Welcome card is silently removed to make it fit.
    """
    started = time.perf_counter(); calls = 0; prompt = completion = 0; model_seconds = 0.0
    schemas = tool_schemas(librarian=tools.library is not None, lead=tools.p.kind == 'lead',technical=tools.p.technical(),
                           reviewer=tools.p.kind == 'reviewer')
    if tools.data_sources and permits(tools.p, 'data_sources') and tools.data_sources.directory():
        from aether.data_sources.service import schema as source_schema
        schemas.append(source_schema())
        messages.append(dict(role='system', content=tools.data_sources.briefing()))
    if tools.p.technical():
        for schema in schemas:
            f=schema['function']
            if f['name'] in ('memory_workspace','memory_query','log_action','recall_log','file_decision'): f['description']='Project-local continuity. '+f['description']
            if f['name']=='file_memory': f['description']='Direct filing: lesson/warning goes to technical memory; fact/todo stays with this project. Distill technical details without private or project information.'
    if tools.delegation is not None:
        from .ultralight_delegation import schema
        schemas.append(schema())
    schemas=[s for s in schemas if permits(tools.p,s['function']['name'])]
    rounds = MAX_TOOL_ROUNDS if max_calls is None else max(0, min(MAX_TOOL_ROUNDS, int(max_calls) - 1))
    guard = context_room(client)
    context = dict(window=guard['window'] if guard else None, room=guard['room'] if guard else None,
                   peak_estimate=0, guard_fired=False, made_room=[], reread_after_set_aside=0)
    known = None
    called, aside = {}, set()           # tool call id -> what was asked; what was set aside (re-reads are counted, O8)
    transcript = deepcopy(messages)

    def account():
        return dict(model_calls=calls, prompt_tokens=prompt, completion_tokens=completion,
                    seconds=round(model_seconds, 1), turn_seconds=round(time.perf_counter() - started, 1), context=context,
                    ultralights=deepcopy(tools.delegation.receipts) if tools.delegation else [])

    def append(message):
        messages.append(message)
        transcript.append(deepcopy(message))

    def call(with_tools):
        nonlocal calls, prompt, completion, model_seconds, known
        if tools.data_sources and tools.data_sources.used:
            available = {s['id'] for s in tools.data_sources.directory()}
            if not tools.data_sources.used <= available:
                raise PermissionError('Data-source access changed before the next model request.')
        selected = schemas if with_tools else None
        estimated = request_estimate(messages, selected, known)
        context['peak_estimate'] = max(context['peak_estimate'], estimated)
        if guard and estimated > guard['room']:
            made = make_room(messages, guard['room'], selected, known, final=not with_tools)
            context['made_room'] += made
            aside.update(called[m['tool_call_id']] for m in made if m['kind'] == 'set_aside' and m['tool_call_id'] in called)
            estimated = request_estimate(messages, selected, known)
            if estimated > guard['room']:
                context.update(guard_fired=True, fired_at_call=calls + 1, unsent_estimate=estimated)
                return None
        raw = request_estimate(messages, selected)
        t0 = time.perf_counter()
        reply = client.chat(_outbound(messages), tools=selected)
        calls += 1; model_seconds += time.perf_counter() - t0
        usage = reply.get('_usage') or {}
        if usage.get('prompt_tokens'):
            ratio = max(1.0, known[1] / max(1, known[2])) if known else 1.0
            known = (len(messages), max(int(usage['prompt_tokens']), math.ceil(raw * ratio)), raw)
        prompt += int(usage.get('prompt_tokens') or 0)
        completion += int(usage.get('completion_tokens') or 0)
        return reply

    text = None
    ending = 'You have used the tool rounds (or the model calls the budget allows) for this turn. '
    for _ in range(rounds):
        if max_calls is not None and calls >= int(max_calls) - 1:
            break
        reply = call(True)
        if reply is None:
            ending = (f"Your context is nearly full (about {context['unsent_estimate']} tokens against "
                      f"{guard['room']} of room in a {guard['window']}-token window). ")
            break
        tool_calls = reply.get('tool_calls') or []
        append({k: v for k, v in reply.items() if k in ('role', 'content', 'tool_calls')} | dict(role='assistant'))
        if not tool_calls:
            text = reply.get('content') or ''
            break
        for tc in tool_calls:
            fn = tc.get('function') or {}
            try:
                args = json.loads(fn.get('arguments') or '{}')
            except ValueError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            asked = (fn.get('name', ''), json.dumps(args, sort_keys=True))
            context['reread_after_set_aside'] += asked in aside
            called[tc.get('id', fn.get('name', ''))] = asked
            tools.remaining_calls = (int(max_calls) - calls) if max_calls is not None else MAX_TOOL_ROUNDS - calls
            before = dict(tools.delegation.usage) if tools.delegation else {}
            result = tools.call(fn.get('name', ''), args)
            if tools.delegation:
                after = tools.delegation.usage
                calls += after['model_calls'] - before['model_calls']
                prompt += after['prompt_tokens'] - before['prompt_tokens']
                completion += after['completion_tokens'] - before['completion_tokens']
                model_seconds += after['seconds'] - before['seconds']
            append(dict(role='tool', tool_call_id=tc.get('id', fn.get('name', '')), content=result))
    if text is None:
        append(dict(role='user', content=ending + 'Write your note to the atelier now, without more tool calls: what you did, '
                    'what is in place, and exactly what you would continue with next turn (which files to re-read, which step is next). '
                    'The next turn starts fresh from the daybook with your note in it. Files you wrote, and files you name as '
                    'deliverables, are attached automatically.'))
        reply = call(False)
        if reply is None:
            raise ContextCapacityError(
                f"The continuation request needs about {context['unsent_estimate']} input tokens; this deployment has "
                f"{guard['room']} available after output reserve and margin. No oversized request was sent. "
                'The host retained the work; there is no agent-authored closing note. Use a smaller input or a larger configured context.',
                account(), transcript)
        text = reply.get('content') or '(nothing to add: I ran out of tool rounds)'
    return text, account()


def _take_deposits(library, atelier, participant: Participant, inbox: list) -> list:
    """A note with files addressed to the librarian is a deposit: its files go to deposits/ under the note's id, once."""
    mine = {participant.id.lower(), participant.name.lower(), 'librarian'}
    mine |= {w for x in list(mine) for w in re.split(r'[\s\-_]+', x) if len(w) >= 3}
    taken = []
    for note in inbox:
        to = note.get('to') or {}
        target = {str(to.get('id', '')).lower(), str(to.get('name', '')).lower()} - {''}
        if not note.get('files') or not (target & mine):
            continue
        files = {}
        for ref in note['files']:
            try:
                files[ref['name']] = atelier.daybook.read_file(ref['sha256'])
            except Exception:
                continue
        if files:
            taken.append(library.deposit(note['sender'], files, note.get('text', ''), commission=atelier.campaign_id,
                                         deposit_id=f"note-{note['id'][:8]}"))
    return taken


def _tree(root: Path | None, limit: int = 300) -> str:
    """The files under the commission root, relative, so a mind sees the ground before it spends a tool round."""
    if root is None or not Path(root).exists():
        return '(no files yet)'
    out = []
    for path in sorted(Path(root).rglob('*')):
        if path.is_dir() or any(part in ('__pycache__', '.pytest_cache', '.git') for part in path.parts):
            continue
        out.append(path.relative_to(root).as_posix())
        if len(out) >= limit:
            out.append('...'); break
    return '\n'.join(out) or '(no files yet)'


def _resolve(name: str, facts: dict, atelier) -> dict:
    """Whom "To: <name>" means: a roster member by id or name, else a mailbox on the share, else the name as given."""
    low = name.lower()
    for member in facts.get('roster') or ():
        if low in (str(member.get('id', '')).lower(), str(member.get('name', '')).lower()):
            return dict(id=member.get('id') or name, kind=member.get('kind', 'member'), name=member.get('name') or name)
    try:
        for pid in Mailbox.all_under(Path(atelier.root)):
            if pid.lower() == low or (len(low) >= 3 and low in re.split(r'[\s\-_]+', pid.lower())):
                return dict(id=pid, kind='member', name=name)
    except OSError:
        pass
    return dict(id=name, kind='member', name=name)


NAMED_FILE_MAX = 2 * 1024 * 1024


def _signals_text(signals: dict) -> str:
    """The note a turn posts when it used a signal tool but wrote nothing else, so the room sees what was said."""
    lines = []
    if signals.get('verdicts'):
        lines += [f"{v['requirement']}: {v['verdict']}" + (f" - {v['why']}" if v.get('why') else '') for v in signals['verdicts']]
    if signals.get('ready'):
        lines.append(f"Ready for the operator's ruling: {signals['ready']}")
    if signals.get('closing'):
        c = signals['closing']
        lines += [f'{label}: {c[k]}' for k, label in (('did', 'What I did'), ('learned', 'What I learned'),
                                                       ('carry', 'What my future self should carry'), ('skip', 'What my next self should skip')) if c.get(k)]
    return '\n'.join(lines)


def _named_files(text: str, participant, root, written: dict) -> dict:
    """Deliverables a note names that are on disk but were not written in this turn (a file kept from an interrupted
    turn, say), attached as they are now so the record holds their bytes by hash (rehearsal 4, O7). Only files the mind
    may read itself; reading only."""
    if root is None or not text:
        return {}
    from .facts import extract
    named = extract(dict(sender=dict(kind='member'), text=text), {}).get('deliverables') or []
    out = {}
    for name in named:
        rel = str(name).strip().replace('\\', '/').lstrip('/')
        if not rel or rel in written or rel in out:
            continue
        try:
            path = resolve_participant_path(participant, Path(root).resolve(), rel, 'read')
            if path.is_file() and path.stat().st_size <= NAMED_FILE_MAX:
                out[rel] = path.read_bytes()
        except (OSError, ValueError, PermissionError):
            continue
    return out


def _answered_already(mailbox: Mailbox, inbox: list) -> Path | None:
    """The note this mind already wrote for the newest unread note, when a turn died between sending it and marking
    the inbox read: the outbox still holds it, or the postmaster has moved it to sent. Reading only; nothing is sent."""
    if not inbox:
        return None
    newest = inbox[-1]['id']
    for folder in ('outbox', 'sent'):
        paths = sorted((mailbox.root / folder).glob('*.json'), key=lambda p: p.stat().st_mtime_ns, reverse=True)[:50]
        for path in paths:
            try:
                if json.loads(path.read_text(encoding='utf-8')).get('in_reply_to') == newest:
                    return path
            except (OSError, ValueError):
                continue
    return None


def run_turn(participant, atelier=None, mailbox: Mailbox | None = None, client=None, commission_root: Path | None = None, since_message_id=None, place: str | None = None, profiles_path=None, stopped=None):
    """One turn for one mind: wake from the atelier and its mailbox, think, send the note through the mailbox.
    Returns the outbox path, or None when the mind had nothing to add.

    Two call shapes: the place daemon's `run_turn(participant, atelier, mailbox, client, root)`, and the page's
    `run_turn(atelier, participant_record, since_message_id)`, where the mind's home is found under
    <atelier root>/homes/<participant id>/ and its deployment in <atelier root>/runtime_profiles.json."""
    if isinstance(participant, Atelier):                    # the page's shape
        atelier, record = participant, atelier
        participant = Participant.load(Path(atelier.root) / 'homes' / record['id'])
        mailbox = Mailbox(atelier.root, participant.record())
        client = Deployment.load(Path(atelier.root) / 'runtime_profiles.json', participant.deployment).client()
        commission_root = Path(atelier.root) / 'work' / atelier.campaign_id
        from .postmaster import Postmaster
        Postmaster(atelier, atelier.root).deliver({participant.id: participant.kind}, {participant.id: participant.name})
    if participant.technical():
        from .specialist_context import bind
        bind(participant,commission_root,atelier.campaign_id)
    context = atelier.wake_context(participant.record())
    facts = atelier.facts()
    if hasattr(participant, 'campaign_members'):
        current = participant.campaign_members
        ids, names = {m['id'] for m in current}, {m['name'] for m in current}
        roster = [m for m in facts['roster'] if m.get('id') not in ids and m.get('name') not in names] + current
        facts = dict(facts, roster=roster)
        context = dict(context, roster=roster)
    inbox = mailbox.inbox(commission=atelier.campaign_id)
    if not inbox and not context.get('questions_for_me'):
        return None
    answered = _answered_already(mailbox, inbox)
    if answered is not None:                             # the last turn died after sending and before marking read
        for note in inbox:
            mailbox.mark_read(note['id'])
        atelier.mark_read(participant.record(), context.get('seen_through'))
        participant.log([dict(kind='recovered', pointer='note:' + answered.stem, tags=['turn', 'recovery'],
                              line='my answer to these notes was already written when I woke; marked them read, no second turn')],
                        commission_id=atelier.campaign_id)
        return answered
    if participant.cartridge() and not participant.technical():
        from .mind_memory import begin_work
        try:
            participant.foundation_session_id = begin_work(participant.home, atelier.campaign_id)
        except Exception as exc:
            participant.foundation_session_error = str(exc)
    library = None
    if participant.kind == 'librarian' and participant.library:
        from aether.library import Library
        library = Library(atelier.root, participant.library)
        _take_deposits(library, atelier, participant, inbox)
        listing = '\n'.join(f"  {item_id:<28} {e['kind']:<10} {e['title'][:70]}" for item_id, e in sorted(library.entries().items())[:80])
        context = dict(context, shelf=dict(collection=participant.library, items=len(library.entries()), deposits=len(library.deposits()),
                                           listing=listing or '  (empty)'))
    messages = wake_messages(participant, dict(context, unread=inbox, unread_total=len(inbox), tree=_tree(commission_root)), facts)
    from .ultralight_delegation import configured
    helper_profiles = getattr(participant, 'campaign_profiles_path', None) or profiles_path or Path(atelier.root) / 'runtime_profiles.json'
    def helper_stopped():
        return bool((stopped and stopped()) or atelier.waiting_for_operator())
    tools = Tools(participant, commission_root, library=library, commission_id=atelier.campaign_id,
                  delegation=configured(helper_profiles, stopped=helper_stopped,
                                        document=getattr(participant, 'delegation_profiles_snapshot', None)))
    left = context.get('budget_left') or {}
    max_calls = None
    if left.get('model_calls_limit') is not None:
        max_calls = max(1, int(left['model_calls_limit']) - int(left.get('model_calls_used') or 0))
    try:
        text, cost = think(client, messages, tools, max_calls=max_calls)
    except ContextCapacityError as exc:
        from .atelier import HOST
        transcript = atelier.daybook.store_file('context-continuation.json',
            json.dumps(exc.transcript, ensure_ascii=False).encode('utf-8'), HOST)
        files = [transcript] + [atelier.daybook.store_file(name, data, participant.record())
                               for name, data in tools.written.items()]
        atelier.turn_failed(participant.record(), str(exc),
            dict(context=exc.cost['context'], transcript=transcript, actions=tools.actions), cost=exc.cost, files=files)
        exc.recorded = True
        raise

    faults.point('after_think')
    text = (text or '').strip()
    receipt = None
    if participant.cartridge():                          # the recall receipt: offered at the wake, looked up, read
        receipt = dict(offered=list(getattr(participant, 'last_offered', []) or []), recalled=tools.memory['recalled'], loaded=tools.memory['loaded'])
        shown = list(getattr(participant, 'last_decisions', []) or [])
        if shown:
            receipt['decisions_shown'] = shown
        if tools.decisions:
            receipt['decisions_filed'] = tools.decisions
        if text and not text.startswith('(nothing to add'):     # D6: what a cited decision rests on, said in the room after the note
            from .mind_memory import cited_decisions
            written = [data.decode('utf-8', errors='replace') for rel, data in tools.written.items() if rel.rsplit('.', 1)[-1] in ('md', 'txt', 'py', 'json')]
            try:
                from .specialist_context import home as project_home
                cited_home=project_home(participant,atelier.campaign_id) if participant.technical() else participant.home
                cited = cited_decisions(cited_home, [text] + written, shown=shown,
                                        loaded=[n['id'] for n in tools.memory['loaded'] if n.get('kind') == 'decision'])
            except Exception:
                cited = []
            if cited:
                receipt['cited'] = cited
                tools.actions += [dict(kind='cited-decision', pointer=','.join(d['id'] for d in c['rests_on']) or 'none', tags=['decision'],
                                       line=f"cited a decision: {c['sentence'][:150]}") for c in cited]
    if tools.signals:
        receipt = dict(receipt or {}, signals=tools.signals)
        if not text or text.startswith('(nothing to add'):
            text = _signals_text(tools.signals)          # a signal said with its tool is never lost in a quiet turn
    if not text or text.startswith('(nothing to add'):
        for note in inbox:                                # a quiet turn: marks first, then the record of the quiet
            mailbox.mark_read(note['id'])
        atelier.mark_read(participant.record(), context.get('seen_through'))
        atelier.quiet(participant.record(), cost, memory=receipt)
        if tools.actions:                                # a quiet turn that did things keeps them; one that only read leaves no row
            participant.log(tools.actions[tools.persisted_actions:] + [dict(kind='quiet', pointer='', tags=['turn'], line='read the new notes and had nothing to add')], commission_id=atelier.campaign_id)
        return None
    to = None
    first, _, rest = text.partition('\n')
    for line in text.splitlines():                       # "To: Harbor Guide - you're up" on any line addresses the note
        m = re.match(r'\s*to:\s*([A-Za-z][\w\- ]*?)\s*(?:[-—–:,.]|$)', line, re.I)
        if m:
            to = _resolve(m.group(1).strip(), facts, atelier)
            if line == first:
                text = rest.strip() or text
            break
    files = {**tools.written, **_named_files(text, participant, commission_root, tools.written)} or None
    # The note first, then the marks: a turn that dies between the two is found by _answered_already at the next wake
    # and not answered twice; a turn that dies before the send leaves its mail unread and is woken again.
    sent = mailbox.send(text, files=files, cost=cost, to=to, in_reply_to=inbox[-1]['id'] if inbox else None,
                        place=place or getattr(atelier, 'place', None), memory=receipt)
    faults.point('after_send')
    for note in inbox:
        mailbox.mark_read(note['id'])
    atelier.mark_read(participant.record(), context.get('seen_through'))
    faults.point('after_marks')
    participant.log(tools.actions[tools.persisted_actions:] + [dict(kind='posted', pointer='note:' + Path(sent).stem, tags=['turn'] + (['to:' + (to.get('name') or to.get('id'))] if to else []),
                                          line='posted: ' + text.splitlines()[0][:120])], commission_id=atelier.campaign_id)
    return sent
