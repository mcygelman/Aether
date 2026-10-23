"""Read-only campaign receipt projection. Existing records are the only store.

Record digests hash the bytes read; captured digests are labeled references from
those records, not assertions that their targets were independently validated.
Ordering is workflow phase and record path, not a reconstructed global clock.
"""
from copy import deepcopy
from pathlib import Path
import re
from urllib.parse import urlencode

from .records import load_campaign_records, RUN_STORES, HOST_STORES, HISTORY_STORES
from .state import campaign_state

API = '/api/campaign-receipts'
RECORD_API = '/api/campaign-receipt-record'
PAGE = '/projects/campaign-receipts'
HEX = re.compile(r'^[0-9a-fA-F]{64}$')


def read_receipt_history(read, home):
    """Include superseded captures, not only the state loader's latest outcome.

    Visit only known store layouts. T07 outcomes are located through the closure
    run's captured evidence directory; never traverse an identity or cartridge.
    """
    folders = {'planning/proposals', 'staffing', 'closure-packets', 'closure-dialogue',
               'closure-dialogue/operator-decisions', 'closure-staff', 'closure-staff/outcomes'}   # staff memory step 4 (Fable, 10-12)
    folders.update(row[1] for row in HISTORY_STORES)
    nested = {'execution', 'planning/minder/native-artifact-effects'}
    nested.update(row[1] for row in RUN_STORES)
    nested.update(row[1] for row in HOST_STORES)
    for folder in sorted(nested):
        folders.update(str(p.relative_to(home)) for p in read.listing(home / folder, 'dir'))
    for folder in sorted(folders):
        for path in read.listing(home / folder, 'file'):
            if path.suffix == '.json' and str(path) not in read.documents:
                read.json(path)
    # Closure result anchors are immutable references to this run's evidence.
    for path, record in list(read.documents.items()):
        if (record.get('schema') != 'aether-closure-supervision-result-1.0.0'
                or '/native-closure-runs/' not in Path(path).as_posix()):
            continue
        anchor = record.get('anchor') or {}
        directory = anchor.get('evidence_directory')
        if not isinstance(directory, str):
            continue
        evidence = read.anchor(directory, path)
        if evidence is None:
            continue
        for name in ('closure-owner-intent.json', 'closure-owner-outcome.json'):
            target = evidence / name
            if read.lstat(target, missing_ok=True) is not None:
                read.json(target)


def captured_digests(value, field='$'):
    """Exact stored digest values, including maps of artifact/source digests."""
    result = []
    if isinstance(value, dict):
        for key, child in sorted(value.items()):
            result.extend(captured_digests(child, field + '.' + key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.extend(captured_digests(child, f'{field}[{index}]'))
    elif isinstance(value, str) and HEX.fullmatch(value):
        result.append(dict(field=field, sha256=value))
    return result


def _phase(path):
    path = path.replace('\\', '/')
    if 'closure-owner-' in path: return '08 Memory / identity closure'
    if '/operator-decisions/' in path or '/native-task-decisions/' in path: return '07 Accepted / decision'
    if '/closure-' in path or '/native-closure-' in path: return '06 Closure'
    if '/native-reviews/' in path or '/minder-assessment/' in path: return '05 Reviewed'
    if '/execution/' in path or '/native-staff-contributions/' in path or '/native-artifact-effects/' in path: return '04 Run / artifacts'
    if 'staff' in path: return '03 Staff / authorization'
    if '/planning/' in path: return '02 Plan / authorization'
    if '#revision=' in path: return '04 Run / generation'
    return '01 Framing / authorization'


def campaign_receipts(records):
    """Pure projection; never writes, activates, accepts or declares a new verdict."""
    state = campaign_state(records)
    documents = records.get('receipt_documents', {})
    rows = []
    seen = set()
    for source in records.get('sources', []):
        path, sha = source['path'], source['sha256']
        if (path, sha) in seen: continue
        seen.add((path, sha))
        document = documents.get(path, {})
        memory = document.get('memory') if document.get('schema') == 'aether-closure-owner-outcome-1' else None
        rows.append(dict(phase=_phase(path), path=path, sha256=sha,
                         href=RECORD_API + '?' + urlencode(dict(id=records['campaign_id'], path=path, sha256=sha)),
                         captured=captured_digests(document), memory_receipt=deepcopy(memory)))
    rows.sort(key=lambda row: (row['phase'], row['path']))
    return dict(campaign_id=records['campaign_id'], stage=state['stage'], condition=state['condition'],
                reason=state['reason'], obligations=state['obligations'], receipts=rows,
                planning_memory=deepcopy(records.get('planning_memory', [])),
                artifact_candidates=deepcopy(records.get('artifact_candidates', [])),
                problems=deepcopy(records.get('problems', [])),
                ordering='Workflow phase, then record path; not a global event chronology',
                verification='Record byte digests and captured references; no additional acceptance or T07 behavioral verdict')


def load_campaign_receipts(root, campaign_id=None, *, live_runs=()):
    return campaign_receipts(load_campaign_records(root, campaign_id, live_runs=live_runs, include_receipts=True))
