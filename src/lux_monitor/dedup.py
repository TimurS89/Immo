"""Cross-portal de-duplication for Luxembourg listings.

The same property often appears on athome + immotop + wortimmo. We treat two
*different-portal* listings as the same property when they share commune +
listing type, have equal bedroom counts, surfaces within ±2 m², and prices within
5% (rent_total for rentals, sale price for buys). Tolerances are tuned tighter
than the legacy DE/FR dedup because the LU market is small and surfaces are
reported precisely.

``mark_duplicates`` records the link by setting ``duplicate_of_id`` on the
secondary (less complete) listing, pointing at the kept primary.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from src.lux_monitor.models import Listing

logger = logging.getLogger(__name__)

SURFACE_TOL_M2 = 2.0
PRICE_TOL_PCT = 5.0


def is_duplicate_pair(a: Listing, b: Listing) -> bool:
    """True if a and b are very likely the same property (same listing type)."""
    if a.listing_type != b.listing_type:
        return False
    if (a.commune or "").strip().lower() != (b.commune or "").strip().lower():
        return False
    if a.bedrooms is None or b.bedrooms is None or a.bedrooms != b.bedrooms:
        return False
    if a.surface_m2 is None or b.surface_m2 is None:
        return False
    if abs(a.surface_m2 - b.surface_m2) > SURFACE_TOL_M2:
        return False
    pa, pb = a.compare_price, b.compare_price
    if pa is None or pb is None or max(pa, pb) == 0:
        return False
    if abs(pa - pb) / max(pa, pb) * 100.0 > PRICE_TOL_PCT:
        return False
    return True


def _completeness(listing: Listing) -> int:
    """Rough richness score used to choose which duplicate to keep as primary."""
    return sum(
        [
            bool(listing.description_raw and len(listing.description_raw) > 40),
            len(listing.photos_urls or []),
            bool(listing.energy_class),
            bool(listing.lat and listing.lng),
            bool(listing.construction_year),
            bool(listing.address_text),
        ]
    )


def _pick_primary(a: Listing, b: Listing) -> tuple[Listing, Listing]:
    sa, sb = _completeness(a), _completeness(b)
    if sa != sb:
        return (a, b) if sa > sb else (b, a)
    # Deterministic tie-break: lower id is primary.
    return (a, b) if (a.id or 0) <= (b.id or 0) else (b, a)


def mark_duplicates(session: Session) -> int:
    """Find cross-portal duplicates among active listings and link secondaries.

    Returns the number of listings newly marked as duplicates.
    """
    listings = session.query(Listing).filter(Listing.is_active.is_(True)).all()

    groups: dict[tuple[str, str], list[Listing]] = {}
    for listing in listings:
        key = ((listing.commune or "").strip().lower(), listing.listing_type)
        groups.setdefault(key, []).append(listing)

    marked = 0
    for group in groups.values():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if a.portal == b.portal:
                    continue
                if a.duplicate_of_id or b.duplicate_of_id:
                    continue  # already linked elsewhere; keep it simple, no chains
                if is_duplicate_pair(a, b):
                    primary, secondary = _pick_primary(a, b)
                    secondary.duplicate_of_id = primary.id
                    marked += 1
                    logger.info(
                        "Duplicate: %s:%s -> primary %s:%s",
                        secondary.portal, secondary.portal_listing_id,
                        primary.portal, primary.portal_listing_id,
                    )

    session.commit()
    logger.info("mark_duplicates: %d secondaries linked", marked)
    return marked
