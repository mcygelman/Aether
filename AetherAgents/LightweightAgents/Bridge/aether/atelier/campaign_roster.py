"""Campaign-local role/model overrides. Reads homes; never edits agent identity or defaults."""
from dataclasses import replace
import json
from pathlib import Path

from aether.config.model_settings_store import writer_lock
from aether.integrity.sealbox import atomic_write_bytes
from .agent_registry import digest, metadata, read_json
from .participants import KINDS, Participant
from . import projection


def assignments(root, cid):
    if not projection.exists(root, cid):
        raise ValueError('Choose an existing campaign.')
    path = Path(root) / 'daybooks' / cid / 'roster.json'
    doc = read_json(path, 1024 * 1024) if path.exists() else dict(version=1, members={})
    if doc.get('version') != 1 or not isinstance(doc.get('members'), dict):
        raise ValueError('This campaign roster cannot be read. Its contents were preserved.')
    if not isinstance(doc.get('inherit_defaults', True), bool):
        raise ValueError('This campaign roster has an invalid defaults setting. Its contents were preserved.')
    for aid, row in doc['members'].items():
        if (not isinstance(row, dict) or row.get('agent_id') != aid or row.get('role') not in KINDS
                or not isinstance(row.get('model_profile'), str) or not row['model_profile']):
            raise ValueError('This campaign roster has an invalid assignment. Its contents were preserved.')
    return doc


class CampaignRoster:
    def __init__(self, root, registry, profiles_path, homes=lambda: ()):
        self.root, self.registry = root, registry
        self.profiles_path, self.homes = Path(profiles_path), homes

    def initialize_new(self, cid):
        """Only the new-campaign action opts out of legacy hosting; never migrate old campaigns."""
        root = Path(self.root())
        assignments(root, cid)
        path = root / 'daybooks' / cid / 'roster.json'
        with writer_lock(path.with_suffix('.lock')):
            if not path.exists():
                atomic_write_bytes(path, b'{"version":1,"inherit_defaults":false,"members":{}}\n')

    def defaults(self):
        # Existing Places defaults, or the original standalone homes, stay in effect.
        homes = list(self.homes())
        if not homes:
            homes = sorted((Path(self.root()) / 'homes').glob('*/participant.json'))
            homes = [p.parent for p in homes]
        return [Participant.load(Path(h)) for h in homes]

    def profiles(self):
        raw = read_json(self.profiles_path, 8 * 1024 * 1024)
        profiles = raw.get('profiles', raw.get('document', {}).get('profiles', {}))
        if not isinstance(profiles, dict):
            raise ValueError('Saved models cannot be read. Open Models to check them.')
        # installed_app.unconfigured_profiles writes one placeholder so the file is valid; it reaches no model.
        return [dict(id=key, model=str(value.get('requestedApiIdentifier') or key),
                     usable=value.get('requestedApiIdentifier') != 'not-configured',
                     key=(value.get('credential_ref') or {}).get('kind') if isinstance(value.get('credential_ref'), dict) else None)
                for key, value in sorted(profiles.items()) if isinstance(value, dict)]

    def own_role(self, aid):
        """The role an agent was authored for, offered first when it joins a campaign (never imposed)."""
        try:
            mind = self.local_agent(aid)
        except Exception:        # noqa: BLE001 - a suggestion only: no role is offered when it cannot be read
            return None
        return mind.kind if mind is not None and mind.kind in KINDS else None

    def usage(self):
        """Which campaigns each saved model connection drives: an explicit assignment, or an existing default
        that campaign still inherits. Read-only; a campaign whose roster cannot be read is skipped."""
        root, defaults, result = Path(self.root()), None, {}
        for campaign in projection.commissions(root):
            try:
                doc = assignments(root, campaign['id'])
            except (OSError, ValueError, TypeError):
                continue
            models = {row['model_profile'] for row in doc['members'].values()}
            if doc.get('inherit_defaults', True):
                if defaults is None:
                    try:
                        defaults = {m.id: m.deployment for m in self.defaults()}
                    except (OSError, ValueError, TypeError):
                        defaults = {}
                models |= {model for aid, model in defaults.items() if aid not in doc['members'] and model}
            for model in models:
                result.setdefault(model, []).append(dict(id=campaign['id'], title=campaign['letter'][:80]))
        return result

    def local_agent(self, aid):
        with self.registry.lock:
            row = self.registry._entries().get(aid)
            if not row or row['state'] != 'available':
                return None
            current = metadata(row['home'])
            if current['id'] != aid:
                return None
            home = Path(row['home'])
            if (home / 'participant.json').is_file():
                return Participant.load(home)
            return Participant(aid, 'member', current['name'], home)

    def effective(self, cid, defaults=None):
        doc = assignments(self.root(), cid)
        base = (self.defaults() if defaults is None else defaults) if doc.get('inherit_defaults', True) else []
        result = {m.id: m for m in base}
        saved = doc['members']
        for aid, row in saved.items():
            # Never fall back to another model when a saved assignment is unavailable.
            result.pop(aid, None)
            try:
                mind = self.local_agent(aid)
            except (OSError, ValueError, TypeError):
                mind = None
            if mind:
                mind = replace(mind, kind=row['role'], deployment=row['model_profile'])
                mind.campaign_profiles_path = self.profiles_path
                result[aid] = mind
        roster = {m.id: dict(id=m.id, name=m.name, role=m.kind) for m in result.values()}
        for aid, row in saved.items():
            roster.setdefault(aid, dict(id=aid, name=row.get('name') or aid, role=row['role']))
        # Copy even inherited defaults: turn metadata never mutates a shared participant object.
        snapshots = []
        for mind in result.values():
            snapshot = replace(mind)
            if hasattr(mind, 'campaign_profiles_path'):
                snapshot.campaign_profiles_path = mind.campaign_profiles_path
            snapshot.campaign_members = list(roster.values())
            snapshots.append(snapshot)
        return snapshots

    def member_rows(self, doc, agents, models):
        rows = {m.id: dict(agent_id=m.id, name=m.name, role=m.kind, model_profile=m.deployment,
                          source='Existing default') for m in (self.defaults() if doc.get('inherit_defaults', True) else [])}
        for aid, row in doc['members'].items():
            rows[aid] = dict(row, name=row.get('name') or aid, source='This project')
        by_id = {a['id']: a for a in agents}
        for aid, row in rows.items():
            agent = by_id.get(aid)
            row.update(name=agent['name'] if agent else row['name'],
                       available=bool(agent and agent['status'] == 'available'),
                       model_available=any(m['id'] == row['model_profile'] and m['usable'] for m in models))
        return list(rows.values())

    def staff(self):
        """One read-only directory of current project assignments, separate from historical participation."""
        answer = self.view()
        projects = []
        root = Path(self.root()).resolve()
        for campaign in answer['campaigns']:
            project = dict(id=campaign['id'], title=campaign['letter'], members=[])
            try:
                doc = assignments(root, campaign['id'])
                project.update(members=self.member_rows(doc,answer['agents'],answer['models']),
                               revision=digest([str(root), doc]))
            except (OSError, ValueError, TypeError) as exc:
                project['error'] = 'Project assignments could not be read. Open its Team page to review.'
            projects.append(project)
        answer['projects'] = projects
        return answer

    def view(self, cid=None):
        campaigns = projection.commissions(self.root())
        agents = self.registry.listing()['agents']
        models, model_error = [], ''
        try:
            models = self.profiles()
        except (OSError, ValueError, TypeError) as exc:
            model_error = str(exc)
        from .readiness import _keys
        answer = dict(campaigns=campaigns, agents=[dict(id=a['id'], name=a['name'], status=a['status'],
                                                        role=self.own_role(a['id']) if a['status'] == 'available' else None)
                                                   for a in agents],
                      models=models, model_error=model_error, roles=list(KINDS), members=[], history=[], keys=_keys())
        if not cid:
            return answer
        doc = assignments(self.root(), cid)
        rows = self.member_rows(doc, agents, models)
        from .atelier import Atelier
        atelier = Atelier(Path(self.root()), cid)
        history = projection.people(atelier.daybook.read(), atelier.facts())
        answer.update(commission=cid, revision=digest([str(Path(self.root()).resolve()), doc]),
                      members=rows, history=[p for p in history if p['id'] not in {m['agent_id'] for m in rows}
                      and p.get('kind') not in ('operator', 'host') and p['id'] not in KINDS])
        return answer

    def save(self, body):
        cid = body.get('commission')
        root = Path(self.root()).resolve()
        assignments(root, cid)  # validate before constructing a write path
        path = root / 'daybooks' / cid / 'roster.json'
        with self.registry.lock, writer_lock(path.with_suffix('.lock')):
            doc = assignments(root, cid)
            if body.get('revision') != digest([str(root), doc]):
                raise ValueError('The campaign roster changed. Reload it before saving.')
            aid, role, model = body.get('agent_id'), body.get('role'), body.get('model_profile')
            if body.get('action') == 'remove':
                removed = self._remove(doc, aid)
                atomic_write_bytes(path, (json.dumps(doc, indent=2) + '\n').encode())
                return dict(self.view(cid), removed=removed)
            if not isinstance(aid, str) or role not in KINDS:
                raise ValueError('Choose an agent and its campaign role.')
            mind = self.local_agent(aid)
            if mind is None:
                raise ValueError('This agent is unavailable. Make it available on AI Staff first.')
            if model not in {m['id'] for m in self.profiles() if m['usable']}:
                raise ValueError('Choose a saved driving model. Add its connection on the Models page first.')
            doc['members'][aid] = dict(agent_id=aid, name=mind.name, role=role, model_profile=model)
            atomic_write_bytes(path, (json.dumps(doc, indent=2) + '\n').encode())
        return self.view(cid)

    def _remove(self, doc, aid):
        """Take one agent off this campaign's team (GUI review S5). An explicit assignment is deleted. An inherited
        default ends inheritance for this campaign only: the other defaults are pinned exactly as they were, so nobody
        else's role or model changes. The agent's identity, memory, home default and other campaigns are untouched."""
        defaults = self.defaults() if doc.get('inherit_defaults', True) else []
        rows = {m.id: dict(agent_id=m.id, name=m.name, role=m.kind, model_profile=m.deployment)
                for m in defaults if m.deployment}
        rows.update(doc['members'])  # Explicit assignments win over inherited defaults.
        if aid not in rows:
            raise ValueError('This agent is not on this campaign’s team.')
        removed = rows.pop(aid)
        doc['members'] = rows
        doc['inherit_defaults'] = False
        return removed
