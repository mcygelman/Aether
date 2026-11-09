"""Check the hash chain of the two daybooks in this folder with Aether's own digest.

From the repository root (Python 3.10, requirements installed):
    python evidence/2026-10-28-two-project-run/verify_daybooks.py
Each line must carry its position, the previous line's hash and its own hash. The attached files themselves are not
included in this folder, so only the chain of notes is checked here.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / 'AetherAgents/LightweightAgents/Bridge'))
from aether.roadmaps.project_engagement_contract import digest

ok = True
for name in ('daybook-project-a.jsonl', 'daybook-project-b.jsonl'):
    previous, rows = None, [json.loads(line) for line in (HERE / name).read_text(encoding='utf-8').splitlines() if line.strip()]
    problem = None
    for n, row in enumerate(rows, 1):
        body = {k: v for k, v in row.items() if k != 'sha256'}
        if row.get('n') != n or row.get('previous_sha256') != previous or digest(body) != row.get('sha256'):
            problem = f'note {n} breaks the chain'
            break
        previous = row['sha256']
    ok = ok and problem is None
    print(f'{name}: {len(rows)} notes, ' + (problem or f'chain intact, last hash {previous}'))
sys.exit(0 if ok else 1)
