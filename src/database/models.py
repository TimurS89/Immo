"""SQLAlchemy models for property data."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Property(Base):
    __tablename__ = "properties"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    external_id = Column(String(255), nullable=False)
    # `source` and `country` are plain strings, not DB enums: the set of valid
    # values is a country-scoped registry (see src/config.py CountryConfig.portals)
    # so adding a country/portal (e.g. Luxembourg) never requires a schema change.
    # Validation lives in the application/config layer, not a frozen CHECK constraint.
    source = Column(String(50), nullable=False)
    country = Column(String(2), nullable=False)
    listing_type = Column(Enum("rent", "buy", name="listing_type_enum"), nullable=False)
    property_type = Column(
        Enum("apartment", "house", "land", name="property_type_enum"), nullable=False
    )

    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    price = Column(Float, nullable=True)
    price_per_sqm = Column(Float, nullable=True)
    rooms = Column(Float, nullable=True)
    living_area_sqm = Column(Float, nullable=True)
    plot_area_sqm = Column(Float, nullable=True)

    address_city = Column(String(255), nullable=True)
    address_postal_code = Column(String(20), nullable=True)
    address_street = Column(String(500), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)

    energy_rating = Column(String(10), nullable=True)
    year_built = Column(Integer, nullable=True)
    floor = Column(Integer, nullable=True)
    has_balcony = Column(Boolean, default=False)
    has_garden = Column(Boolean, default=False)
    has_garage = Column(Boolean, default=False)
    has_elevator = Column(Boolean, default=False)

    image_urls = Column(JSON, default=list)
    listing_url = Column(String(1000), nullable=False)
    contact_info = Column(String(500), nullable=True)

    first_seen_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_seen_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    is_active = Column(Boolean, default=True)

    raw_data = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    price_history = relationship("PriceHistory", back_populates="property", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_source_external_id", "source", "external_id", unique=True),
        Index("ix_country_listing_type", "country", "listing_type"),
        Index("ix_is_active", "is_active"),
        Index("ix_address_postal_code", "address_postal_code"),
    )


class PriceHistory(Base):
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    property_id = Column(String(36), ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    price = Column(Float, nullable=False)
    recorded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    property = relationship("Property", back_populates="price_history")

    __table_args__ = (
        Index("ix_price_history_property_id", "property_id"),
    )


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(50), nullable=False)
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)
    listings_found = Column(Integer, default=0)
    new_listings = Column(Integer, default=0)
    updated_listings = Column(Integer, default=0)
    errors = Column(Integer, default=0)
    status = Column(Enum("running", "success", "partial", "failed", name="run_status_enum"), default="running")
    log = Column(Text, nullable=True)


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(DateTime, nullable=False)
    country = Column(String(2), nullable=False)
    listing_type = Column(String(10), nullable=False)
    property_type = Column(String(20), nullable=False)
    avg_price = Column(Float, nullable=True)
    median_price = Column(Float, nullable=True)
    avg_price_per_sqm = Column(Float, nullable=True)
    median_price_per_sqm = Column(Float, nullable=True)
    total_listings = Column(Integer, default=0)
    new_listings_this_week = Column(Integer, default=0)
    removed_listings_this_week = Column(Integer, default=0)

    __table_args__ = (
        Index("ix_snapshot_date_country", "snapshot_date", "country", "listing_type", "property_type"),
    )
