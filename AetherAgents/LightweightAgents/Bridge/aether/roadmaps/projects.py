"""Project registry and per-project staff hiring for Mission Control."""
from __future__ import annotations

import hashlib
import json
import os
import pickle
import re
import shutil
import threading
import time
import tempfile
from functools import wraps, lru_cache
from dataclasses import dataclass, field
from pathlib import Path

from aether.integrity import canon
from aether.agent.assembler import assemble, section_body
from aether.agent.briefcase import Header, Section, parse_file, verify
from aether.config.model_settings_store import writer_lock


HOME_DIRS = ("staff", "journal", "missions", "board", "reflections")


def registry_locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call


def validate_framing(value, project=None):
    if value is None:
        return
    if not isinstance(value, dict) or value.get('schema_version') not in ('aether-project-framing-1.0.0','aether-project-framing-1.1.0'):
        raise ValueError('Unsupported project framing schema; inspect or migrate it without rewriting the record.')
    rulings=value.get('schema_version')=='aether-project-framing-1.1.0'
    if rulings and (value.get('plan_rulings') is None or 'approved_plan' not in value):
        raise ValueError('Version1.1 requires explicit plan ruling history and current-plan projection.')
    if not rulings and (value.get('plan_rulings') is not None or value.get('approved_plan') is not None):
        raise ValueError('Plan rulings require the explicit version1.1 contract.')
    if value.get('state') not in (('HEAVYWEIGHT_SELECTED','PLAN_APPROVED') if rulings else ('WMC_SELECTED','HEAVYWEIGHT_SELECTED')):
        raise ValueError('This version supports project setup and lead association only.')
    if value['state']=='WMC_SELECTED' and value.get('heavyweight') is not None:
        raise ValueError('A setup project cannot already have a heavyweight.')
    if value['state']=='WMC_SELECTED' and value.get('planning') is not None:
        raise ValueError('Planning preparation requires an associated heavyweight.')
    if value['state']=='WMC_SELECTED' and value.get('lead_rebindings') is not None:
        raise ValueError('Lead rebindings require an original associated identity.')
    for name in ('summary','updated_at','next_action','creation_request_id','creation_request_sha256'):
        if not isinstance(value.get(name),str) or not value[name].strip():
            raise ValueError(f'Invalid project framing field: {name}')
    if re.fullmatch('[0-9a-f]{64}',value['creation_request_sha256']) is None:
        raise ValueError('Invalid project creation integrity reference.')
    for name in ('proposals','approvals','staff','sprints','evidence','wmc_history','events'):
        if not isinstance(value.get(name),list):
            raise ValueError(f'Invalid project framing collection: {name}')
    if any(value[name] for name in (('staff','sprints','evidence') if rulings else ('proposals','staff','sprints','evidence'))) or (value['state']=='WMC_SELECTED' and value['approvals']):
        raise ValueError('A setup project cannot claim planning, staff or execution authority.')
    wmc=value.get('wmc')
    if not isinstance(wmc,dict) or any(not isinstance(wmc.get(k),str) or not wmc[k] for k in ('id','name','version','source','sha256','catalog_sha256')):
        raise ValueError('Incomplete project world-model provenance.')
    if any(re.fullmatch('[0-9a-f]{64}',wmc[k]) is None for k in ('sha256','catalog_sha256')):
        raise ValueError('Invalid world-model integrity reference.')
    if value['state'] in ('HEAVYWEIGHT_SELECTED','PLAN_APPROVED'):
        from aether.roadmaps.project_engagement_contract import validate_association
        validate_association(value, project)


@dataclass
class HireRecord:
    template: str
    instance_agent_id: str
    briefcase_path: str
    hired_at: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Project:
    id: str
    name: str
    root: str
    created_at: str
    hired: list[HireRecord] = field(default_factory=list)
    framing: dict | None = None
    extra: dict = field(default_factory=dict, repr=False)

    @property
    def root_path(self) -> Path:
        return Path(self.root).resolve()

    @property
    def home(self) -> Path:
        return self.root_path / ".aether"

    @property
    def staff_dir(self) -> Path:
        return self.home / "staff"

    @property
    def journal_dir(self) -> Path:
        return self.home / "journal"

    @property
    def missions_dir(self) -> Path:
        return self.home / "missions"

    @property
    def board_dir(self) -> Path:
        return self.home / "board"

    @property
    def reflections_dir(self) -> Path:
        return self.home / "reflections"

    def to_dict(self) -> dict:
        result = {**self.extra,
            "id": self.id,
            "name": self.name,
            "root": self.root,
            "created_at": self.created_at,
            "hired": [h.to_dict() for h in self.hired],
        }
        if self.framing is not None:
            result['framing'] = self.framing
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        validate_framing(data.get('framing'), data)
        return cls(
            id=data["id"],
            name=data["name"],
            root=data["root"],
            created_at=data.get("created_at", ""),
            hired=[HireRecord(**h) for h in data.get("hired", [])],
            framing=data.get('framing'),
            extra={k:v for k,v in data.items() if k not in ('id','name','root','created_at','hired','framing')},
        )


_REGISTRY_CACHE_BYTES = 4 * 1024 * 1024


def _decode_registry(raw):
    data = json.loads(raw)
    return (data.get('active', ''), [Project.from_dict(p) for p in data.get('projects', [])],
            {k: v for k, v in data.items() if k not in ('active', 'projects')})


@lru_cache(maxsize=4)
def _retained_registry(raw):
    # Only self-contained historical validation belongs here. The retained value is
    # an immutable pickle of the decoded projection: every load unpickles a fresh,
    # unshared copy (equivalent to the former deepcopy, and faster), and the exact
    # current file is read before every hit.
    return pickle.dumps(_decode_registry(raw), protocol=pickle.HIGHEST_PROTOCOL)


def _self_contained_registry(data):
    pending = [data]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            schema = value.get('schema')
            if isinstance(schema, str) and schema.startswith('aether-minder-first-session-preparation-'):
                # FirstSessionPreparation.validate_body can resolve today's
                # default token policy. Do not cache that external dependency.
                return False
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return True


@lru_cache(maxsize=4)
def _self_contained_registry_bytes(raw):
    # This classifies only exact JSON bytes, never current external authority.
    # False still causes a fresh decode/validation on every load.
    return _self_contained_registry(json.loads(raw))


class ProjectRegistry:
    def __init__(self, lightweight_dir: str | Path, registry_path: str | Path | None = None):
        self.lightweight_dir = Path(lightweight_dir).resolve()
        self.registry_path = Path(registry_path or self.lightweight_dir / "projects.json").resolve()
        self.active = ""
        self.projects: list[Project] = []
        self._lock = threading.RLock()
        self._raw = None
        self._extra = {}

    @classmethod
    def load_or_migrate(cls, lightweight_dir: str | Path, registry_path: str | Path | None = None) -> "ProjectRegistry":
        reg = cls(lightweight_dir, registry_path)
        if reg.registry_path.is_file():
            reg.load()
            for project in reg.projects:
                ensure_home(project)
            return reg
        reg.create_default_project()
        reg.migrate_bridge_journal_to_default()
        reg.save()
        return reg

    @registry_locked
    def load(self) -> None:
        raw = self.registry_path.read_bytes()
        if len(raw) <= _REGISTRY_CACHE_BYTES and _self_contained_registry_bytes(raw):
            self.active, self.projects, self._extra = pickle.loads(_retained_registry(raw))
        else:
            self.active, self.projects, self._extra = _decode_registry(raw)
        self._raw = raw

    @registry_locked
    def save(self) -> None:
        for project in self.projects: validate_framing(project.framing, project.to_dict())
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        data = {**self._extra, "active": self.active, "projects": [p.to_dict() for p in self.projects]}
        payload = (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode('utf-8')
        def current():
            return self.registry_path.read_bytes() if self.registry_path.exists() else None
        with self._lock, writer_lock(self.registry_path.with_name(self.registry_path.name+'.lock')):
            if current() != self._raw:
                raise ValueError('Project registry changed; reload before saving.')
            fd, name = tempfile.mkstemp(prefix=self.registry_path.name+'.', suffix='.tmp', dir=self.registry_path.parent)
            staged = Path(name)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(payload); stream.flush(); os.fsync(stream.fileno())
                if current() != self._raw:
                    raise ValueError('Project registry changed during save; reload before saving.')
                os.replace(staged, self.registry_path)
                self._raw = payload
            finally:
                staged.unlink(missing_ok=True)

    def create_default_project(self) -> Project:
        root = self.lightweight_dir / "Projects" / "default"
        project = self.create_project("Default", root, project_id="default", select=True, save=False)
        return project

    @registry_locked
    def create_project(
        self,
        name: str,
        root: str | Path,
        project_id: str | None = None,
        select: bool = True,
        save: bool = True,
    ) -> Project:
        target_root = Path(root).resolve()
        for other in self.projects:
            if other.framing is not None and (target_root.is_relative_to(other.root_path) or other.root_path.is_relative_to(target_root)):
                raise ValueError('That folder belongs to a framed project; it cannot be adopted through legacy creation.')
        pid = project_id or slugify(name)
        existing = {p.id for p in self.projects}
        base = pid
        n = 2
        while pid in existing:
            pid = f"{base}-{n}"
            n += 1
        project = Project(
            id=pid,
            name=name.strip() or pid,
            root=str(Path(root).resolve()),
            created_at=timestamp(),
            hired=[],
        )
        ensure_home(project)
        self.projects.append(project)
        if select or not self.active:
            self.active = project.id
        if save:
            self.save()
        return project

    def list_projects(self) -> list[dict]:
        return [p.to_dict() for p in self.projects]

    def get(self, project_id: str) -> Project:
        for project in self.projects:
            if project.id == project_id:
                return project
        raise KeyError(f"project not found: {project_id}")

    def active_project(self) -> Project:
        if not self.active and self.projects:
            self.active = self.projects[0].id
        return self.get(self.active)

    def select_active(self, project_id: str) -> Project:
        with self._lock:
            project = self.get(project_id)
            self.active = project.id
            self.save()
            return project

    def remove_registry_entry(self, project_id: str) -> Project:
        """Remove an unused project from the registry without deleting its directory."""
        with self._lock:
            project = self.get(project_id)
            if project.id == "default":
                raise ValueError("The Default project cannot be removed.")
            if project.id == self.active:
                raise ValueError("Select another project before removing this registry entry.")
            if project.hired:
                raise ValueError("This project has hired agents and is not an empty scratch project.")
            root = project.root_path
            if root.exists():
                if root.is_symlink():
                    raise ValueError("A linked project root is ambiguous and cannot be removed.")
                files = [p for p in root.rglob("*") if p.is_file() or p.is_symlink()]
                if files:
                    raise ValueError("This project contains files or evidence and cannot be removed from the registry.")
            self.projects = [p for p in self.projects if p.id != project.id]
            self.save()
            return project

    @registry_locked
    def record_hire(self, project: Project, record: HireRecord) -> None:
        project.hired = [h for h in project.hired if not (h.template == record.template and h.instance_agent_id == record.instance_agent_id)]
        project.hired.append(record)
        self.save()

    @registry_locked
    def hire_template(self, template: str, project_id: str | None = None) -> HireRecord:
        project = self.get(project_id) if project_id else self.active_project()
        if project.framing is not None:
            raise ValueError('This project awaits governed heavyweight selection; legacy template hiring is unavailable.')
        record = hire_template_into_project(self.lightweight_dir / "Briefcases", project, template)
        self.record_hire(project, record)
        return record

    def migrate_bridge_journal_to_default(self) -> None:
        project = self.active_project()
        source = self.lightweight_dir / "Bridge" / "bridge_journal"
        if not source.is_dir():
            return
        before = file_hashes(source)
        if not before:
            return
        for rel in before:
            src = source / rel
            dst = project.journal_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        after = file_hashes(project.journal_dir)
        missing = {rel: sha for rel, sha in before.items() if after.get(rel) != sha}
        if missing:
            raise ValueError(f"journal migration hash verification failed for: {sorted(missing)}")


def ensure_home(project: Project) -> None:
    project.root_path.mkdir(parents=True, exist_ok=True)
    for name in HOME_DIRS:
        (project.home / name).mkdir(parents=True, exist_ok=True)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or f"project-{int(time.time())}"


def timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def file_hashes(root: Path) -> dict[str, str]:
    out = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def hire_template_into_project(pool_dir: Path, project: Project, template: str) -> HireRecord:
    name = canonical_template_name(pool_dir, template)
    source = pool_dir / name / f"{name}_Briefcase.txt"
    if not source.is_file():
        raise FileNotFoundError(f"template briefcase not found: {source}")
    bc = parse_file(source)
    sections = []
    instance_id = f"{name}@{project.id}"
    hire_time = timestamp()
    for section in bc.sections:
        body = section.body
        if section.label.endswith("IDENTITY"):
            body = rewrite_identity_for_instance(body, name, instance_id, project, hire_time)
        body = section_body(body)
        sha = canon.canon_sha(body)
        sections.append(Section(label=section.label, body=body, seal=sha[:6], manifest_sha=sha))
    header = Header(
        format=bc.header.format,
        agent=instance_id.upper(),
        parent_core=bc.header.parent_core,
        task=bc.header.task,
        sprint=bc.header.sprint,
        part=bc.header.part,
        encoding=bc.header.encoding,
        assembled=hire_time + " BY BRIDGE PROJECT HIRE",
        est_tokens=0,
    )
    raw = assemble(header, sections)
    out = project.staff_dir / name / f"{name}_Briefcase.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as f:
        f.write(raw)
    rep = verify(parse_file(out))
    if not rep.ok:
        raise ValueError(f"hired briefcase failed verify: {rep.failures}")
    return HireRecord(template=name, instance_agent_id=instance_id, briefcase_path=str(out), hired_at=hire_time)


def canonical_template_name(pool_dir: Path, template: str) -> str:
    needle = template.strip()
    if not needle:
        raise ValueError("template is required")
    for path in pool_dir.iterdir():
        if path.is_dir() and path.name.lower() == needle.lower():
            return path.name
    raise KeyError(f"template not found: {template}")


def rewrite_identity_for_instance(body: str, name: str, instance_id: str, project: Project, hire_time: str) -> str:
    text = body
    source_ids = set(re.findall(r"⌜AGENT_ID⌝\s*:=\s*⌞([^⌟]+)⌟", text))
    for source_id in source_ids:
        text = text.replace(source_id, instance_id)
    # A hired identity must not retain any template-global AGENT_ID assertion, including
    # runtime workflow challenges and file metadata.
    text = re.sub(
        r"(⌜AGENT_ID⌝\s*:=\s*)⌞[^⌟]*⌟",
        rf"\1⌞{instance_id}⌟",
        text,
    )
    text = replace_assert_value(text, "FILE_ID", f"AETHER_PROJECT_INSTANCE_{name.upper()}_{project.id.upper()}")
    text = replace_assert_value(text, "LAST_MODIFIED", hire_time)
    text = replace_assert_value(text, "GENERATING_AGENT", "Bridge project hire — S00070")
    text = re.sub(
        r"(➤ ⟪BRIEFCASE::)[^⟫]+(⟫)",
        rf"\1{project.home.as_posix()}/staff/{name}/\2",
        text,
        count=1,
    )
    text = empty_system_2(text, project)
    text = add_hire_change_log(text, instance_id, project, hire_time)
    return text


def replace_assert_value(text: str, field: str, value: str) -> str:
    pattern = re.compile(rf"(\[ASSERT\]\s*⌜{re.escape(field)}⌝\s*:=\s*)⌞[^⌟]*⌟")
    new, count = pattern.subn(rf"\1⌞{value}⌟", text, count=1)
    if count != 1:
        raise ValueError(f"{field} assert not found cleanly")
    return new


def empty_system_2(text: str, project: Project) -> str:
    pattern = re.compile(r"⟪SYSTEM_2_BEGIN⟫.*?⟪SYSTEM_2_END⟫", re.S)
    replacement = f"""⟪SYSTEM_2_BEGIN⟫
⟪TYPE::PERSISTENT_MEMORY_INDEX_BLOCK⟫

[REFLECTION_INDEX] := [
  // Empty at hire: project instance starts without inherited reflections.
]

[TEACHABLE_FRAME_INDEX] := [
  // Empty at hire: project instance starts without inherited promoted memory.
]

[PROJECT_INSTANCE_HOME] := ⌜{project.home.as_posix()}⌟
[CHECKSUM_SHA256_SYSTEM_2] := ⌜UNVERIFIED_RUNTIME⌟
⟪SYSTEM_2_END⟫"""
    new, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise ValueError("SYSTEM_2 block not found cleanly")
    return new


def add_hire_change_log(text: str, instance_id: str, project: Project, hire_time: str) -> str:
    marker = "[ CHANGE_LOG ]"
    line = f"  [S00070] [{hire_time.split('T', 1)[0]}] [Codex] — hired project instance {instance_id} for {project.name}; System 2 initialized empty."
    if marker not in text:
        raise ValueError("CHANGE_LOG block not found cleanly")
    return text.replace(marker, marker + "\n" + line, 1)
