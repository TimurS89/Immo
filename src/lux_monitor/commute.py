"""Crude, offline commute & walkability estimates (no external routing API).

Google Maps was only ever needed here (drive/PT/walk + geocoding) and nothing
else in the project, so we drop it entirely. Instead: straight-line (haversine)
distance × a road-winding factor, divided by assumed speeds, with a rush-hour
multiplier for driving. Rough but deterministic, free, offline, and testable.
Tune the constants below if the estimates feel off.

Origin per listing = its own lat/lng when present, else the commune anchor
(``config.luxembourg.TARGET_COMMUNES`` school coordinate). Drive/PT target the
DWS office. ``walk_to_school`` is only computed when the listing has its own
coordinate (otherwise origin == anchor == 0, which is meaningless); crèche/park
walk times are left ``None`` (no data source without a maps API).
"""

from __future__ import annotations

import logging
import math

from sqlalchemy.orm import Session

from config.luxembourg import DWS_OFFICE, TARGET_COMMUNES
from src.lux_monitor.models import Listing

logger = logging.getLogger(__name__)

# --- Tunable assumptions (Luxembourg rush hour is heavy; intentionally rough) ---
ROAD_WINDING_FACTOR = 1.3  # straight-line -> approximate road distance
AVG_DRIVE_KMH = 45.0       # effective average (a flat speed under-models highways)
RUSH_DRIVE_FACTOR = 1.7    # peak congestion (NOT discounted)
AVG_PT_KMH = 22.0          # effective public-transport speed
PT_OVERHEAD_MIN = 12.0     # walk-to-stop + wait + transfer
WALK_KMH = 4.8             # ~80 m/min
MIN_DRIVE_MIN = 5          # floor so in-commune isn't an unrealistic 0–1 min

_EARTH_R_KM = 6371.0


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * _EARTH_R_KM * math.asin(math.sqrt(a))


def _road_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    return haversine_km(lat1, lng1, lat2, lng2) * ROAD_WINDING_FACTOR


def estimate_drive_min(lat: float, lng: float, *, office=DWS_OFFICE) -> int:
    km = _road_km(lat, lng, office.lat, office.lng)
    return max(MIN_DRIVE_MIN, round(km / AVG_DRIVE_KMH * 60 * RUSH_DRIVE_FACTOR))


def estimate_pt_min(lat: float, lng: float, *, office=DWS_OFFICE) -> int:
    km = _road_km(lat, lng, office.lat, office.lng)
    return round(km / AVG_PT_KMH * 60 + PT_OVERHEAD_MIN)


def estimate_walk_min(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    km = _road_km(lat1, lng1, lat2, lng2)
    return round(km / WALK_KMH * 60)


def commune_anchor(commune: str | None) -> tuple[float, float] | None:
    """Representative (lat, lng) for a commune (its centre point)."""
    meta = TARGET_COMMUNES.get(commune or "")
    if not meta:
        return None
    return (meta["lat"], meta["lng"])


def estimate_for_commune(commune: str) -> dict | None:
    """Drive/PT estimate from a commune anchor to the office (for spot-checks)."""
    anchor = commune_anchor(commune)
    if anchor is None:
        return None
    return {
        "drive_min": estimate_drive_min(*anchor),
        "pt_min": estimate_pt_min(*anchor),
    }


def _origin(listing: Listing) -> tuple[float, float] | None:
    if listing.lat is not None and listing.lng is not None:
        return (listing.lat, listing.lng)
    return commune_anchor(listing.commune)


def populate_commute_times(session: Session, *, only_missing: bool = True) -> int:
    """Fill drive/PT (and walk-to-school where possible) for active listings.

    Returns the number of listings updated.
    """
    query = session.query(Listing).filter(Listing.is_active.is_(True))
    if only_missing:
        query = query.filter(Listing.drive_time_rush_min.is_(None))

    updated = 0
    for listing in query.all():
        origin = _origin(listing)
        if origin is None:
            logger.debug("No origin for listing %s (commune=%s)", listing.id, listing.commune)
            continue
        olat, olng = origin
        listing.drive_time_rush_min = estimate_drive_min(olat, olng)
        listing.pt_time_rush_min = estimate_pt_min(olat, olng)
        updated += 1

    session.commit()
    logger.info("populate_commute_times: %d listings updated", updated)
    return updated
