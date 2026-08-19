"""Time, in one place.

Everything stored is UTC and timezone-aware. `datetime.utcnow()` returns a
naive datetime, which SQLite stores without an offset and SQLAlchemy hands
back as naive, so a comparison against an aware value raises at the point of
use rather than where the mistake was made. One helper, used everywhere.
"""
from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """Re-attach UTC to a value that came back from SQLite naive.

    SQLite has no datetime type: SQLAlchemy writes an ISO string and parses it
    back without an offset, so every column round-trips aware to naive. Read
    through this rather than comparing a stored value directly.
    """
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
