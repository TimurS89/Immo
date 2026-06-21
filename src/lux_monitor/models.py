"""SQLAlchemy 2.0 data model for the Luxembourg property monitor.

Design notes
------------
* **Validation lives in Pydantic** (`schemas.py`), not in the DB. Whitelisted
  string fields (``portal``, ``listing_type``, ``description_lang``,
  ``energy_class`` ...) are stored as plain ``String`` columns rather than
  DB-level ``Enum``/CHECK constraints. This keeps the schema flexible — adding a
  new portal never requires a migration — and avoids the rigid-enum problem
  documented in ADAPTATION_PLAN.md (the old tool's frozen ``source`` enum).
* **JSON list/dict columns** use ``MutableList.as_mutable(JSON)`` so in-place
  ``.append(...)`` is tracked by the session (otherwise mutations wouldn't be
  flushed).
* **Price tracking uses both** a normalized side table and a JSON log:
  ``PriceHistoryEntry`` is the queryable source of truth; ``Listing.price_history``
  keeps a denormalized ``[{date, price, charges}]`` log for cheap read access.
  Both are written through the single helper ``Listing.record_price()`` so they
  never drift.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# --- Allowed values (enforced in the Pydantic layer, see schemas.py) ---
PORTALS: tuple[str, ...] = ("athome", "immotop", "wortimmo", "nexvia")
LISTING_TYPES: tuple[str, ...] = ("furnished", "rent", "buy")
DESCRIPTION_LANGS: tuple[str, ...] = ("fr", "de", "en")
ENERGY_CLASSES: tuple[str, ...] = tuple("ABCDEFGHI")  # Luxembourg passeport énergétique


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Listing(Base):
    """A single property listing scraped from a Luxembourg portal."""

    __tablename__ = "listings"

    # --- Identity ---
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portal: Mapped[str] = mapped_column(String(32), nullable=False)
    portal_listing_id: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)

    # --- Location ---
    commune: Mapped[str] = mapped_column(String(128), nullable=False)
    address_text: Mapped[str | None] = mapped_column(String(512))  # often partial
    postcode: Mapped[str | None] = mapped_column(String(16))
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)

    # --- Property core ---
    listing_type: Mapped[str] = mapped_column(String(8), nullable=False)  # rent | buy
    bedrooms: Mapped[int] = mapped_column(Integer, nullable=False)  # chambres
    rooms_total: Mapped[int | None] = mapped_column(Integer)  # pièces, if reported
    surface_m2: Mapped[float] = mapped_column(Float, nullable=False)
    floor: Mapped[int | None] = mapped_column(Integer)  # 0 = ground, -1 = below
    has_elevator: Mapped[bool | None] = mapped_column(Boolean)
    has_garage: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    parking_spaces: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    has_garden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    garden_m2: Mapped[float | None] = mapped_column(Float)
    has_balcony_terrace: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    construction_year: Mapped[int | None] = mapped_column(Integer)
    renovation_year: Mapped[int | None] = mapped_column(Integer)

    # --- Pricing (rent) ---
    rent_eur: Mapped[float | None] = mapped_column(Float)  # loyer, excl. charges
    charges_eur: Mapped[float | None] = mapped_column(Float)  # monthly charges
    rent_total_eur: Mapped[float | None] = mapped_column(Float)  # rent + charges
    deposit_months: Mapped[int | None] = mapped_column(Integer)  # typically 2 or 3

    # --- Pricing (buy) ---
    price_eur: Mapped[float | None] = mapped_column(Float)
    price_per_m2_eur: Mapped[float | None] = mapped_column(Float)

    # --- Energy ---
    energy_class: Mapped[str | None] = mapped_column(String(2))  # A–I
    thermal_class: Mapped[str | None] = mapped_column(String(2))  # insulation rating
    annual_energy_cost_eur: Mapped[int | None] = mapped_column(Integer)

    # --- Property tax estimate (Luxembourg-specific, usually low ~€100–300/yr) ---
    taxe_fonciere_eur: Mapped[float | None] = mapped_column(Float)

    # --- Description & metadata ---
    description_raw: Mapped[str] = mapped_column(Text, nullable=False)  # original lang
    description_lang: Mapped[str] = mapped_column(String(2), nullable=False)  # fr|de|en
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    photos_urls: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSON), nullable=False, default=list
    )
    listing_agency: Mapped[str | None] = mapped_column(String(256))

    # --- Computed: commute (populated by commute.py) ---
    drive_time_rush_min: Mapped[int | None] = mapped_column(Integer)
    pt_time_rush_min: Mapped[int | None] = mapped_column(Integer)

    # --- LLM analysis (populated by llm.py) ---
    llm_quality_score: Mapped[int | None] = mapped_column(Integer)  # 0–100
    # Stored as JSON lists of strings (the spec comments read "JSON list").
    llm_red_flags: Mapped[list[str] | None] = mapped_column(MutableList.as_mutable(JSON))
    llm_highlights: Mapped[list[str] | None] = mapped_column(MutableList.as_mutable(JSON))
    llm_summary: Mapped[str | None] = mapped_column(Text)

    # --- Workflow tracking ---
    score_total: Mapped[float | None] = mapped_column(Float)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Denormalized log: [{"date": iso, "price": float|None, "charges": float|None}]
    price_history: Mapped[list[dict]] = mapped_column(
        MutableList.as_mutable(JSON), nullable=False, default=list
    )

    # --- User workflow ---
    user_shortlist: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    user_viewing_scheduled: Mapped[datetime | None] = mapped_column(DateTime)
    user_notes: Mapped[str | None] = mapped_column(Text)

    # --- De-duplication: a secondary listing points at its cross-portal primary.
    # Plain indexed id reference (no DB-level FK): SQLite can't ALTER-add a FK and
    # the link is advisory for this personal tool. Set by lux_monitor.dedup.
    duplicate_of_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    # --- Relationships ---
    price_history_entries: Mapped[list["PriceHistoryEntry"]] = relationship(
        back_populates="listing",
        cascade="all, delete-orphan",
        order_by="PriceHistoryEntry.recorded_at",
    )

    __table_args__ = (
        UniqueConstraint("portal", "portal_listing_id", name="uq_portal_listing"),
        Index("ix_listings_listing_type", "listing_type"),
        Index("ix_listings_commune", "commune"),
        Index("ix_listings_is_active", "is_active"),
        Index("ix_listings_portal", "portal"),
    )

    # --- Behavior ---
    def record_price(
        self,
        *,
        price: float | None = None,
        charges: float | None = None,
        when: datetime | None = None,
    ) -> "PriceHistoryEntry":
        """Record a price observation in both the side table and the JSON log.

        Returns the created :class:`PriceHistoryEntry`.
        """
        when = when or _utcnow()
        entry = PriceHistoryEntry(recorded_at=when, price_eur=price, charges_eur=charges)
        self.price_history_entries.append(entry)

        if self.price_history is None:  # default not applied until first flush
            self.price_history = []
        self.price_history.append(
            {"date": when.isoformat(), "price": price, "charges": charges}
        )
        return entry

    def mark_inactive(self, *, when: datetime | None = None) -> None:
        """Mark the listing as delisted/no longer seen."""
        self.is_active = False
        self.last_seen_at = when or _utcnow()

    def touch(self, *, when: datetime | None = None) -> None:
        """Mark the listing as seen again in the current run (re-activates it).

        If it had previously been delisted, this is a *relisting*: reset
        ``first_seen_at`` so "days on market" measures the current continuous
        spell, not a stale original sighting from months ago.
        """
        when = when or _utcnow()
        if self.is_active is False:
            self.first_seen_at = when
        self.is_active = True
        self.last_seen_at = when

    @property
    def compare_price(self) -> float | None:
        """The price used everywhere a buy and a rental must be compared.

        Sale price for buy; monthly total (rent + charges, falling back to bare
        rent) for rentals. Single source of truth — scoring, snapshots, dedup,
        the scrapers and the dashboard all use this so the bases never drift.
        """
        if self.listing_type == "buy":
            return self.price_eur
        if self.rent_total_eur is not None:
            return self.rent_total_eur
        return self.rent_eur

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"<Listing id={self.id} portal={self.portal!r} "
            f"portal_listing_id={self.portal_listing_id!r} "
            f"type={self.listing_type!r} commune={self.commune!r}>"
        )


class PriceHistoryEntry(Base):
    """Normalized, queryable price observation for a :class:`Listing`."""

    __tablename__ = "price_history_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow
    )
    price_eur: Mapped[float | None] = mapped_column(Float)  # sale price or rent
    charges_eur: Mapped[float | None] = mapped_column(Float)

    listing: Mapped["Listing"] = relationship(back_populates="price_history_entries")

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"<PriceHistoryEntry listing_id={self.listing_id} "
            f"price_eur={self.price_eur} at={self.recorded_at!r}>"
        )


class MarketSnapshot(Base):
    """A daily market aggregate per (date, listing_type, commune).

    Per-listing price history only lives as long as a listing is online (weeks to
    months), so it can't show how the *market* moves over a year. This table
    records one row per run per segment — counts and price/m² medians — building
    the long-run trend that informs a rent-vs-buy / now-vs-later decision.
    Computed over active, non-duplicate, filter-passing listings.
    """

    __tablename__ = "market_snapshots_lu"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_date: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    listing_type: Mapped[str] = mapped_column(String(16), nullable=False)  # furnished|rent|buy
    commune: Mapped[str] = mapped_column(String(128), nullable=False)

    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Price = sale price for buy, monthly total for rent/furnished.
    median_price_eur: Mapped[float | None] = mapped_column(Float)
    mean_price_eur: Mapped[float | None] = mapped_column(Float)
    median_price_per_m2_eur: Mapped[float | None] = mapped_column(Float)
    median_surface_m2: Mapped[float | None] = mapped_column(Float)
    new_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # first seen since last snapshot

    __table_args__ = (
        UniqueConstraint("snapshot_date", "listing_type", "commune", name="uq_snapshot_segment"),
        Index("ix_snapshot_type_commune", "listing_type", "commune"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"<MarketSnapshot {self.snapshot_date:%Y-%m-%d} {self.listing_type} "
            f"{self.commune} n={self.count} median={self.median_price_eur}>"
        )

