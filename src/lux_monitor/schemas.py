"""Pydantic v2 schemas for API input/output.

``ListingCreate`` validates inbound data (and converts to an ORM object via
``to_orm()``); ``ListingRead`` serializes ORM objects back out
(``from_attributes=True``). Allowed-value validation that the DB intentionally
does not enforce (see ``models.py``) is applied here.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import (
    DESCRIPTION_LANGS,
    ENERGY_CLASSES,
    LISTING_TYPES,
    PORTALS,
    Listing,
)


class ListingBase(BaseModel):
    """Shared, user/scraper-suppliable fields."""

    # Identity
    portal: str
    portal_listing_id: str
    url: str

    # Location
    commune: str
    address_text: str | None = None
    postcode: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)

    # Property core
    listing_type: str
    bedrooms: int = Field(ge=0)
    rooms_total: int | None = Field(default=None, ge=0)
    surface_m2: float = Field(gt=0)
    floor: int | None = None
    has_elevator: bool | None = None
    has_garage: bool = False
    parking_spaces: int = Field(default=0, ge=0)
    has_garden: bool = False
    garden_m2: float | None = Field(default=None, ge=0)
    has_balcony_terrace: bool = False
    construction_year: int | None = Field(default=None, ge=1700, le=2100)
    renovation_year: int | None = Field(default=None, ge=1700, le=2100)

    # Pricing (rent)
    rent_eur: float | None = Field(default=None, ge=0)
    charges_eur: float | None = Field(default=None, ge=0)
    rent_total_eur: float | None = Field(default=None, ge=0)
    deposit_months: int | None = Field(default=None, ge=0, le=6)

    # Pricing (buy)
    price_eur: float | None = Field(default=None, ge=0)
    price_per_m2_eur: float | None = Field(default=None, ge=0)

    # Energy
    energy_class: str | None = None
    thermal_class: str | None = None
    annual_energy_cost_eur: int | None = Field(default=None, ge=0)

    # Tax
    taxe_fonciere_eur: float | None = Field(default=None, ge=0)

    # Description & metadata
    description_raw: str
    description_lang: str
    title: str
    photos_urls: list[str] = Field(default_factory=list)
    listing_agency: str | None = None

    @field_validator("portal")
    @classmethod
    def _check_portal(cls, v: str) -> str:
        if v not in PORTALS:
            raise ValueError(f"portal must be one of {PORTALS}, got {v!r}")
        return v

    @field_validator("listing_type")
    @classmethod
    def _check_listing_type(cls, v: str) -> str:
        if v not in LISTING_TYPES:
            raise ValueError(f"listing_type must be one of {LISTING_TYPES}, got {v!r}")
        return v

    @field_validator("description_lang")
    @classmethod
    def _check_lang(cls, v: str) -> str:
        if v not in DESCRIPTION_LANGS:
            raise ValueError(
                f"description_lang must be one of {DESCRIPTION_LANGS}, got {v!r}"
            )
        return v

    @field_validator("energy_class", "thermal_class")
    @classmethod
    def _check_energy_class(cls, v: str | None) -> str | None:
        if v is None:
            return None
        normalized = v.strip().upper()
        if normalized not in ENERGY_CLASSES:
            raise ValueError(
                f"energy/thermal class must be one of {ENERGY_CLASSES}, got {v!r}"
            )
        return normalized


class ListingCreate(ListingBase):
    """Inbound payload to create a listing."""

    def to_orm(self) -> Listing:
        """Build a :class:`Listing`, deriving a couple of convenience fields."""
        data = self.model_dump()

        # Derive price_per_m2 for buy listings when not provided.
        if (
            data.get("price_per_m2_eur") is None
            and data.get("price_eur")
            and data.get("surface_m2")
        ):
            data["price_per_m2_eur"] = round(data["price_eur"] / data["surface_m2"], 2)

        # Derive total rent when not provided.
        if data.get("rent_total_eur") is None and data.get("rent_eur") is not None:
            data["rent_total_eur"] = (data["rent_eur"] or 0.0) + (
                data.get("charges_eur") or 0.0
            )

        return Listing(**data)


class PriceHistoryEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    listing_id: int
    recorded_at: datetime
    price_eur: float | None = None
    charges_eur: float | None = None


class ListingRead(ListingBase):
    """Outbound representation of a stored listing (read from the ORM)."""

    model_config = ConfigDict(from_attributes=True)

    id: int

    # Computed (commute)
    drive_time_rush_min: int | None = None
    pt_time_rush_min: int | None = None

    # LLM analysis
    llm_quality_score: int | None = None
    llm_red_flags: list[str] | None = None
    llm_highlights: list[str] | None = None
    llm_summary: str | None = None

    # Workflow tracking
    score_total: float | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    is_active: bool
    price_history: list[dict] = Field(default_factory=list)

    # User workflow
    user_shortlist: bool = False
    user_viewing_scheduled: datetime | None = None
    user_notes: str | None = None

    # Normalized history (side table)
    price_history_entries: list[PriceHistoryEntryRead] = Field(default_factory=list)
