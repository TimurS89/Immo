"""Base scraper class and shared data structures."""

from __future__ import annotations

import asyncio
import logging
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from src.config import AppConfig, Filters, SearchArea
from src.database.models import PriceHistory, Property, ScrapeRun

logger = logging.getLogger(__name__)


@dataclass
class PropertyData:
    """Intermediate data structure for scraped property listings."""

    external_id: str
    source: str
    country: str
    listing_type: str
    property_type: str
    title: str
    price: float | None = None
    rooms: float | None = None
    living_area_sqm: float | None = None
    plot_area_sqm: float | None = None
    address_city: str | None = None
    address_postal_code: str | None = None
    address_street: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    energy_rating: str | None = None
    year_built: int | None = None
    floor: int | None = None
    has_balcony: bool = False
    has_garden: bool = False
    has_garage: bool = False
    has_elevator: bool = False
    image_urls: list[str] = field(default_factory=list)
    listing_url: str = ""
    description: str | None = None
    contact_info: str | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)


class BaseScraper(ABC):
    """Abstract base class for all property scrapers."""

    SOURCE_NAME: str = ""
    COUNTRY: str = ""
    BASE_URL: str = ""

    def __init__(self, config: AppConfig):
        self.config = config
        self.delay_range = config.scrapers.request_delay_seconds
        self.max_pages = config.scrapers.max_pages_per_source
        self.logger = logging.getLogger(f"{__name__}.{self.SOURCE_NAME}")

    async def random_delay(self) -> None:
        """Wait a random amount of time between requests."""
        delay = random.uniform(self.delay_range[0], self.delay_range[1])
        self.logger.debug(f"Waiting {delay:.1f}s before next request")
        await asyncio.sleep(delay)

    def get_filters(self, listing_type: str) -> Filters:
        """Get the appropriate filter set for buy or rent."""
        return self.config.filters_buy if listing_type == "buy" else self.config.filters_rent

    def get_search_areas(self) -> list[SearchArea]:
        """Get the search areas for this scraper's country."""
        if self.COUNTRY == "DE":
            return self.config.search_germany
        return self.config.search_france

    @abstractmethod
    async def scrape(self) -> list[PropertyData]:
        """Run the scraper and return a list of property data."""
        ...

    def save_results(self, session: Session, properties: list[PropertyData]) -> ScrapeRun:
        """Save scraped properties to the database. Returns the ScrapeRun record."""
        run = ScrapeRun(
            source=self.SOURCE_NAME,
            started_at=datetime.utcnow(),
            listings_found=len(properties),
        )
        session.add(run)

        new_count = 0
        updated_count = 0
        error_count = 0

        for prop_data in properties:
            try:
                existing = (
                    session.query(Property)
                    .filter_by(source=prop_data.source, external_id=prop_data.external_id)
                    .first()
                )

                if existing:
                    # Update existing property
                    price_changed = existing.price != prop_data.price and prop_data.price is not None
                    for attr in [
                        "title", "price", "rooms", "living_area_sqm", "plot_area_sqm",
                        "address_city", "address_postal_code", "address_street",
                        "latitude", "longitude", "energy_rating", "year_built",
                        "floor", "has_balcony", "has_garden", "has_garage", "has_elevator",
                        "image_urls", "listing_url", "description", "contact_info", "raw_data",
                    ]:
                        new_val = getattr(prop_data, attr)
                        if new_val is not None:
                            setattr(existing, attr, new_val)
                    existing.last_seen_at = datetime.utcnow()
                    existing.is_active = True

                    # Recalculate price per sqm
                    if existing.price and existing.living_area_sqm:
                        existing.price_per_sqm = existing.price / existing.living_area_sqm

                    # Track price change
                    if price_changed:
                        session.add(PriceHistory(
                            property_id=existing.id,
                            price=prop_data.price,
                        ))
                    updated_count += 1
                else:
                    # Create new property
                    price_per_sqm = None
                    if prop_data.price and prop_data.living_area_sqm:
                        price_per_sqm = prop_data.price / prop_data.living_area_sqm

                    new_prop = Property(
                        external_id=prop_data.external_id,
                        source=prop_data.source,
                        country=prop_data.country,
                        listing_type=prop_data.listing_type,
                        property_type=prop_data.property_type,
                        title=prop_data.title,
                        description=prop_data.description,
                        price=prop_data.price,
                        price_per_sqm=price_per_sqm,
                        rooms=prop_data.rooms,
                        living_area_sqm=prop_data.living_area_sqm,
                        plot_area_sqm=prop_data.plot_area_sqm,
                        address_city=prop_data.address_city,
                        address_postal_code=prop_data.address_postal_code,
                        address_street=prop_data.address_street,
                        latitude=prop_data.latitude,
                        longitude=prop_data.longitude,
                        energy_rating=prop_data.energy_rating,
                        year_built=prop_data.year_built,
                        floor=prop_data.floor,
                        has_balcony=prop_data.has_balcony,
                        has_garden=prop_data.has_garden,
                        has_garage=prop_data.has_garage,
                        has_elevator=prop_data.has_elevator,
                        image_urls=prop_data.image_urls,
                        listing_url=prop_data.listing_url,
                        contact_info=prop_data.contact_info,
                        raw_data=prop_data.raw_data,
                    )
                    session.add(new_prop)
                    session.flush()

                    # Initial price history entry
                    if prop_data.price is not None:
                        session.add(PriceHistory(
                            property_id=new_prop.id,
                            price=prop_data.price,
                        ))
                    new_count += 1

            except Exception:
                self.logger.exception(f"Error saving property {prop_data.external_id}")
                error_count += 1

        run.completed_at = datetime.utcnow()
        run.new_listings = new_count
        run.updated_listings = updated_count
        run.errors = error_count
        run.status = "success" if error_count == 0 else "partial"

        session.commit()
        self.logger.info(
            f"{self.SOURCE_NAME}: {new_count} new, {updated_count} updated, {error_count} errors"
        )
        return run
