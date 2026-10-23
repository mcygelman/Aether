"""Stateless MOD-16 validation/export. Caller bytes in; verified download bytes out.

Never installs a template, hires an agent, opens a cartridge or calls a model.
The archive retains the entire authored draft, including strategies and attribution;
the executable template contains the identity projection only.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile

from . import draft

MAX_DRAFT_BYTES = 1024 * 1024
MAX_TEMPLATE_BYTES = 2 * MAX_DRAFT_BYTES
VALIDATOR = 'aether-agent-editor-1'


class DraftError(ValueError):
    pass


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise DraftError('Duplicate JSON keys are not accepted.')
        result[key] = value
    return result


def _constant(value):
    raise DraftError('Draft numbers must be finite JSON numbers.')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def validate(raw: bytes):
    """Return an exact-input report and optional artifact, without writing files."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_DRAFT_BYTES:
        raise DraftError('Provide a UTF-8 draft between 1 byte and 1 MiB.')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)
        # Also reject escaped unpaired surrogates; exported text is UTF-8.
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise DraftError('Draft must be finite, unambiguous UTF-8 JSON.') from exc
    try:
        errors = draft.validate_draft_schema(value)
        if not errors:
            # The prototype schema permits some optional fields without type checks.
            # Refuse malformed values before template interpolation or header parsing.
            ident = value['identity']
            for key in ('agent_id_template', 'origin', 'handoff_expectations', 'law', 'tags', 'task'):
                if key in ident and not isinstance(ident[key], str):
                    errors.append(f'identity.{key} must be text.')
            if 'task' in value and not isinstance(value['task'], str):
                errors.append('task must be text.')
            for key in ('name', 'role'):
                if any(c in ident[key] for c in '\r\n\x00'):
                    errors.append(f'identity.{key} must be a single line without NUL.')
            for trait in ident['disposition_and_working_style']:
                if 'context' in trait and not isinstance(trait['context'], str):
                    errors.append('Trait context must be text.')
        artifact = None
        if not errors:
            tier = value['identity']['tier']
            exporter = draft.export_and_verify_lw if tier == 'lightweight' else draft.export_and_verify_ul
            ok, text, errors = exporter(value)
            if ok and not errors:
                artifact = text.encode('utf-8')
                if len(artifact) > MAX_TEMPLATE_BYTES:
                    errors.append('Template exceeds the 2 MiB export limit.')
                    artifact = None
    except (TypeError, KeyError, ValueError, AttributeError, OverflowError, RecursionError):
        errors = ['Draft has malformed fields or cannot form a valid template.']
        artifact = None
    report = dict(format='aether-agent-editor-validation/1', validator=VALIDATOR,
                  draft_sha256=_sha(raw), status='REFUSED' if errors else 'TEMPLATE_VERIFIED',
                  errors=errors, policy='Fixed Bridge draft bounds; browser custom policy is advisory.',
                  scope='Template syntax and integrity only; no hiring, execution or consent.',
                  projection='Identity and disposition in template; complete source draft retained separately.')
    if artifact is not None:
        report.update(tier=value['identity']['tier'], artifact_sha256=_sha(artifact),
                      artifact_filename='template.briefcase.txt' if value['identity']['tier']=='lightweight'
                      else 'template.microcase.txt')
    return report, artifact


def export(raw: bytes):
    report, artifact = validate(raw)
    if artifact is None or report['status'] != 'TEMPLATE_VERIFIED':
        return report, None
    stream = io.BytesIO()
    files = {'draft.json': raw, report['artifact_filename']: artifact,
             'validation.json': (json.dumps(report, ensure_ascii=False, indent=2)+'\n').encode('utf-8')}
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, body)
    return report, stream.getvalue()
