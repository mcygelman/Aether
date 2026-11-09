"""Validate and describe explicit briefcase grants; never infer access from prose."""
import re
from pathlib import Path

GRANT = re.compile(r"⟪GRANT::(?:(WRITE)::)?([^⟫]+)⟫")
GRANT_HINT = re.compile(r"⟪GRANT|\bGRANT\s*::", re.IGNORECASE)
GRANT_LABEL = re.compile(r"\b(?:READ_GRANT|WRITE_GRANT)\b", re.IGNORECASE)
HELP = 'Use briefcase_create read_roots=["<absolute existing folder>"] to install read access; remove the malformed grant directive and retry. Ordinary prose does not grant access.'


def validate_directive(text):
    matches = list(GRANT.finditer(text))
    if (GRANT_HINT.search(GRANT.sub('', text)) or
            (GRANT_LABEL.search(text) and not matches) or
            any(not m.group(2).strip() or any(c in m.group(2) for c in '\r\n<>⟪') for m in matches)):
        raise ValueError('Malformed access grant. ' + HELP)


def read_directive(roots):
    if not isinstance(roots, list) or any(not isinstance(r, str) or not r.strip() for r in roots):
        raise ValueError('read_roots must be a list of absolute existing folder paths.')
    paths = []
    for raw in roots:
        path = Path(raw.strip())
        if any(c in raw for c in '\r\n⟪⟫') or not path.is_absolute() or not path.is_dir():
            raise ValueError(f'read_roots folder is not an absolute existing directory: {raw!r}. Correct the path and retry.')
        value = str(path.resolve())
        if value not in paths: paths.append(value)
    if not paths: return None
    return ('∆WMC{\nWORLD_MODEL := ⌜EXPLICIT_READ_ACCESS⌝\n'
            '[DEF] → ⌜ACCESS⌝ := ⌞ Read, test and run access only. Write only your task outputs. ⌟\n'
            + '\n'.join('⟪GRANT::' + p + '⟫' for p in paths) + '\n}∆WMC')


def effective_roots(sprint, agent):
    roots = []
    for path in sorted((sprint.briefcase(agent)/'directives').glob('*.txt')):
        text = path.read_text(encoding='utf-8')
        validate_directive(text)
        for _, raw in GRANT.findall(text):
            p = Path(raw.strip())
            value = str((p if p.is_absolute() else sprint.root/p).resolve())
            if value not in roots: roots.append(value)
    return roots
