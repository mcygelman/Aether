"""A heavyweight's fresh Overseer turn: identity, memory, directive, state, notice.

The mind chooses all plan changes. This runner never interprets its prose as a
verdict, retries model calls, or imports the Atelier room.
"""
from __future__ import annotations

import json
from pathlib import Path

from aether.integrity.sanctum import verify
from aether.tasker._files import file_lock, now
from aether.tasker.model import ContextCapacityError, Deployment, SetupError, tool_loop
from aether.tasker.sprint import Sprint, Wake, TurnResult, OVERSEER_WAKES
from aether.tasker.staff_turn import profiles_path, redact
from aether.tasker.tools import OVERSEER_TASKER_TOOLS, OVERSEER_FILE_TOOLS, OverseerTools, _f, _read_text
from aether.tasker import ultralight
from aether.tasker.path_context import overseer_context
from aether.tasker.model import TurnLoopError
from aether.tasker.turn_journal import Journal, EvidenceError, resume_messages
from aether.tasker.terminal import stop
from . import continuity, memory, owner_memory
from .homes import home_path
from .roadmap import record_outcome, roadmap_path
from .accounting import AccountedClient, failure_cost

PACKAGE = Path(__file__).parent
JOURNAL_LIMIT = 6000
S = {'type': 'string'}
HEAVYWEIGHT_TOOLS = [
    _f('recall_memory', 'Recall headers from your own cartridge.', {'words': {'type': 'array', 'items': S}}, ['words']),
    _f('load_memory', 'Read selected notes from your own cartridge.', {'ids': {'type': 'array', 'items': S}}, ['ids']),
    _f('give_closing_words', 'After your own memory work and sprint_finish, publish your exact carry. Optional carry_note_id selects your own existing note with this exact body.',
       {'text': S, 'carry_note_id': S}, ['text']),
    _f('roadmap_update', 'Record this sprint outcome on the configured project roadmap.',
       {'summary': S, 'result': {'type': 'string', 'enum': ['pass', 'fail']}}, ['summary', 'result']),
    _f('librarian_consult', 'Ask the heavyweight Librarian for knowledge with source references.', {'question': S}, ['question']),
    _f('librarian_build_knowledge', 'Ask the Librarian to select source material for knowledge.sqlite.', {'brief': S}, ['brief']),
]


def build_client(sprint, agent_id):
    record = sprint.record()
    deployments = record.get('deployments') or {}
    participant = json.loads((home_path(sprint.data_root, agent_id) / 'participant.json').read_text(encoding='utf-8'))
    name = deployments.get(agent_id) or participant.get('deployment') or deployments.get('*')
    if not name:
        raise SetupError(f'no deployment configured for heavyweight {agent_id}')
    return Deployment.load(profiles_path(sprint), name).client()


def journal_tail(sprint):
    path = sprint.root / 'overseer/journal.md'
    if not path.exists():
        return '(no prior turns)'
    with path.open('rb') as stream:
        stream.seek(0, 2)
        length = stream.tell()
        stream.seek(max(0, length - JOURNAL_LIMIT * 4))
        text = stream.read().decode('utf-8', errors='replace')
    return ('[earlier entries omitted; read_file overseer/journal.md for history]\n' if length > JOURNAL_LIMIT else '') + text[-JOURNAL_LIMIT:]


def wake_messages(sprint, wake):
    config = sprint.record()
    project = config.get('project')
    project_context = (json.dumps(dict(project=project, roadmap_path=str(roadmap_path(sprint, config)),
                                     roadmap_id=config.get('roadmap_id') or project,
                                     path_binding='explicit' if config.get('roadmap_path') else 'default'),
                                  ensure_ascii=False)
                       if project else 'No linked project; roadmap_update is not required.')
    home = home_path(sprint.data_root, sprint.overseer)
    core = (home / 'identity/System_0.txt').read_text(encoding='utf-8')
    report = verify(home)
    goals = (sprint.root / 'SPRINT_GOALS.md').read_text(encoding='utf-8')
    overview = sprint.root / 'SPRINT_OVERVIEW.md'
    tags = memory.words(goals)
    tk = sprint.tasker('overseer:' + sprint.overseer)
    tasks = tk.list()
    summary = '\n'.join(f"T{t.number} {t.status} {t.assigned_to or 'unassigned'}: {t.title[:200]} "
                        f"deps={t.depends_on} note={t.note[:400]}"
                        for t in tasks if t.status != 'Closed')
    parts = [overseer_context(sprint), '=== YOUR IDENTITY ===', core, '=== IDENTITY INTEGRITY (advisory) ===',
             json.dumps(report.to_dict(), ensure_ascii=False),
             '=== WELCOME ===', continuity.welcome(home, sprint.id), owner_memory.GUIDANCE,
             memory.render_headers(memory.recall(home, tags)),
             memory.render_decisions(memory.decisions(home, sprint.id, tags)),
             '=== OVERSEER DIRECTIVE ===', (PACKAGE / 'directives/OVERSEER_DIRECTIVE.txt').read_text(encoding='utf-8'),
             '=== LEXICON ===', (PACKAGE.parent / 'tasker/assets/Aether_Crib_Sheet.txt').read_text(encoding='utf-8'),
             '=== EXACT TIMER NOTICE REFERENCE ===', (PACKAGE.parent / 'tasker/directives/NOTICES.txt').read_text(encoding='utf-8'),
             '=== LINKED PROJECT ===', project_context,
             '=== SPRINT GOALS ===', goals, '=== SPRINT OVERVIEW ===',
             overview.read_text(encoding='utf-8') if overview.exists() else '(not written)',
             '=== CURRENT TASKER ===', json.dumps(tk.counts()), summary,
             '=== YOUR RECENT SPRINT JOURNAL ===', journal_tail(sprint)]
    return [dict(role='system', content='\n'.join(parts)), dict(role='user', content=wake.text)]


class Tools:
    def __init__(self, sprint, *, client=None, stopped=None, delegation=None):
        self.sprint, self.client, self.stopped, self.delegation = sprint, client, stopped, delegation
        self.home = home_path(sprint.data_root, sprint.overseer)
        self.owner_memory = owner_memory.OwnerMemory(self.home, sprint.id,
                                                     context_window=getattr(client, 'context_window', None))
        self.tasker = OverseerTools(sprint)
        self.nested = dict(model_calls=0, prompt_tokens=0, completion_tokens=0)
        self.librarian_receipts = []

    def schemas(self):
        files = [t for t in OVERSEER_FILE_TOOLS if t['function']['name'] != 'write_file' or not self.sprint.record().get('staff')]
        return OVERSEER_TASKER_TOOLS + files + HEAVYWEIGHT_TOOLS + owner_memory.schemas() + ([ultralight.schema()] if self.delegation else [])

    def call(self, name, args):
        try:
            if name.startswith('memory_'):
                return self.owner_memory.call(name, args)
            if name == 'recall_memory':
                return memory.render_headers(memory.recall(self.home, args['words']))
            if name == 'load_memory':
                return json.dumps(memory.load(self.home, args['ids'], session=self.sprint.id), ensure_ascii=False)
            if name == 'give_closing_words':
                if not self.sprint.record().get('state', '').startswith('closed-'):
                    return 'Record the sprint outcome with sprint_finish before giving your closing words.'
                return json.dumps(continuity.close(self.home, self.sprint.id, args['text'], args.get('carry_note_id')), ensure_ascii=False)
            if name == 'roadmap_update':
                return json.dumps(record_outcome(self.sprint, **args), ensure_ascii=False)
            if name == 'delegate_ultralight' and self.delegation:
                return self.delegation.call(args, remaining_calls=2)
            if name in ('librarian_consult', 'librarian_build_knowledge'):
                if len(self.librarian_receipts) >= 2:
                    return 'Two Librarian requests have been used this turn; carry further research to a later turn.'
                from .librarian import run_librarian
                result = run_librarian(self.sprint, args.get('question') or args.get('brief'),
                                       build=name.endswith('knowledge'), stopped=self.stopped)
                for key in self.nested:
                    self.nested[key] += int(result.cost.get(key, 0))
                self.librarian_receipts.append(dict(ok=result.ok, cost=result.cost, error=result.error))
                return result.text if result.ok else result.error
            if name == 'write_file' and self.sprint.record().get('staff'):
                return 'Staff are hired; assign a task to write or integrate outputs. write_file is solo-only.'
            if name in ('read_file', 'list_dir'):
                return self.read(args.get('path', '.'), directory=name == 'list_dir',
                                 offset=args.get('offset', 0), limit=args.get('limit'))
            return self.tasker.call(name, args)
        except Exception as exc:
            return redact(f'{type(exc).__name__}: {exc}')

    def read(self, path, *, directory=False, offset=0, limit=None):
        p = Path(path)
        p = (p if p.is_absolute() else self.sprint.root / p).resolve()
        roots = [self.sprint.root.resolve()] + [Path(r).resolve() for r in self.sprint.record().get('overseer_read_roots', [])]
        locations = (f'Relative paths start in the sprint folder: {roots[0]}\n'
                     'Operator-granted project roots (use the full path):\n' +
                     ('\n'.join(str(root) for root in roots[1:]) or '(none)'))
        if not any(p == root or root in p.parents for root in roots):
            raise PermissionError('read_file is limited to the sprint and operator-granted project roots.\n' + locations)
        if p.name == '.sealbox_key' or p.suffix.lower() == '.key' or any(x.lower() in ('credentials', '.aws', '.ssh') for x in p.parts):
            raise PermissionError('credential files are not task inputs')
        if directory or p.is_dir():
            listing = '\n'.join(sorted(x.name + ('/' if x.is_dir() else '') for x in p.iterdir()))[:12000]
            return listing + ('\n\n' + locations if p == roots[0] else '')
        return _read_text(p, offset, limit)


def run_overseer_turn(sprint: Sprint, wake: Wake, *, client=None, stopped=None) -> TurnResult:
    meter, tools, delegation = None, None, None
    try:
        if wake.kind not in OVERSEER_WAKES:
            raise ValueError(f'unknown Overseer wake: {wake.kind}')
        home = home_path(sprint.data_root, sprint.overseer)
        if client is None:
            client = build_client(sprint, sprint.overseer)
        continuity.activate(home, sprint.id)
        messages = wake_messages(sprint, wake)
        messages = resume_messages(sprint,'overseer',wake,messages)
        delegation = ultralight.configured(profiles_path(sprint), stopped=stopped)
        tools = Tools(sprint, client=client, stopped=stopped, delegation=delegation)
        meter = AccountedClient(client)
        text, cost, calls = tool_loop(meter, messages, tools.schemas(), tools.call,
                                      stopped=stopped, delegation=delegation,checkpoint=Journal(sprint,'overseer'))
        for key, value in tools.nested.items():
            cost[key] = cost.get(key, 0) + value
        cost['tools'], cost['librarian'] = calls, tools.librarian_receipts
        journal = sprint.root / 'overseer/journal.md'
        journal.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(journal.with_suffix('.lock')):
            with journal.open('a', encoding='utf-8', newline='\n') as stream:
                stream.write(f'\n## {now()} {wake.kind}\n\n{text}\n')
        return TurnResult(True, text, cost)
    except ContextCapacityError as exc:
        return TurnResult(False, '', dict(exc.cost,**failure_cost(meter, nested=tools.nested if tools else None, delegation=delegation)),
                          redact(f'ContextCapacityError: {exc}'))
    except TurnLoopError as exc:
        if exc.uncertain or isinstance(exc.__cause__,EvidenceError):
            stop(sprint,'operation_uncertain','Operation evidence is incomplete; preserve it and do not replay the action.',agent='overseer')
        return TurnResult(False,'',dict(exc.cost,**failure_cost(meter,nested=tools.nested if tools else None,delegation=delegation)),redact(str(exc)))
    except Exception as exc:
        kind = 'SetupError' if isinstance(exc, (SetupError, FileNotFoundError)) else type(exc).__name__
        return TurnResult(False, '', failure_cost(meter, nested=tools.nested if tools else None, delegation=delegation),
                          redact(f'{kind}: {exc}'))
