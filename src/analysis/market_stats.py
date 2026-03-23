"""Market statistics calculation and snapshot creation."""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from src.database.models import MarketSnapshot, Property

logger = logging.getLogger(__name__)


def compute_market_snapshots(session: Session) -> list[MarketSnapshot]:
    """Compute and store market statistics snapshots.

    Groups by country, listing_type, and property_type.
    Returns the list of created snapshots.
    """
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    one_week_ago = today - timedelta(days=7)

    snapshots = []

    # Get all active combinations
    combos = (
        session.query(
            Property.country,
            Property.listing_type,
            Property.property_type,
        )
        .filter(Property.is_active.is_(True))
        .distinct()
        .all()
    )

    for country, listing_type, property_type in combos:
        active_props = (
            session.query(Property)
            .filter(
                Property.is_active.is_(True),
                Property.country == country,
                Property.listing_type == listing_type,
                Property.property_type == property_type,
            )
            .all()
        )

        if not active_props:
            continue

        prices = [p.price for p in active_props if p.price]
        prices_per_sqm = [p.price_per_sqm for p in active_props if p.price_per_sqm]

        new_count = sum(
            1 for p in active_props if p.first_seen_at and p.first_seen_at >= one_week_ago
        )

        # Count removed (were active before but not seen this week)
        removed_count = (
            session.query(func.count(Property.id))
            .filter(
                Property.is_active.is_(False),
                Property.country == country,
                Property.listing_type == listing_type,
                Property.property_type == property_type,
                Property.last_seen_at >= one_week_ago,
                Property.last_seen_at < today,
            )
            .scalar()
        ) or 0

        snapshot = MarketSnapshot(
            snapshot_date=today,
            country=country,
            listing_type=listing_type,
            property_type=property_type,
            avg_price=statistics.mean(prices) if prices else None,
            median_price=statistics.median(prices) if prices else None,
            avg_price_per_sqm=statistics.mean(prices_per_sqm) if prices_per_sqm else None,
            median_price_per_sqm=statistics.median(prices_per_sqm) if prices_per_sqm else None,
            total_listings=len(active_props),
            new_listings_this_week=new_count,
            removed_listings_this_week=removed_count,
        )
        session.add(snapshot)
        snapshots.append(snapshot)

    session.commit()
    logger.info(f"Created {len(snapshots)} market snapshots")
    return snapshots


def get_historical_stats(
    session: Session,
    country: str | None = None,
    listing_type: str | None = None,
    property_type: str | None = None,
    weeks: int = 52,
) -> list[MarketSnapshot]:
    """Retrieve historical market snapshots for trend analysis."""
    cutoff = datetime.utcnow() - timedelta(weeks=weeks)

    query = session.query(MarketSnapshot).filter(
        MarketSnapshot.snapshot_date >= cutoff
    )
    if country:
        query = query.filter(MarketSnapshot.country == country)
    if listing_type:
        query = query.filter(MarketSnapshot.listing_type == listing_type)
    if property_type:
        query = query.filter(MarketSnapshot.property_type == property_type)

    return query.order_by(MarketSnapshot.snapshot_date).all()
