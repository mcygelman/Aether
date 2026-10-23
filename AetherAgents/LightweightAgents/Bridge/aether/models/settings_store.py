"""Runtime-profile settings store (Model Catalog, MOD-05): the model spec sheet on disk.

Moved out of aether.config.model_settings_store on 2026-10-07 (MOD-04): it validates
runtime profiles, which belong to the Model Catalog. The safe-file utilities it uses
(parse_json, writer_lock, revision, utc_now, StaleSettings, SettingsAuditError) stay in
Configuration, where about ten modules share them.

Binding preparation moved to Backends on 2026-10-07 (DeploymentBinding.from_store,
bind_preparation). Operator lookup is injected at construction since 2026-10-08 (item 9):
callers that save inject the Roadmaps operators.reader(projects.json), which keeps the
whole-registry check. Without it, attribution fails closed; this package never imports Roadmaps.
"""
import json
import os
from pathlib import Path
import tempfile
import uuid

from aether.config.context_configuration import ContextConfigurationError, integer, resolve
from aether.config.model_settings_store import (SettingsAuditError, StaleSettings, parse_json,
                                                revision, utc_now, writer_lock)
from aether.models import runtime_profiles


def decode(raw):
    document = parse_json(raw)
    runtime_profiles.validate_registry(document, budget_aware=True, deployment_aware=True)
    return document



class SettingsStore:
    document_decoder = staticmethod(decode)

    def __init__(self, path=runtime_profiles.DEFAULT_REGISTRY_PATH, *, operator_registry_path=None, operators=None):
        """operators: zero-argument callable returning registered identities; None refuses saves."""
        self.path = Path(path).resolve()
        # Saves hold this registry's writer lock so attribution cannot change mid-save.
        self.operator_registry_path = Path(operator_registry_path if operator_registry_path is not None
            else runtime_profiles.DEFAULT_REGISTRY_PATH.parent.parent / 'projects.json').resolve()
        self.operators = operators

    def registered_operators(self):
        """Injected lookup only; fail closed when none was given."""
        if self.operators is None:
            raise ContextConfigurationError('Registered operator identity unavailable; load the existing operator record first.')
        return self.operators()

    def resolve_operator(self, operator_id):
        try:
            if not isinstance(operator_id, str):
                raise ValueError
            identity = str(uuid.UUID(operator_id))
        except (ValueError, TypeError, AttributeError):
            raise ContextConfigurationError('Settings writes require a registered operator UUID') from None
        for actor in self.registered_operators():
            if actor['id'] == identity:
                return actor
        raise ContextConfigurationError('Unknown registered operator ID')

    def read_bytes(self):
        return self.path.read_bytes()

    def read(self):
        raw = self.read_bytes()
        return dict(document=self.document_decoder(raw), revision=revision(raw), path=str(self.path))

    def validate_transition(self, current, document, migrate):
        if migrate:
            transition = (current['schemaVersion'], document['schemaVersion'])
            if migrate is not True or transition not in ((1, 2), (1, 3), (2, 3)):
                raise ContextConfigurationError('Explicit migration must convert legacy profiles to advanced budgets')
            if not set(current['profiles']).issubset(document['profiles']):
                raise ContextConfigurationError('Migration must retain every existing profile')
            if document['schemaVersion'] == 3:
                for name, original in current['profiles'].items():
                    preserved = {k: v for k, v in original.items() if k != 'backend'}
                    converted = {k: v for k, v in document['profiles'][name].items()
                                 if k not in ('adapter_type', 'resource_pool', 'credential_ref')}
                    # A neutral v3 control may be explicit null on a GUI draft.
                    # Enabling/disabling thinking is a separate operating edit.
                    if converted.get('thinking') is None:
                        converted.pop('thinking', None)
                    if preserved != converted:
                        raise ContextConfigurationError('Deployment migration must preserve existing operating fields and budgets')
        elif current['schemaVersion'] != document['schemaVersion']:
            raise ContextConfigurationError('Changing launch format requires explicit profile migration')

    def append_audit(self, event):
        path = self.path.with_name(self.path.name + '.audit.jsonl')
        raw = (json.dumps(event, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
        # Refuse to concatenate onto an interrupted record. No truncation/repair here.
        with path.open('a+b') as handle:
            if handle.tell():
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b'\n':
                    raise OSError('Settings audit has an incomplete record')
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())

    def save(self, document, expected_revision, *, operator, migrate=False):
        if not isinstance(expected_revision, str) or len(expected_revision) != 64:
            raise StaleSettings('A loaded settings revision is required; reload first')
        payload = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode('utf-8')
        document = self.document_decoder(payload)  # Validate the exact detached bytes that will be saved.
        # Use the same project-registry writer lock as operator edits. Retain it
        # through audit completion so cooperating renames cannot change attribution.
        with writer_lock(self.operator_registry_path.with_name(self.operator_registry_path.name+'.lock')), \
                writer_lock(self.path.with_name(self.path.name+'.lock')):
            actor = self.resolve_operator(operator)
            current = self.read()
            if current['revision'] != expected_revision:
                raise StaleSettings('Settings changed since you loaded them. Reload before saving.')
            self.validate_transition(current['document'], document, migrate)
            action = ('migration' if migrate else 'default_change'
                      if current['document'].get('default_profile_id') != document.get('default_profile_id')
                      else 'save')
            event = dict(event_id=uuid.uuid4().hex, operator=actor,
                         old_revision=current['revision'], new_revision=revision(payload), action=action)
            try:
                self.append_audit(dict(event, time=utc_now(), state='intent'))
            except OSError:
                raise SettingsAuditError(replaced=False, new_revision=revision(payload)) from None
            temp_path = None
            replaced = False
            try:
                fd, name = tempfile.mkstemp(prefix=self.path.name+'.', suffix='.tmp', dir=self.path.parent)
                temp_path = Path(name)
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(payload); handle.flush(); os.fsync(handle.fileno())
                # Detect non-cooperating editor changes up to the replacement boundary.
                if revision(self.read_bytes()) != expected_revision:
                    raise StaleSettings('Settings changed during save. Reload before saving.')
                os.replace(temp_path, self.path)
                replaced = True
                temp_path = None
                saved = self.read()
                if saved['revision'] != revision(payload):
                    raise StaleSettings('Settings were replaced but changed again; reload to inspect the current file.')
                try:
                    self.append_audit(dict(event, time=utc_now(), state='committed'))
                except OSError:
                    raise SettingsAuditError(replaced=True, new_revision=revision(payload)) from None
                return saved
            except SettingsAuditError:
                raise
            except Exception:
                try:
                    self.append_audit(dict(event, time=utc_now(), state='unconfirmed' if replaced else 'aborted'))
                except OSError:
                    raise SettingsAuditError(replaced=replaced, new_revision=revision(payload)) from None
                raise
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)


def preview(document, profile_name, request_tokens=None, measurement='estimate'):
    profiles = runtime_profiles.validate_registry(document, budget_aware=True, deployment_aware=True)
    if profile_name not in profiles:
        raise ContextConfigurationError('Select an existing model profile')
    selected = profiles[profile_name]
    if request_tokens is not None:
        integer(request_tokens, 'request_tokens')
    if measurement not in ('estimate','tokenizer'):
        raise ContextConfigurationError('Choose estimate or tokenizer measurement')
    if selected.context_budget is None:
        return dict(launch_support='Current profile launchers', complete_budget=False,
                    capability_source=selected.capability_source, verified_limits=None,
                    declared_context_tokens=selected.context_window, output_tokens=selected.max_output_tokens,
                    input_allowance=None, request=None,
                    note='This launch path uses its existing reserve rules. Complete-request preview is available for advanced budget profiles.')
    snapshot = resolve(selected)
    return dict(launch_support='Configured packet CLI and foreground Studio with a trusted counter adapter; durable Studio and Mission Control dispatch migration remain',
                complete_budget=True, capability_source=selected.capability_source,
                verified_limits=None, declared_context_tokens=selected.context_window,
                output_tokens=snapshot.max_output_tokens, input_allowance=snapshot.ledger().usable_input_budget,
                settings_revision=snapshot.revision, provenance=dict(snapshot.provenance),
                token_count_policy=snapshot.token_count_policy.to_mapping(),
                request=(snapshot.preview(request_tokens, measurement=measurement)
                         if request_tokens is not None else None),
                note='Input allowance excludes output, future tool results and safety margin. No endpoint or tokenizer was contacted by this screen.')
