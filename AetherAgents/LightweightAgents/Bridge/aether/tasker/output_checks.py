"""Check operator-declared artifacts and bind acceptance to their bytes.

Never execute artifact code, change a task/output, infer quality from a review's prose,
or file a mind's reflection. Contracts, receipts and review decisions belong to the
operator outside the worker's output folder; this is not a hostile-code sandbox.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def parse(text):
    return json.loads(text, object_pairs_hook=_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def _safe_name(name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        raise ValueError('Expected a relative output filename')
    p = PurePosixPath(name)
    if p.is_absolute() or p.as_posix() != name or any(x in ('.', '..') for x in p.parts):
        raise ValueError('Output path must be canonical and relative')
    if any(x.lower() == '.sealbox_key' or x.lower().endswith('.key') for x in p.parts):
        raise ValueError('Key files are not verification inputs')
    reserved = {'CON', 'PRN', 'AUX', 'NUL'} | {f'{prefix}{n}' for prefix in ('COM', 'LPT') for n in range(1, 10)}
    if any(x != x.rstrip(' .') or x.split('.')[0].upper() in reserved for x in p.parts):
        raise ValueError('Output name aliases a reserved Windows path')
    return name


def _plain(path):
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            stat = part.lstat()
            if part.is_symlink() or getattr(stat, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Redirected verification path: ' + str(part))


def _bytes(path):
    _safe_name(path.name)
    _plain(path)
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('Verification input exceeds 32 MiB')
    return path.read_bytes()


def load(path):
    return parse(_bytes(Path(path).absolute()).decode('utf-8-sig'))


def validate(contract):
    if not isinstance(contract, dict) or set(contract) != {'version', 'review_required', 'files', 'evidence'}:
        raise ValueError('Contract needs version, review_required, files and evidence')
    if type(contract['version']) is not int or contract['version'] != 1 or type(contract['review_required']) is not bool:
        raise ValueError('Unsupported contract version or review requirement')
    files = contract['files']
    if not isinstance(files, dict) or not files:
        raise ValueError('Declare at least one output')
    names = [_safe_name(n) for n in files]
    if len(set(n.casefold() for n in names)) != len(names):
        raise ValueError('Output names collide on Windows')
    for spec in files.values():
        if not isinstance(spec, dict) or not spec or set(spec) - {'nonempty', 'json', 'required', 'forbidden'}:
            raise ValueError('Unknown or empty output check')
        if 'nonempty' in spec and spec['nonempty'] is not True:
            raise ValueError('nonempty must be true')
        for key in ('required', 'forbidden'):
            if key in spec and (not isinstance(spec[key], list) or not spec[key] or
                                any(not isinstance(s, str) or not s for s in spec[key])):
                raise ValueError(key + ' needs nonempty literal strings')
    if not contract['review_required'] and any('json' not in spec for spec in files.values()):
        raise ValueError('Review-free acceptance requires an independent exact JSON value for every output')
    if not isinstance(contract['evidence'], list):
        raise ValueError('evidence must be a list')
    ids = set()
    for item in contract['evidence']:
        if not isinstance(item, dict) or set(item) != {'id', 'path', 'sha256', 'output', 'hash_field'}:
            raise ValueError('Malformed trusted evidence reference')
        if any(not isinstance(v, str) or not v for v in item.values()):
            raise ValueError('Evidence reference values must be strings')
        if item['id'] in ids or item['output'] not in files:
            raise ValueError('Duplicate evidence id or unknown output')
        ids.add(item['id'])
        p = Path(item['path'])
        if not p.is_absolute() or p.suffix.lower() != '.json':
            raise ValueError('Evidence must name an absolute operator-owned JSON report')
        if len(item['sha256']) != 64 or any(c not in '0123456789abcdef' for c in item['sha256']):
            raise ValueError('Evidence needs its predeclared SHA256')
    digest(contract)  # Reject non-JSON/nonfinite expected values before reading artifacts.


def check(root, contract):
    validate(contract)
    root = Path(root).absolute()
    result = dict(version=1, checker_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  contract_sha256=digest(contract), artifacts={}, evidence={}, findings=[],
                  review_required=contract['review_required'])
    findings = result['findings']
    try:
        _plain(root)
        if not root.is_dir():
            raise ValueError('Output folder does not exist')
        actual = set()
        for base, dirs, files in os.walk(root, followlinks=False):
            for name in [*dirs, *files]:
                _plain(Path(base) / name)
            actual.update((Path(base) / name).relative_to(root).as_posix() for name in files)
        if actual != set(contract['files']):
            findings.append('Output file set differs: missing=' + repr(sorted(set(contract['files']) - actual)) +
                            ', extra=' + repr(sorted(actual - set(contract['files']))))
        # Unexpected files are named, never opened (including keys).
        for name, spec in contract['files'].items():
            try:
                raw = _bytes(root / name)
                result['artifacts'][name] = hashlib.sha256(raw).hexdigest()
                text = raw.decode('utf-8-sig')
                if spec.get('nonempty') and not text.strip():
                    findings.append(name + ': empty output')
                if 'json' in spec and digest(parse(text)) != digest(spec['json']):
                    findings.append(name + ': JSON differs from the independent expected value')
                for phrase in spec.get('required', []):
                    if phrase not in text:
                        findings.append(name + ': missing required literal ' + repr(phrase))
                for phrase in spec.get('forbidden', []):
                    if phrase.casefold() in text.casefold():
                        findings.append(name + ': prohibited literal ' + repr(phrase))
            except (OSError, ValueError, UnicodeError) as exc:
                findings.append(name + ': ' + str(exc))
        for item in contract['evidence']:
            try:
                path = Path(item['path']).absolute()
                if path.is_relative_to(root):
                    raise ValueError('Worker output cannot supply its own trusted check report')
                raw = _bytes(path)
                actual_hash = hashlib.sha256(raw).hexdigest()
                result['evidence'][item['id']] = actual_hash
                if actual_hash != item['sha256']:
                    raise ValueError('Trusted report bytes changed')
                report = parse(raw.decode('utf-8-sig'))
                if not isinstance(report, dict) or report.get('passed') is not True:
                    raise ValueError('Trusted check did not pass')
                if report.get(item['hash_field']) != result['artifacts'].get(item['output']):
                    raise ValueError('Trusted check covers different artifact bytes')
            except (OSError, ValueError, UnicodeError) as exc:
                findings.append(item['id'] + ': ' + str(exc))
    except (OSError, ValueError) as exc:
        findings.append(str(exc))
    result['status'] = 'failed' if findings else 'checks_passed'
    return result


def accept(root, contract, receipt, review=None):
    current = check(root, contract)
    if current['status'] != 'checks_passed':
        return dict(status='failed', findings=current['findings'])
    if not isinstance(receipt, dict) or digest(current) != digest(receipt):
        return dict(status='unverified_or_stale')
    if current['review_required']:
        if not isinstance(review, dict) or review.get('verdict') not in ('PASS', 'FAIL'):
            return dict(status='review_required')
        if review.get('receipt_sha256') != digest(receipt) or not review.get('reviewer'):
            return dict(status='unbound_or_stale_review')
        if review['verdict'] != 'PASS':
            return dict(status='review_failed')
    return dict(status='accepted', receipt_sha256=digest(receipt),
                limitation='Only declared checks and the explicit bound review; no universal quality claim')


def write_receipt(path, root, value):
    path = Path(path).absolute()
    if path.is_relative_to(Path(root).absolute()):
        raise ValueError('Receipts belong outside worker outputs')
    _safe_name(path.name)
    _plain(path)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['check', 'accept'])
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--contract', type=Path, required=True)
    p.add_argument('--receipt', type=Path, required=True)
    p.add_argument('--review', type=Path)
    a = p.parse_args()
    try:
        contract = load(a.contract)
        if a.action == 'check':
            result = check(a.root, contract)
            write_receipt(a.receipt, a.root, result)
        else:
            result = accept(a.root, contract, load(a.receipt), load(a.review) if a.review else None)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['status'] in ('checks_passed', 'accepted') else 2
    except (OSError, ValueError) as exc:
        print(json.dumps(dict(status='invalid_verification_input', detail=str(exc))))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
