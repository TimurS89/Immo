"""Tests for the Luxembourg property monitor data model (src/lux_monitor)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from src.lux_monitor.db import PROJECT_ROOT, get_engine, init_db, make_session_factory
from src.lux_monitor.models import Listing, PriceHistoryEntry
from src.lux_monitor.schemas import ListingCreate, ListingRead


@pytest.fixture
def session(tmp_path):
    """A session bound to a throwaway file-based SQLite DB."""
    engine = get_engine(url=f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    sess = make_session_factory(engine)()
    try:
        yield sess
    finally:
        sess.close()
        engine.dispose()


def _sample_create(**overrides) -> ListingCreate:
    data = dict(
        portal="athome",
        portal_listing_id="A-123",
        url="https://www.athome.lu/a-123",
        commune="Walferdange",
        listing_type="rent",
        bedrooms=4,
        surface_m2=120.0,
        rent_eur=3200.0,
        charges_eur=300.0,
        description_raw="Bel appartement de 4 chambres avec jardin.",
        description_lang="fr",
        title="Appartement 4 chambres - Walferdange",
        photos_urls=["https://img/1.jpg", "https://img/2.jpg"],
        energy_class="b",  # lower-case on purpose: validator should normalize
    )
    data.update(overrides)
    return ListingCreate(**data)


# --- create -------------------------------------------------------------------

def test_create_listing(session):
    listing = _sample_create().to_orm()
    session.add(listing)
    session.commit()

    loaded = session.query(Listing).one()
    assert loaded.id is not None
    assert loaded.portal == "athome"
    assert loaded.commune == "Walferdange"
    assert loaded.bedrooms == 4
    assert loaded.is_active is True
    assert loaded.first_seen_at is not None and loaded.last_seen_at is not None
    assert loaded.photos_urls == ["https://img/1.jpg", "https://img/2.jpg"]
    assert loaded.price_history == []  # JSON default applied
    # Derived fields from to_orm()
    assert loaded.rent_total_eur == 3500.0  # 3200 + 300
    assert loaded.energy_class == "B"  # normalized to upper-case


def test_create_buy_derives_price_per_m2(session):
    listing = _sample_create(
        listing_type="buy",
        rent_eur=None,
        charges_eur=None,
        price_eur=900_000.0,
        surface_m2=150.0,
    ).to_orm()
    session.add(listing)
    session.commit()
    assert listing.price_per_m2_eur == pytest.approx(6000.0)


def test_listing_read_round_trip(session):
    listing = _sample_create().to_orm()
    session.add(listing)
    session.commit()

    read = ListingRead.model_validate(listing)
    assert read.id == listing.id
    assert read.title == listing.title
    assert read.is_active is True
    assert read.price_history_entries == []  # no history yet


def test_unique_portal_listing_id(session):
    session.add(_sample_create().to_orm())
    session.commit()
    session.add(_sample_create().to_orm())  # same (portal, portal_listing_id)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# --- update price history -----------------------------------------------------

def test_record_price_history(session):
    listing = _sample_create().to_orm()
    session.add(listing)
    session.commit()

    t1 = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 2, 1, 8, 0, tzinfo=timezone.utc)
    listing.record_price(price=3200.0, charges=300.0, when=t1)
    listing.record_price(price=3000.0, charges=300.0, when=t2)  # price drop
    session.commit()

    # Side table is the queryable source of truth.
    entries = (
        session.query(PriceHistoryEntry)
        .filter_by(listing_id=listing.id)
        .order_by(PriceHistoryEntry.recorded_at)
        .all()
    )
    assert len(entries) == 2
    assert entries[0].price_eur == 3200.0
    assert entries[1].price_eur == 3000.0

    # Denormalized JSON log kept in sync by record_price().
    assert len(listing.price_history) == 2
    assert listing.price_history[-1]["price"] == 3000.0
    assert listing.price_history[-1]["date"] == t2.isoformat()

    # ListingRead surfaces both representations.
    read = ListingRead.model_validate(listing)
    assert len(read.price_history_entries) == 2
    assert len(read.price_history) == 2


def test_price_history_cascade_delete(session):
    listing = _sample_create().to_orm()
    session.add(listing)
    session.commit()
    listing.record_price(price=3200.0)
    session.commit()
    assert session.query(PriceHistoryEntry).count() == 1

    session.delete(listing)
    session.commit()
    # orphan cleanup via cascade
    assert session.query(PriceHistoryEntry).count() == 0


# --- mark inactive ------------------------------------------------------------

def test_mark_inactive(session):
    listing = _sample_create().to_orm()
    session.add(listing)
    session.commit()
    assert listing.is_active is True

    listing.mark_inactive()
    session.commit()

    loaded = session.get(Listing, listing.id)
    assert loaded.is_active is False


def test_touch_reactivates(session):
    listing = _sample_create().to_orm()
    session.add(listing)
    session.commit()
    listing.mark_inactive()
    session.commit()
    assert listing.is_active is False

    listing.touch()
    session.commit()
    assert session.get(Listing, listing.id).is_active is True


# --- validation (Pydantic boundary) ------------------------------------------

@pytest.mark.parametrize(
    "overrides",
    [
        {"portal": "zillow"},          # not a known LU portal
        {"listing_type": "lease"},     # not rent|buy
        {"description_lang": "lu"},    # not fr|de|en
        {"surface_m2": 0},             # must be > 0
        {"bedrooms": -1},              # must be >= 0
        {"energy_class": "Z"},         # outside A-I
        {"deposit_months": 12},        # outside 0..6
    ],
)
def test_validation_rejects_bad_input(overrides):
    with pytest.raises(Exception):  # pydantic.ValidationError
        _sample_create(**overrides)


# --- alembic migration --------------------------------------------------------

def test_alembic_migration_applies(tmp_path):
    """The committed initial migration builds the expected schema from scratch."""
    alembic_command = pytest.importorskip("alembic.command")
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    db_file = tmp_path / "migrated.db"
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")
    alembic_command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_file}")
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert {"listings", "price_history_entries", "alembic_version"} <= tables


# --- JSON / LLM columns -------------------------------------------------------

def test_llm_json_columns(session):
    listing = _sample_create().to_orm()
    session.add(listing)
    session.commit()

    # Populated later by llm.py — set the JSON-list columns + summary.
    listing.llm_quality_score = 82
    listing.llm_red_flags = ["north-facing", "near motorway"]
    listing.llm_highlights = ["renovated kitchen", "large garden"]
    listing.llm_summary = "Spacious family home, minor noise concern."
    session.commit()

    loaded = session.get(Listing, listing.id)
    assert loaded.llm_quality_score == 82
    assert loaded.llm_red_flags == ["north-facing", "near motorway"]
    assert loaded.llm_highlights == ["renovated kitchen", "large garden"]

    # MutableList tracks in-place append without reassignment.
    loaded.llm_highlights.append("south terrace")
    session.commit()
    assert session.get(Listing, listing.id).llm_highlights[-1] == "south terrace"

    # ListingRead surfaces both JSON lists.
    read = ListingRead.model_validate(loaded)
    assert read.llm_highlights == ["renovated kitchen", "large garden", "south terrace"]
    assert read.llm_red_flags == ["north-facing", "near motorway"]


def test_compare_price_buy_and_rent_fallback():
    from src.lux_monitor.schemas import ListingCreate

    def mk(**over):
        d = dict(portal="athome", portal_listing_id="cp", url="https://x",
                 commune="Luxembourg", listing_type="rent", bedrooms=4, surface_m2=120,
                 description_raw="x" * 20, description_lang="fr", title="t")
        d.update(over)
        return ListingCreate(**d).to_orm()

    # buy -> price_eur
    assert mk(listing_type="buy", price_eur=900_000).compare_price == 900_000
    # rent -> rent_total_eur (rent + charges derived in to_orm)
    r = mk(rent_eur=3000, charges_eur=200)
    assert r.compare_price == 3200
    # rent with only rent_eur (no total) -> falls back to rent_eur
    r2 = mk(rent_eur=2800)
    r2.rent_total_eur = None  # simulate missing total
    assert r2.compare_price == 2800


def test_touch_resets_first_seen_on_relisting():
    from datetime import datetime
    from src.lux_monitor.schemas import ListingCreate

    obj = ListingCreate(portal="athome", portal_listing_id="re", url="https://x",
                        commune="Luxembourg", listing_type="rent", bedrooms=4,
                        surface_m2=120, rent_eur=3000, description_raw="x" * 20,
                        description_lang="fr", title="t").to_orm()
    old = datetime(2026, 1, 1)
    obj.first_seen_at = old
    obj.mark_inactive(when=datetime(2026, 2, 1))  # delisted

    obj.touch(when=datetime(2026, 6, 1))  # relisted months later
    assert obj.is_active is True
    assert obj.first_seen_at == datetime(2026, 6, 1)  # reset, not the old date


def test_touch_keeps_first_seen_when_still_active():
    from datetime import datetime
    from src.lux_monitor.schemas import ListingCreate

    obj = ListingCreate(portal="athome", portal_listing_id="act", url="https://x",
                        commune="Luxembourg", listing_type="rent", bedrooms=4,
                        surface_m2=120, rent_eur=3000, description_raw="x" * 20,
                        description_lang="fr", title="t").to_orm()
    old = datetime(2026, 1, 1)
    obj.first_seen_at = old
    obj.is_active = True  # still active (a normal re-scrape)
    obj.touch(when=datetime(2026, 6, 1))
    assert obj.first_seen_at == old  # unchanged for a continuously-active listing
