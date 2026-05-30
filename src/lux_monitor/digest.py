"""Recent-activity helpers: brand-new listings and price drops.

Reusable by the dashboard (and later by notifications). All queries are scoped to
active, non-duplicate listings, and default to ones that passed the hard filter
(``score_total`` set).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from src.lux_monitor.models import Listing


def _utc_cutoff(days: int) -> datetime:
    # SQLite stores naive datetimes (UTC); compare against naive UTC.
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)


def _as_naive_utc(value: datetime) -> datetime:
    """Normalize a datetime to naive UTC (from-DB rows are naive; in-session
    rows may still be the original timezone-aware object)."""
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def new_listings(session: Session, *, days: int = 7, scored_only: bool = True) -> list[Listing]:
    """Active, non-duplicate listings first seen within the last ``days``."""
    query = session.query(Listing).filter(
        Listing.is_active.is_(True),
        Listing.duplicate_of_id.is_(None),
        Listing.first_seen_at >= _utc_cutoff(days),
    )
    if scored_only:
        query = query.filter(Listing.score_total.isnot(None))
    return query.order_by(Listing.score_total.desc()).all()


@dataclass
class PriceDrop:
    listing: Listing
    old_price: float
    new_price: float

    @property
    def delta(self) -> float:
        return self.new_price - self.old_price

    @property
    def pct(self) -> float:
        return round(100.0 * self.delta / self.old_price, 1) if self.old_price else 0.0


def price_drops(session: Session, *, days: int = 30, scored_only: bool = True) -> list[PriceDrop]:
    """Listings whose most recent recorded price is a *decrease*, within ``days``.

    Compares the last two price-history points; biggest percentage drop first.
    """
    cutoff = _utc_cutoff(days)
    query = session.query(Listing).filter(
        Listing.is_active.is_(True), Listing.duplicate_of_id.is_(None)
    )
    if scored_only:
        query = query.filter(Listing.score_total.isnot(None))

    drops: list[PriceDrop] = []
    for listing in query.all():
        points = [
            (entry.recorded_at, entry.price_eur)
            for entry in sorted(listing.price_history_entries, key=lambda e: e.recorded_at)
            if entry.price_eur is not None
        ]
        if len(points) < 2:
            continue
        (_, previous), (last_when, latest) = points[-2], points[-1]
        if latest < previous and _as_naive_utc(last_when) >= cutoff:
            drops.append(PriceDrop(listing, previous, latest))

    drops.sort(key=lambda d: d.pct)  # most negative (biggest drop) first
    return drops
