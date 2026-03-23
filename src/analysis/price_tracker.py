"""Price change detection and trend analysis."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from src.database.models import PriceHistory, Property

logger = logging.getLogger(__name__)


@dataclass
class PriceChange:
    property_id: str
    source: str
    title: str
    listing_url: str
    old_price: float
    new_price: float
    change_amount: float
    change_percent: float
    city: str
    listing_type: str


def track_price_changes(session: Session, days: int = 7) -> list[PriceChange]:
    """Find properties with price changes in the last N days.

    Returns a list of PriceChange objects sorted by change percentage.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    # Get properties with multiple price history entries
    subquery = (
        session.query(
            PriceHistory.property_id,
            func.count(PriceHistory.id).label("entry_count"),
        )
        .filter(PriceHistory.recorded_at >= cutoff)
        .group_by(PriceHistory.property_id)
        .having(func.count(PriceHistory.id) >= 2)
        .subquery()
    )

    property_ids = [
        row[0] for row in session.query(subquery.c.property_id).all()
    ]

    changes = []
    for prop_id in property_ids:
        # Get the two most recent price entries
        entries = (
            session.query(PriceHistory)
            .filter(PriceHistory.property_id == prop_id)
            .order_by(PriceHistory.recorded_at.desc())
            .limit(2)
            .all()
        )

        if len(entries) < 2:
            continue

        new_entry, old_entry = entries[0], entries[1]
        if new_entry.price == old_entry.price:
            continue

        prop = session.query(Property).filter(Property.id == prop_id).first()
        if not prop:
            continue

        change_amount = new_entry.price - old_entry.price
        change_percent = (change_amount / old_entry.price) * 100

        changes.append(PriceChange(
            property_id=prop.id,
            source=prop.source,
            title=prop.title,
            listing_url=prop.listing_url,
            old_price=old_entry.price,
            new_price=new_entry.price,
            change_amount=change_amount,
            change_percent=change_percent,
            city=prop.address_city or "",
            listing_type=prop.listing_type,
        ))

    # Sort by change percentage (biggest drops first)
    changes.sort(key=lambda c: c.change_percent)
    logger.info(f"Found {len(changes)} price changes in last {days} days")
    return changes


def get_price_trend(session: Session, property_id: str) -> list[tuple[datetime, float]]:
    """Get the full price history for a property."""
    entries = (
        session.query(PriceHistory)
        .filter(PriceHistory.property_id == property_id)
        .order_by(PriceHistory.recorded_at)
        .all()
    )
    return [(e.recorded_at, e.price) for e in entries]
