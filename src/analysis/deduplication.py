"""Cross-platform duplicate detection using fuzzy matching."""

from __future__ import annotations

import logging
from itertools import combinations

from sqlalchemy import and_
from sqlalchemy.orm import Session
from thefuzz import fuzz

from src.database.models import Property

logger = logging.getLogger(__name__)

# Thresholds for considering two listings as duplicates
ADDRESS_SIMILARITY_THRESHOLD = 80
PRICE_TOLERANCE_PERCENT = 5
AREA_TOLERANCE_PERCENT = 10


def deduplicate_properties(session: Session) -> int:
    """Find and mark duplicate properties across different sources.

    Returns the number of duplicates found.
    """
    # Group active properties by approximate location (postal code + listing type)
    properties = (
        session.query(Property)
        .filter(Property.is_active.is_(True))
        .order_by(Property.address_postal_code, Property.listing_type)
        .all()
    )

    # Group by postal code and listing type
    groups: dict[tuple[str, str], list[Property]] = {}
    for prop in properties:
        key = (prop.address_postal_code or "", prop.listing_type)
        groups.setdefault(key, []).append(prop)

    total_dupes = 0

    for (postal, lt), group in groups.items():
        if len(group) < 2:
            continue

        # Compare each pair within the group (different sources only)
        for a, b in combinations(group, 2):
            if a.source == b.source:
                continue

            if _is_duplicate(a, b):
                # Keep the one with more data, mark the other
                primary, secondary = _pick_primary(a, b)
                if not _already_linked(primary, secondary):
                    logger.info(
                        f"Duplicate found: {primary.source}:{primary.external_id} "
                        f"<-> {secondary.source}:{secondary.external_id}"
                    )
                    # Store duplicate reference in raw_data
                    raw = secondary.raw_data or {}
                    raw["_duplicate_of"] = {
                        "id": primary.id,
                        "source": primary.source,
                        "external_id": primary.external_id,
                    }
                    secondary.raw_data = raw
                    total_dupes += 1

    session.commit()
    logger.info(f"Deduplication complete: {total_dupes} duplicates found")
    return total_dupes


def _is_duplicate(a: Property, b: Property) -> bool:
    """Check if two properties are likely the same listing."""
    # Must be same listing type and property type
    if a.listing_type != b.listing_type or a.property_type != b.property_type:
        return False

    # Price check
    if a.price and b.price:
        price_diff_pct = abs(a.price - b.price) / max(a.price, b.price) * 100
        if price_diff_pct > PRICE_TOLERANCE_PERCENT:
            return False
    elif a.price or b.price:
        return False  # One has price, other doesn't

    # Area check
    if a.living_area_sqm and b.living_area_sqm:
        area_diff_pct = abs(a.living_area_sqm - b.living_area_sqm) / max(a.living_area_sqm, b.living_area_sqm) * 100
        if area_diff_pct > AREA_TOLERANCE_PERCENT:
            return False
    elif a.living_area_sqm or b.living_area_sqm:
        return False

    # Rooms check (must match exactly if both present)
    if a.rooms and b.rooms:
        if a.rooms != b.rooms:
            return False

    # Address similarity (fuzzy match)
    addr_a = _normalize_address(a)
    addr_b = _normalize_address(b)
    if addr_a and addr_b:
        similarity = fuzz.token_sort_ratio(addr_a, addr_b)
        if similarity < ADDRESS_SIMILARITY_THRESHOLD:
            return False

    # Title similarity as backup
    if a.title and b.title:
        title_sim = fuzz.token_sort_ratio(a.title.lower(), b.title.lower())
        if title_sim > 85:
            return True

    # If all checks passed (price, area, rooms, address), it's likely a duplicate
    return True


def _normalize_address(prop: Property) -> str:
    """Create a normalized address string for comparison."""
    parts = []
    if prop.address_street:
        parts.append(prop.address_street.lower())
    if prop.address_postal_code:
        parts.append(prop.address_postal_code)
    if prop.address_city:
        parts.append(prop.address_city.lower())
    return " ".join(parts)


def _pick_primary(a: Property, b: Property) -> tuple[Property, Property]:
    """Pick the primary listing (the one with more complete data)."""
    score_a = sum([
        bool(a.description),
        bool(a.image_urls),
        bool(a.energy_rating),
        bool(a.year_built),
        bool(a.latitude),
        bool(a.address_street),
        len(a.image_urls or []),
    ])
    score_b = sum([
        bool(b.description),
        bool(b.image_urls),
        bool(b.energy_rating),
        bool(b.year_built),
        bool(b.latitude),
        bool(b.address_street),
        len(b.image_urls or []),
    ])
    return (a, b) if score_a >= score_b else (b, a)


def _already_linked(primary: Property, secondary: Property) -> bool:
    """Check if these two are already linked as duplicates."""
    raw = secondary.raw_data or {}
    dup = raw.get("_duplicate_of", {})
    return dup.get("id") == primary.id
