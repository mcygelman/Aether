"""Does a model key's value appear in any file under the given folders? The scan behind key-scan.json.

    python scan_for_key.py <report.json> <folder> [<folder> ...]

The value is read from the AETHER_API_KEY environment variable of this process and compared as bytes: the whole
value, its UTF-16-LE form, and its last 12 characters. Only counts and the paths of matching files are printed or
written; never the value. On October 28 it ran over the installation, its backup and the run's own evidence folder.
"""
import json
import os
from pathlib import Path
import sys

value = os.environ.get('AETHER_API_KEY', '')
if len(value) < 16:
    sys.exit('AETHER_API_KEY is not set in this process; nothing scanned')
needles = {'whole': value.encode(), 'whole_utf16': value.encode('utf-16-le'), 'tail12': value[-12:].encode()}
out = Path(sys.argv[1])
report = dict(roots=sys.argv[2:], files=0, bytes=0, unreadable=0, hits={k: [] for k in needles})
for root in sys.argv[2:]:
    for p in Path(root).rglob('*'):
        if not p.is_file():
            continue
        try:
            data = p.read_bytes()
        except OSError:
            report['unreadable'] += 1
            continue
        report['files'] += 1
        report['bytes'] += len(data)
        for name, needle in needles.items():
            if needle in data:
                report['hits'][name].append(str(p))
report['clean'] = not any(report['hits'].values())
out.write_text(json.dumps(report, indent=1), encoding='utf-8', newline='\n')
print(json.dumps(dict(files=report['files'], bytes=report['bytes'], unreadable=report['unreadable'],
                      hits={k: len(v) for k, v in report['hits'].items()}, clean=report['clean'])))
