"""Daily market snapshots — the long-run trend layer.

``record_snapshot`` aggregates the current active, non-duplicate, filter-passing
listings into one :class:`MarketSnapshot` row per (date, listing_type, commune)
and per type-wide "All communes" rollup. Run once per pipeline run (idempotent
per calendar day: a second run the same day overwrites that day's rows), it
accumulates into a year-long series you can chart to decide rent-vs-buy and
now-vs-later.

Price = sale price for buy, monthly total (rent+charges, falling back to rent)
for rentals — the same basis the scorer uses.
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from src.lux_monitor.models import Listing, MarketSnapshot
from src.lux_monitor.timeutil import as_naive_utc, naive_utc_now

logger = logging.getLogger(__name__)

# Rollup label across communes for a type. Sentinel-prefixed so it can never
# collide with a real commune name (which would break the unique constraint).
ALL_COMMUNES = "[all]"


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 2) if values else None


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 2) if values else None


def _day_bounds(when: datetime) -> tuple[datetime, datetime]:
    start = when.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _aggregate(listings: list[Listing], *, new_since: datetime | None) -> dict:
    # Compute compare_price once per listing.
    priced = [(l, l.compare_price) for l in listings]
    prices = [p for _, p in priced if p is not None]
    surfaces = [l.surface_m2 for l in listings if l.surface_m2]
    ppm2 = [p / l.surface_m2 for l, p in priced if p is not None and l.surface_m2]
    new_count = 0
    if new_since is not None:
        new_count = sum(
            1 for l in listings
            if l.first_seen_at and as_naive_utc(l.first_seen_at) >= new_since
        )
    return {
        "count": len(listings),
        "median_price_eur": _median(prices),
        "mean_price_eur": _mean(prices),
        "median_price_per_m2_eur": _median(ppm2),
        "median_surface_m2": _median(surfaces),
        "new_count": new_count,
    }


def record_snapshot(session: Session, *, when: datetime | None = None) -> int:
    """Write today's market snapshot rows. Returns the number of segments written.

    Idempotent per calendar day: existing rows for ``when``'s date are replaced,
    so re-running the pipeline the same day updates rather than duplicates.
    """
    from src.lux_monitor.scoring import passes_hard_filter

    when = as_naive_utc(when) if when else naive_utc_now()
    day_start, day_end = _day_bounds(when)

    # "New since last snapshot": a supply-inflow signal. The prior snapshot's
    # snapshot_date is stored at MIDNIGHT, so to avoid re-counting everything
    # that arrived *during* that prior day we start the window at the END of the
    # prior snapshot's day (prior date + 1 day). Fallback: today's midnight.
    prior = (
        session.query(MarketSnapshot.snapshot_date)
        .filter(MarketSnapshot.snapshot_date < day_start)
        .order_by(MarketSnapshot.snapshot_date.desc())
        .first()
    )
    new_since = (prior[0] + timedelta(days=1)) if prior else day_start

    active = (
        session.query(Listing)
        .filter(Listing.is_active.is_(True), Listing.duplicate_of_id.is_(None))
        .all()
    )
    listings = [l for l in active if passes_hard_filter(l).passed]

    # Replace any existing rows for this calendar day (idempotent re-run).
    session.query(MarketSnapshot).filter(
        MarketSnapshot.snapshot_date >= day_start,
        MarketSnapshot.snapshot_date < day_end,
    ).delete(synchronize_session=False)

    # Group by (type, commune); also a per-type "All" rollup.
    by_segment: dict[tuple[str, str], list[Listing]] = {}
    for l in listings:
        by_segment.setdefault((l.listing_type, l.commune), []).append(l)
        by_segment.setdefault((l.listing_type, ALL_COMMUNES), []).append(l)

    written = 0
    for (listing_type, commune), group in by_segment.items():
        agg = _aggregate(group, new_since=new_since)
        session.add(MarketSnapshot(
            snapshot_date=day_start,
            listing_type=listing_type,
            commune=commune,
            **agg,
        ))
        written += 1

    session.commit()
    logger.info("record_snapshot: wrote %d market segments for %s", written, day_start.date())
    return written
