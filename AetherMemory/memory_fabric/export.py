"""Compatibility entry point: clean sharing is unavailable under preservation.

The October 7 owner agreement supersedes deletion from export copies. Retaining
historical payloads while calling such a copy clean would also leak past values.
No destination is created. Full recovery backups are available separately.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .errors import MemoryFabricError


class ExportError(MemoryFabricError, ValueError):
    """Any refusal from the export box."""


@dataclass
class ExportReport:
    destination: str
    removed_proprietary: List[str] = field(default_factory=list)
    flagged_derived: List[str] = field(default_factory=list)
    flagged_removed: bool = True
    removed_links: int = 0
    kept_notes: int = 0
    seal: str = ""


def export_clean(cart, destination: str, *, include_flagged: bool = False) -> ExportReport:
    raise ExportError(
        "clean sharing is unavailable under the preservation policy: deleting rows from a copy "
        "does not preserve information, and retained history may disclose earlier private values. "
        "Use a full backup for recovery; do not share it when confidentiality matters. "
        "Encryption/masking and lending require a separate supported workflow.")
