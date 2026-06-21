"""Time helpers shared across the monitor.

SQLite stores naive datetimes (we treat them as UTC). In-session ORM objects may
still carry the original timezone-aware ``first_seen_at``/``recorded_at`` until
the next flush+reload, so any comparison must normalize both sides. Centralized
here so the convention lives in exactly one place.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def naive_utc_now() -> datetime:
    """Current time as naive UTC (matching how SQLite stores our datetimes)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_naive_utc(value: datetime) -> datetime:
    """Normalize a datetime to naive UTC.

    From-DB rows are already naive; in-session rows may still be the original
    timezone-aware object, which we convert to UTC and strip.
    """
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def naive_utc_cutoff(days: int) -> datetime:
    """Naive-UTC timestamp ``days`` before now."""
    return naive_utc_now() - timedelta(days=days)
