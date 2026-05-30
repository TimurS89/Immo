"""Base class for Luxembourg portal scrapers (targets the lux_monitor schema)."""

from __future__ import annotations

import asyncio
import logging
import random
from abc import ABC, abstractmethod

from sqlalchemy.orm import Session

from src.config import AppConfig
from src.lux_monitor.models import Listing
from src.lux_monitor.schemas import ListingCreate

logger = logging.getLogger(__name__)

# Polite identification for a low-volume personal monitor.
USER_AGENT = "ImmoLuxMonitor/1.0 (personal property search; low volume)"

# Scraped factual fields refreshed on re-scrape. Deliberately excludes computed
# fields (commute/walk/llm/score), user workflow fields, price_history, and
# duplicate_of_id so re-scraping never clobbers enrichment or user edits.
UPDATABLE_FIELDS = (
    "title", "url", "address_text", "postcode", "lat", "lng", "listing_type",
    "bedrooms", "rooms_total", "surface_m2", "floor", "has_elevator", "has_garage",
    "parking_spaces", "has_garden", "garden_m2", "has_balcony_terrace",
    "construction_year", "renovation_year", "rent_eur", "charges_eur",
    "rent_total_eur", "deposit_months", "price_eur", "price_per_m2_eur",
    "energy_class", "thermal_class", "annual_energy_cost_eur", "taxe_fonciere_eur",
    "description_raw", "description_lang", "photos_urls", "listing_agency",
)


def _current_price(listing: Listing) -> tuple[float | None, float | None]:
    """Return (price, charges) for price-history tracking, by listing type."""
    if listing.listing_type == "rent":
        price = listing.rent_total_eur if listing.rent_total_eur is not None else listing.rent_eur
        return price, listing.charges_eur
    return listing.price_eur, None


class LuxBaseScraper(ABC):
    SOURCE_NAME: str = ""
    COUNTRY: str = "LU"
    BASE_URL: str = ""
    REQUEST_DELAY_S: float = 1.5  # polite default between requests

    def __init__(self, config: AppConfig):
        self.config = config
        self.logger = logging.getLogger(f"{__name__}.{self.SOURCE_NAME}")

    def communes(self) -> list[str]:
        """Target commune names for LU (from the country registry)."""
        lu = self.config.search_areas.get("LU")
        if not lu or not lu.enabled:
            return []
        return [a.name for a in lu.areas]

    @property
    def max_pages(self) -> int:
        return self.config.scrapers.max_pages_per_source

    async def random_delay(self) -> None:
        await asyncio.sleep(self.REQUEST_DELAY_S + random.uniform(0, 0.5))

    @abstractmethod
    async def scrape(self) -> list[ListingCreate]:
        """Run the scraper, returning validated ListingCreate objects."""
        ...

    # --- persistence -----------------------------------------------------------
    def save_listings(self, session: Session, listings: list[ListingCreate]) -> dict:
        """Upsert listings by (portal, portal_listing_id) and track price history.

        Records a PriceHistoryEntry on price changes, refreshes scraped facts
        (without clobbering enrichment/user fields), and marks this portal's
        previously-active listings that weren't seen this run as inactive.
        """
        new = updated = 0
        seen_ids: list[int] = []

        for data in listings:
            incoming = data.to_orm()
            existing = (
                session.query(Listing)
                .filter_by(portal=data.portal, portal_listing_id=data.portal_listing_id)
                .first()
            )

            if existing is None:
                session.add(incoming)
                session.flush()
                price, charges = _current_price(incoming)
                if price is not None:
                    incoming.record_price(price=price, charges=charges)
                seen_ids.append(incoming.id)
                new += 1
                continue

            old_price, _ = _current_price(existing)
            for attr in UPDATABLE_FIELDS:
                val = getattr(incoming, attr)
                if val is not None and getattr(existing, attr) != val:
                    setattr(existing, attr, val)
            existing.touch()
            new_price, new_charges = _current_price(existing)
            if new_price is not None and new_price != old_price:
                existing.record_price(price=new_price, charges=new_charges)
            seen_ids.append(existing.id)
            updated += 1

        deactivated = 0
        if seen_ids:
            stale = (
                session.query(Listing)
                .filter(
                    Listing.portal == self.SOURCE_NAME,
                    Listing.is_active.is_(True),
                    ~Listing.id.in_(seen_ids),
                )
                .all()
            )
            for s in stale:
                s.mark_inactive()
                deactivated += 1

        session.commit()
        self.logger.info(
            "%s: %d new, %d updated, %d deactivated",
            self.SOURCE_NAME, new, updated, deactivated,
        )
        return {"new": new, "updated": updated, "deactivated": deactivated}
