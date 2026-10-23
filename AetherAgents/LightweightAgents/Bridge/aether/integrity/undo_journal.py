"""The undo journal's writer (MOD-09 Integrity): one record per change, prior content kept beside it.

Moved down from aether.toolbelt.toolbelt (`ToolBelt._journal` and its id counter) on
2026-10-08, unchanged, so SANCTUM's reseal can journal the prior manifest without building
a ToolBelt. The Toolbelt writes through this function too and still reads the journal for
undo; the format is the same.

Inputs:  the journal folder, the operation name, the root's name and resolved path, the
         changed file, its prior content (text, sealed bytes or None) and any detail.
Outputs: the entry id; `<id>.prior` beside `journal.jsonl` when there was prior content.
"""
from __future__ import annotations

import itertools
import json
import time
from pathlib import Path

JOURNAL_IDS = itertools.count()     # one counter per process, shared by every writer


def write_entry(journal_dir: Path, op: str, root_name: str, root_path: Path, p: Path,
                prior: str | bytes | None, **detail) -> str:
    entry_id = f"{int(time.time() * 1000):x}-{next(JOURNAL_IDS):x}"
    if prior is not None:
        prior_path = journal_dir / f"{entry_id}.prior"
        if isinstance(prior, bytes):
            prior_path.write_bytes(prior)
            detail["prior_storage"] = "sealed-bytes"
        else:
            with prior_path.open("w", encoding="utf-8", newline="\n") as f:
                f.write(prior)
    rec = {"id": entry_id, "op": op, "root": root_name,
           "root_path": str(root_path),
           "path": str(p.relative_to(root_path)), "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "had_prior": prior is not None, **detail}
    with (journal_dir / "journal.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return entry_id
