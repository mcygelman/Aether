"""Own GUI project bindings and the lifetime of explicitly started native Timers.

Never infer acceptance, author reflections, migrate old sprints, or restart on read.
Tasker owns execution, contracts, budgets, closure and verification.
"""
from pathlib import Path
import hashlib
import math
import re
import threading
import uuid

from aether.tasker._files import file_lock, now, read_json, write_json
from aether.tasker import verification
from aether.tasker.cli import status
from aether.tasker.sprint import DEFAULT_CAPS, load_sprint, new_sprint

# Plain words for the operator. They only describe native state; they never decide it.
RUN_WORDS = dict(prepared='Ready to start', running='Working', stopping='Stopping after the current turns',
                 stopped='Stopped', interrupted='Interrupted; it will not restart on its own',
                 unavailable='Unavailable')
RESULT_WORDS = dict(accepted='Accepted', review_required='Waiting for your review',
                    review_failed='You marked it Fail', unbound_or_stale_review='Your review no longer matches the output; review again',
                    unverified_or_stale='Output changed after its check', failed='Output checks failed',
                    incomplete='Finished without a checked result', legacy_unverified='Finished (older project, not checked)')


def headline(execution, verification_status, *, terminal_failure=None, sprint_state=None):
    if execution != 'ended':
        return RUN_WORDS.get(execution, execution)
    # 11-04 SVG Cat runs: a run the Timer stopped on a failure read "Finished without a checked result" or "Output
    # checks failed", like the ones the team closed. Name the stop; the page's detail line gives its reason.
    if terminal_failure:
        return 'Stopped before finishing'
    if verification_status == 'incomplete' and sprint_state == 'closed-pass':
        return 'Closed by the team; not reviewable now'
    return RESULT_WORDS.get(verification_status, 'Finished')


def _files(text):
    try:
        names = [n.strip().strip("'\"") for n in text.strip().strip('[]').split(',') if n.strip()]
    except AttributeError:
        return ''
    return ', '.join(names)


def failure_words(text):
    """A stop's reason in plain words (Michel, 2026-11-07: the code line 'reads as code'). The raw reason stays in the
    technical record; this never decides anything."""
    if not text:
        return ''
    parts = str(text).split(': ', 2)
    kind, detail = (parts[1], parts[2]) if len(parts) == 3 else ('', str(text))
    if kind == 'output_verification_failed':
        match = re.search(r'missing=(\[[^\]]*\]), extra=(\[[^\]]*\])', detail)
        if match:
            missing, extra = _files(match.group(1)), _files(match.group(2))
            said = []
            if missing:
                said.append('these files were not written: ' + missing)
            if extra:
                said.append('the lead also wrote files this sprint did not ask for: ' + extra)
            return 'The outputs did not pass their checks: ' + '; '.join(said) + '.'
        return 'The outputs did not pass their checks: ' + detail
    known = dict(timer_failed='The app could not keep the work running', operation_uncertain='An action’s result could not be confirmed',
                 interrupted_turn='The work was cut off in the middle of a turn',
                 staff_closure_missing='A staff member did not file their closing words',
                 overseer_closure_missing='The lead did not file its closing words',
                 staff_writeback_failed='A staff member’s closing words could not be saved',
                 staff_closure_inconsistent='A staff member’s closing record did not match')
    return known.get(kind, 'The work stopped') + ': ' + detail


class TaskerProjects:
    def __init__(self, root, profiles, *, halted=lambda: False, runners=None, interval=0.2, roadmaps=None):
        self.root = Path(root).resolve()
        self.profiles = Path(profiles).resolve()
        self.halted, self.runners, self.interval = halted, runners, interval
        self.roadmaps = roadmaps         # ProjectFolders: where a sprint started from a roadmap records its outcome
        self.index = self.root / 'gui-projects.json'
        self.lock = threading.RLock()
        self.workers = {}

    def _rows(self):
        return read_json(self.index, {})

    def _bound(self, project):
        row = self._rows().get(project)
        if not row:
            raise ValueError('Native project not found. Historical projects are not migrated.')
        path = self.root / 'sprints' / row['sprint_id']
        if path.resolve().parent != (self.root / 'sprints').resolve():
            raise ValueError('Invalid sprint binding')
        sprint = load_sprint(path)
        if sprint.data_root.resolve() != self.root or sprint.record().get('project') != project:
            raise ValueError('Sprint binding changed; preserve this project')
        return row, sprint

    def _staff(self):
        """On first use, seed the Staff Home with the shipped standard staff; later, it is the operator's."""
        from aether.tasker import staff
        with self.lock:
            if not (staff.staff_root(self.root) / 'roster.json').exists() and staff.PRODUCT_STAFF.is_dir():
                staff.seed_staff(self.root, staff.PRODUCT_STAFF)
            # The shipped catalog's categories group the list on the page; staff it does not know (those made with
            # Create a specialist) show as "Your specialists".
            catalog = read_json(staff.PRODUCT_STAFF / 'catalog.json', {}).get('templates') or []
            catalog = [t for t in catalog if isinstance(t, dict)]
            groups = {t.get('staff_id'): t.get('category') for t in catalog}
            order = {c: i for i, c in reversed(list(enumerate(t.get('category') for t in catalog)))}
            rows = [dict(id=e['id'], name=e['name'], role=e['role'], available=e['state'] == 'available',
                         category=groups.get(e['id']) or 'Your specialists') for e in staff.roster(self.root)]
            return sorted(rows, key=lambda r: (order.get(r['category'], len(order)), r['name']))

    def team(self):
        """Staff → AI (Praxis, 11-03): the native leads and staff, read from the same homes sprints hire from.
        Read-only; work state is shown as the homes record it, never reset."""
        from aether.tasker import staff
        from aether.overseer.homes import list_heavyweights
        rows = self._staff()
        roster = {e['id']: e for e in staff.roster(self.root)}
        for row in rows:
            entry = roster.get(row['id'], {})
            state = entry.get('state', '')
            row.update(sprint=state[3:] if state.startswith('on:') else None, summary=entry.get('resume', ''),
                       home=str(staff.staff_root(self.root) / row['id']))
        leads = []
        for mind in list_heavyweights(self.root):
            prepared = read_json(Path(mind['home']) / 'PREPARATION.json', {}).get('source') or {}
            leads.append(dict(id=mind['id'], name=mind['name'], available=mind['available'], sprints=mind['sprints'],
                              home=mind['home'], intact=bool(mind['integrity'].get('ok')),
                              deployment=mind.get('deployment'), note=prepared.get('qualification')))
        return dict(leads=leads, staff=rows, missing_staff=self._missing_staff())

    def school(self):
        """Teach → From the School (11-05): the School's catalog as the staff's teachframe_list reads it, so the
        operator sees what the AI staff are taught. Read-only; never a workspace draft, never memory."""
        from aether.knowledge.school import DEFAULT_CATALOG_PATH, load_catalog
        from aether.knowledge.teachframe import metadata
        entries = {e.get('name'): e for e in read_json(DEFAULT_CATALOG_PATH, {}).get('entries', [])}
        rows = []
        for frame in load_catalog().values():
            entry = entries.get(frame.name, {})
            meta = metadata(frame.path.read_text(encoding='utf-8')) if frame.state == 'CURRICULUM' else {}
            rows.append(dict(name=frame.name, state=frame.state, school=frame.path.parent.name,
                             title=frame.title or frame.name, version=meta.get('VERSION', ''), date=meta.get('DATE', ''),
                             audience=frame.audience, prevents=frame.prevents, prerequisites=list(frame.prerequisites),
                             recommended_for=entry.get('recommended_for') or [], qualification=entry.get('qualification', '')))
        return dict(frames=rows)

    def school_frame(self, name):
        """One School frame's full text, by its catalog name only (never a path)."""
        from aether.knowledge.school import load_catalog
        frame = load_catalog().get(name or '')
        if frame is None:
            raise ValueError('No School frame has that name')
        return dict(name=frame.name, text=frame.path.read_text(encoding='utf-8'))

    def _missing_staff(self):
        """Shipped standard staff not in this roster (an install seeded before the catalog grew)."""
        from aether.tasker import staff
        catalog = read_json(staff.PRODUCT_STAFF / 'catalog.json', {}).get('templates') or []
        known = {e['id'] for e in staff.roster(self.root)}
        return [dict(id=t['staff_id'], name=t.get('name') or t['staff_id']) for t in catalog
                if isinstance(t, dict) and t.get('staff_id') and t['staff_id'] not in known]

    def add_standard_staff(self):
        """The operator adds the missing shipped staff: only those, never over an existing home (Michel, 11-03)."""
        from aether.tasker import staff
        with self.lock:
            missing = [m['id'] for m in self._missing_staff()]
            if not missing:
                raise ValueError('Every standard staff member is already in your roster')
            added = staff.seed_staff(self.root, staff.PRODUCT_STAFF, only=missing)
        return dict(self.options(), added=added)

    def specialist_preview(self, body, author):
        """Create a specialist (Michel, 11-05): the identity a description would make, written nowhere."""
        from aether.tasker import specialist
        self._staff()
        made = specialist.preview(self.root, body, author)
        return dict(name=made['name'], id=made['id'], role=made['role'], file=made['file'], text=made['text'])

    def create_specialist(self, body, author):
        """Add the described specialist to the Staff Home, fresh, so sprints can hire it like the shipped staff."""
        from aether.tasker import specialist
        self._staff()   # the shipped staff are seeded before the first addition, never skipped by it
        with self.lock:
            made = specialist.create(self.root, body, author)
        return dict(created=dict(id=made['id'], name=made['name'], role=made['role'], file=made['file']), team=self.team())

    def options(self):
        from aether.overseer.homes import list_heavyweights
        doc = read_json(self.profiles, {})
        profiles = doc.get('profiles') or doc.get('document', {}).get('profiles') or {}
        # The installed setup placeholder reaches no model; Chat and readiness skip it the same way.
        profiles = {k: v for k, v in profiles.items()
                    if isinstance(v, dict) and v.get('requestedApiIdentifier') not in (None, '', 'not-configured')}
        return dict(minds=[dict(id=m['id'], name=m.get('name', m['id']), available=m['available'])
                           for m in list_heavyweights(self.root)],
                    staff=self._staff(), missing_staff=self._missing_staff(), roadmaps=self._roadmap_choices(),
                    deployments=sorted(profiles),
                    caps=DEFAULT_CAPS, halted=self.halted())

    def _roadmap_choices(self):
        """Roadmaps a sprint can start in (not Completed or Aborted)."""
        if self.roadmaps is None:
            return []
        try:
            rows = self.roadmaps.roadmap_rows()
        except (OSError, ValueError):
            return []
        busy = self._steps_in_progress()
        return [dict(key=key, name=row['name'], status=row['stage'],
                     steps=[dict(id=s['id'], name=s['name'], description=s['description'])
                            for s in self._roadmap_sprints(key) if s['status'] != 'Closed' and (key, s['id']) not in busy])
                for key, row in rows.items() if not row['done']]

    def _roadmap_sprints(self, key):
        """A roadmap's own sprints, in order, read from its store; a run's outcome record is marked as such."""
        from aether.roadmaps.tracker.tracker_store import RoadmapStore
        try:
            path, rid, _ = self.roadmaps.roadmap(key)
            data, _ = RoadmapStore(path).read()
        except (LookupError, OSError, ValueError):
            return []
        runs = {row.get('sprint_id') for row in self._rows().values()}
        rows = sorted((s for s in data['sprints'] if s.get('roadmapId') == rid), key=lambda s: s.get('sequence', 0))
        return [dict(id=s['id'], name=s.get('name') or s['id'], description=s.get('description', ''),
                     next=s.get('next', ''), status=s.get('status', 'Planned'), owner=s.get('owner', ''),
                     updated=s.get('updated', ''), sequence=s.get('sequence', 0), run_record=s['id'] in runs)
                for s in rows]

    def _steps_in_progress(self):
        """(roadmap key, planned sprint id) pairs that already have a sprint not yet ended."""
        busy = set()
        for project, row in self._rows().items():
            try:
                _, sprint = self._bound(project)
            except (ValueError, OSError, KeyError, TypeError):
                continue
            record = sprint.record()
            state = read_json(sprint.root / 'timer/gui-run.json', {}).get('state')
            if record.get('roadmap_sprint_id') and state not in ('ended', 'stopped'):
                busy.add((record.get('roadmap_key'), record['roadmap_sprint_id']))
        return busy

    def plan(self):
        """Active Sprints (Michel, 11-03): every roadmap with its own sprints, for joining with the runs."""
        if self.roadmaps is None:
            return dict(roadmaps=[])
        try:
            rows = self.roadmaps.roadmap_rows()
        except (OSError, ValueError):
            return dict(roadmaps=[])
        return dict(roadmaps=[dict(key=key, name=row['name'], status=row['stage'], done=row['done'],
                                   sprints=self._roadmap_sprints(key)) for key, row in rows.items()])

    def create_lead(self, body):
        """Create a fresh lead home from the product template; an existing home is never touched."""
        from aether.overseer.homes import create_heavyweight_home
        if not isinstance(body, dict) or set(body) - {'name'}:
            raise ValueError('Send only the lead name')
        name = body.get('name')
        if not isinstance(name, str) or not name.strip() or len(name) > 60:
            raise ValueError('Give the lead a name of at most 60 characters')
        agent_id = re.sub(r'[^a-z0-9]+', '-', name.strip().lower()).strip('-')[:40] or 'lead'
        try:
            create_heavyweight_home(self.root, agent_id, name.strip())
        except FileExistsError:
            raise ValueError(f'A lead with the ID {agent_id} already exists; choose it from the list') from None
        return self.options()

    def create(self, body):
        from aether.overseer.homes import home_path
        from aether.tasker.model import describe_deployment
        from aether.tasker.output_checks import digest
        allowed = {'request_id', 'name', 'goals', 'overseer', 'deployment', 'contracts', 'caps', 'roadmap', 'roadmap_step'}
        if set(body) - allowed:
            raise ValueError('Use project fields and IDs; paths are resolved by the server')
        key = body.get('request_id')
        if not isinstance(key, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,80}', key):
            raise ValueError('A stable submission ID is required')
        fingerprint = digest(body)
        project = 'native-' + hashlib.sha256(key.encode()).hexdigest()[:24]
        with self.lock, file_lock(self.root / '.gui-projects.lock'):
            rows = self._rows()
            if project in rows:
                if rows[project]['request_sha256'] != fingerprint:
                    raise ValueError('This submission ID already belongs to different work')
                return self.read(project)
            if self.halted():
                raise ValueError('New work is stopped. Enable requests before preparing a project.')
            name, goals = body.get('name', ''), body.get('goals', '')
            if not isinstance(name, str) or not name.strip() or len(name) > 120:
                raise ValueError('Give the project a name of at most 120 characters')
            if not isinstance(goals, str) or not goals.strip():
                raise ValueError('Write the work to deliver')
            contracts = verification.validate(body.get('contracts'))
            # The first GUI slice accepts inline contracts, never local evidence paths.
            if any(c['evidence'] for c in contracts.values()):
                raise ValueError('File evidence references require the command-line workflow in this preview')
            overseer, deployment = body.get('overseer'), body.get('deployment')
            if not (home_path(self.root, overseer) / 'participant.json').is_file():
                raise ValueError('Choose an existing native Tasker lead')
            describe_deployment(self.profiles, deployment)
            roadmap = None
            if body.get('roadmap') is not None:
                if self.roadmaps is None:
                    raise ValueError('Roadmaps are unavailable on this host')
                try:
                    path, rid, rname = self.roadmaps.roadmap(body['roadmap'])
                except LookupError as exc:
                    raise ValueError(str(exc).strip('"\'')) from None
                roadmap = dict(roadmap_path=str(path), roadmap_id=rid, roadmap_key=body['roadmap'], roadmap_name=rname)
                step = body.get('roadmap_step')
                if step is not None:
                    # Michel, 11-03: a sprint can be the work of one planned sprint in its roadmap (GUI binding).
                    found = next((s for s in self._roadmap_sprints(body['roadmap']) if s['id'] == step), None)
                    if found is None:
                        raise ValueError('That planned sprint is no longer in this roadmap; choose another')
                    roadmap.update(roadmap_sprint_id=found['id'], roadmap_sprint_name=found['name'])
            elif body.get('roadmap_step') is not None:
                raise ValueError('Choose the roadmap of that planned sprint')
            self._staff()
            caps = body.get('caps', {})
            if not isinstance(caps, dict) or set(caps) - set(DEFAULT_CAPS):
                raise ValueError('Unknown budget setting')
            for key, value in caps.items():
                if (type(value) not in (int, float) or not math.isfinite(value) or value <= 0
                        or key != 'max_cost_per_sprint' and type(value) is not int):
                    raise ValueError('Budgets must be positive; turn and parallel limits must be integers')
            # A saved intent prevents a retry from making a second sprint after a crash.
            rows[project] = dict(id=project, name=name.strip(), request_sha256=fingerprint,
                                 sprint_id=None, created_at=now())
            write_json(self.index, rows)
            sprint = new_sprint(self.root, goals, overseer, project, caps, name=name, contracts=contracts)
            sprint.update(lambda d: d.update(deployments={'*': deployment}, profiles_path=str(self.profiles)))
            if roadmap:
                # The lead's roadmap_update records this sprint's outcome in the roadmap it started from.
                sprint.update(lambda d: d.update(roadmap))
            rows[project]['sprint_id'] = sprint.id
            write_json(self.index, rows)
        return self.read(project)

    def read(self, project):
        with self.lock:
            row = self._rows().get(project)
            if row and not row.get('sprint_id'):
                raise ValueError('Project preparation was interrupted; preserve it for recovery, do not resubmit')
            row, sprint = self._bound(project)
            value = status(sprint)
            reviewable = verification.evaluate(sprint, reviews={})
            run = read_json(sprint.root / 'timer/gui-run.json', {})
            worker = self.workers.get(project)
            alive = bool(worker and worker.is_alive())
            phase = ('stopping' if run.get('stop_requested') else 'running') if alive else (
                run.get('state', 'prepared') if run.get('state') != 'running' else 'interrupted')
            open_turns = self._open_turns(sprint) if phase == 'interrupted' else []
            value.update(project_id=project, name=row['name'], execution=phase, created=sprint.record().get('created'),
                         run=run, halted=self.halted(), read_at=now(),
                         can_start=not run and not self.halted(),
                         open_turns=open_turns,
                         can_continue=self._continuable(sprint, phase, run, value, open_turns),
                         can_accept=not alive and reviewable['status'] in ('accepted', 'review_required'),
                         review_tasks=reviewable['tasks'],
                         goals=(sprint.root / 'SPRINT_GOALS.md').read_text(encoding='utf-8'),
                         contracts=verification.contracts(sprint),
                         deployment=sprint.record().get('deployments', {}).get('*'),
                         roadmap=dict(key=sprint.record()['roadmap_key'], name=sprint.record().get('roadmap_name'),
                                      step=dict(id=sprint.record()['roadmap_sprint_id'],
                                                name=sprint.record().get('roadmap_sprint_name'))
                                      if sprint.record().get('roadmap_sprint_id') else None)
                         if sprint.record().get('roadmap_key') else None)
            # Receipts remain authoritative. File downloads accept only declared output IDs.
            value['outputs'] = verification.summary(sprint)
            value.update(self._names(value))
            value['output_files'] = {key: [dict(name=name, ready=(self._folder(sprint, key) / name).is_file())
                                           for name in names] for key, names in value['outputs'].items()}
            result = value['verification']['status']
            if result == 'failed' and any(t.get('status') == 'review_failed'
                                          for t in value['verification'].get('tasks', {}).values()):
                result = 'review_failed'
            value['headline'] = headline(phase, result, terminal_failure=value.get('terminal_failure'),
                                         sprint_state=value.get('state'))
            if value['can_continue']:
                value['headline'] = 'Interrupted between turns; ready to continue'
            elif open_turns and not value.get('terminal_failure'):
                value['headline'] = 'Interrupted mid-turn; kept as it is'
            for decision in value['review_tasks'].values():
                decision['words'] = RESULT_WORDS.get(decision.get('status'), decision.get('status'))
            # Michel, 2026-11-07: extra files are named, not failed; a failed check has a way forward.
            value['extra_outputs'] = verification.extra_outputs(sprint)
            value['failure_words'] = failure_words(value.get('terminal_failure'))
            try:
                value['operator_accepted'] = verification.operator_accepted(sprint)
            except (OSError, ValueError):
                value['operator_accepted'] = None
            value['can_accept_anyway'] = (not alive and bool(run) and phase not in ('prepared', 'running', 'stopping')
                                          and value['operator_accepted'] is None and value.get('contracts') is not None
                                          and reviewable['status'] not in ('accepted', 'review_required'))
            if value['operator_accepted']:
                by = (value['operator_accepted'].get('by') or {}).get('name') or 'an Admin'
                value['headline'] = ('Accepted by ' + by + ' over a failed check' if value['operator_accepted']['current']
                                     else 'Outputs changed after ' + by + ' accepted them anyway')
            return value

    def accept_anyway(self, project, reason, operator):
        """An Admin proceeds over a failed check with a reason; recorded as that, never as checks passed."""
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValueError('Say in a few words why you accept it anyway (up to 2,000 characters)')
        with self.lock:
            _, sprint = self._bound(project)
            worker = self.workers.get(project)
            if worker and worker.is_alive():
                raise ValueError('Wait for the work to stop first')
            if not read_json(sprint.root / 'timer/gui-run.json', {}):
                raise ValueError('Start the work before accepting it')
            result = verification.evaluate(sprint)
            if result['status'] in ('accepted', 'review_required', 'legacy_unverified'):
                raise ValueError('There is no failed check to accept over; use Your review')
            verification.accept_anyway(sprint, result, reason.strip(), operator)
        return self.read(project)

    def _names(self, value):
        """The page shows people by name; the records keep their IDs."""
        from aether.tasker import staff
        from aether.overseer.homes import home_path
        try:
            lead = read_json(home_path(self.root, value.get('overseer')) / 'participant.json', {}).get('name')
        except ValueError:
            lead = None
        names = {e['id']: e['name'] for e in staff.roster(self.root)}
        return dict(lead_name=lead or value.get('overseer'),
                    staff_names=[names.get(s, s) for s in value.get('staff') or []])

    @staticmethod
    def _folder(sprint, task):
        return sprint.root / 'outputs' if task == 'solo' else sprint.outputs(int(task))

    def listing(self):
        rows = []
        for project, row in self._rows().items():
            try:
                rows.append(self.read(project))
            except (ValueError, OSError, TypeError) as exc:
                rows.append(dict(project_id=project, name=row['name'], execution='unavailable', error=str(exc),
                                 headline=RUN_WORDS['unavailable']))
        return rows

    def start(self, project, actor):
        with self.lock, file_lock(self.root / '.gui-start.lock'):
            _, sprint = self._bound(project)
            path = sprint.root / 'timer/gui-run.json'
            if read_json(path):
                return self.read(project)  # Includes stopped/interrupted: never implicit resume.
            if self.halted():
                raise ValueError('New work is stopped. Enable requests before starting.')
            verification.contracts(sprint)
            write_json(path, dict(state='running', started_at=now(), operator=actor))
            self._worker(project, sprint, actor)
            return self.read(project)

    def _worker(self, project, sprint, actor):
        worker = threading.Thread(target=self._run, args=(project, sprint, actor), daemon=True,
                                  name='native-' + sprint.id)
        self.workers[project] = worker
        worker.start()

    @staticmethod
    def _open_turns(sprint):
        """Turns the Timer launched and never collected; their minds may have acted (timer/active)."""
        return sorted(read_json(p, {}).get('mind') or p.stem for p in (sprint.root / 'timer/active').glob('*.json'))

    def _continuable(self, sprint, phase, run, value, open_turns):
        # Only where Continue means what it says: the app closed between turns, nothing was mid-way, there is no
        # final stop, and the operator had not asked it to stop. A mid-turn case is the Timer's to refuse.
        return (phase == 'interrupted' and not open_turns and not value.get('terminal_failure')
                and not run.get('stop_requested') and not (sprint.root / 'timer/STOP').exists() and not self.halted())

    def continue_interrupted(self, project, actor):
        """The operator continues work whose process is gone, between turns; the same Timer carries on from its
        records. Never on read, never for a turn left mid-way, never over a final stop."""
        with self.lock, file_lock(self.root / '.gui-start.lock'):
            value = self.read(project)
            if value['open_turns']:
                minds = ['the lead' if m == 'overseer' else m for m in value['open_turns']]
                raise ValueError(' and '.join(minds).capitalize() + ' was in the middle of a turn when the app closed; '
                                 'continuing could repeat what it did, so the work is kept as it is')
            if not value['can_continue']:
                raise ValueError('This sprint cannot continue here: ' + value['headline'])
            _, sprint = self._bound(project)
            path = sprint.root / 'timer/gui-run.json'
            record = read_json(path)
            record.setdefault('continued', []).append(dict(at=now(), operator=actor, after=record.get('error')))
            for key in ('error', 'ended_at', 'outcome'):
                record.pop(key, None)
            record['state'] = 'running'
            write_json(path, record)
            self._worker(project, sprint, actor)
            return self.read(project)

    def _run(self, project, sprint, actor):
        from aether.tasker.timer import Timer
        from aether.keys.runtime_keys import as_operator
        from aether.keys.redaction import redact
        path = sprint.root / 'timer/gui-run.json'
        timer = None
        try:
            if self.runners is None:
                from aether.overseer.turn import run_overseer_turn
                from aether.tasker.staff_turn import run_staff_turn
                runners = (run_overseer_turn, run_staff_turn)
            else:
                runners = self.runners
            def scoped(runner):
                def run(*args, **kwargs):
                    with as_operator(actor):
                        return runner(*args, **kwargs)
                return run
            timer = Timer(sprint, overseer_runner=scoped(runners[0]), staff_runner=scoped(runners[1]),
                          interval=self.interval)
            timer.start()
            why = timer.run()  # run closes/joins the Timer before an ended receipt is written.
            with self.lock:
                record = read_json(path)
                record.update(state='stopped' if record.get('stop_requested') else 'ended',
                              ended_at=now(), outcome=str(why))
                write_json(path, record)
        except Exception as exc:
            if timer is not None and timer.lock is not None:
                timer.close()
            with self.lock:
                record = read_json(path)
                record.update(state='interrupted', ended_at=now(), error=redact(str(exc)))
                write_json(path, record)

    def stop(self, project):
        with self.lock:
            _, sprint = self._bound(project)
            path = sprint.root / 'timer/gui-run.json'
            record = read_json(path, {})
            worker = self.workers.get(project)
            (sprint.root / 'timer/STOP').write_bytes(b'')
            record.update(stop_requested=True, stop_requested_at=now())
            if not worker or not worker.is_alive():
                # A lost owner is unconfirmed, not a successful stop.
                record['state'] = 'interrupted' if record.get('state') == 'running' else record.get('state', 'stopped')
            write_json(path, record)
            return self.read(project)

    def resume(self, project):
        """The operator's answer to a pause, as `sprint resume` gives it; never a restart."""
        from aether.tasker.timer import resume
        with self.lock:
            _, sprint = self._bound(project)
            worker = self.workers.get(project)
            if not worker or not worker.is_alive():
                raise ValueError('This work is not running here; it will not restart')
            resume(sprint)
            return self.read(project)

    def raise_budget(self, project, caps):
        """The operator raises a limit, as `sprint caps` does; the Timer reads it at its next tick."""
        names = {'max_cost_per_sprint', 'max_turns_per_sprint'}
        if not isinstance(caps, dict) or not caps or set(caps) - names:
            raise ValueError('Send a new cost limit or turn limit')
        with self.lock:
            _, sprint = self._bound(project)
            current = sprint.record().get('caps', {})
            for key, value in caps.items():
                if (type(value) not in (int, float) or not math.isfinite(value) or value <= 0
                        or key == 'max_turns_per_sprint' and type(value) is not int):
                    raise ValueError('Limits must be positive; the turn limit is a whole number')
                if value < float(current.get(key, 0)):
                    raise ValueError('A raised limit cannot be lower than the current one')
            sprint.update(lambda d: d.setdefault('caps', {}).update(caps))
            return self.read(project)

    def stop_all(self):
        for project in list(self.workers):
            if self.workers[project].is_alive():
                self.stop(project)

    def close(self):
        self.stop_all()
        for worker in self.workers.values():
            worker.join(timeout=30)
        if any(worker.is_alive() for worker in self.workers.values()):
            raise RuntimeError('Native Tasker turns are still stopping; wait before closing Mission Control')

    def accept(self, project, reviews):
        with self.lock:
            _, sprint = self._bound(project)
            worker = self.workers.get(project)
            if worker and worker.is_alive():
                raise ValueError('Wait for execution and agent closure to finish')
            return verification.acceptance(sprint, reviews)

    def output(self, project, task, name):
        from aether.tasker.output_checks import _bytes
        _, sprint = self._bound(project)
        declared = verification.summary(sprint)
        if task not in declared or name not in declared[task]:
            raise ValueError('Choose a declared output')
        return _bytes(self._folder(sprint, task) / name)
