"""Clock box: one place for time.

Inputs:  datetimes or ISO-8601 UTC strings ("2026-10-06T09:00:00Z").
Outputs: canonical ISO strings, fractional day differences, and clock objects.

Every other box asks a Clock for "now" instead of reading the system time
itself, so tests and the simulator can move time forward deterministically.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def to_iso(moment: datetime) -> str:
    """Canonical storage format: UTC, second precision, trailing Z."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime(ISO_FORMAT)


def parse_iso(text: str) -> datetime:
    """Parse the canonical format (Python 3.10 safe; no fromisoformat 'Z')."""
    text = text.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def days_between(earlier: str, later: str) -> float:
    """Fractional days from `earlier` to `later` (negative if reversed)."""
    return (parse_iso(later) - parse_iso(earlier)).total_seconds() / 86400.0


def add_days(moment: str, days: float) -> str:
    return to_iso(parse_iso(moment) + timedelta(days=days))


class SystemClock:
    """Real time."""

    def now(self) -> str:
        return to_iso(datetime.now(timezone.utc))


class FixedClock:
    """Test/simulation time. Starts at `start` and only moves when told."""

    def __init__(self, start: str):
        self._now = to_iso(parse_iso(start))

    def now(self) -> str:
        return self._now

    def set(self, moment: str) -> None:
        self._now = to_iso(parse_iso(moment))

    def advance(self, days: float) -> str:
        self._now = add_days(self._now, days)
        return self._now
