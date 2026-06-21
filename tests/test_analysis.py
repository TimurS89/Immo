"""Tests for analysis module.

LEGACY (Germany/France stack). These exercise src/analysis + src/database, which
depend on heavy deps (thefuzz, …) that the lean Luxembourg install omits. Skip the
whole module cleanly when those deps aren't present, rather than erroring at
collection time.
"""

import pytest

pytest.importorskip("thefuzz", reason="legacy DE/FR stack dep not installed (lean LU install)")

from datetime import datetime, timedelta
from src.database.db import init_db, get_session, get_engine
from src.database.models import Base, Property, PriceHistory
from src.analysis.deduplication import _is_duplicate


# Use in-memory SQLite for tests
TEST_DB = ":memory:"


@pytest.fixture
def session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _make_property(**kwargs):
    defaults = dict(
        external_id="test",
        source="test",
        country="DE",
        listing_type="buy",
        property_type="apartment",
        title="Test",
        listing_url="https://example.com/test",
        is_active=True,
    )
    defaults.update(kwargs)
    return Property(**defaults)


def test_duplicate_detection_same_property():
    a = _make_property(
        external_id="a", source="immoscout24",
        price=250000, living_area_sqm=80, rooms=3,
        address_postal_code="76530", address_city="Baden-Baden",
    )
    b = _make_property(
        external_id="b", source="immowelt",
        price=249000, living_area_sqm=80, rooms=3,
        address_postal_code="76530", address_city="Baden-Baden",
    )
    assert _is_duplicate(a, b)


def test_no_duplicate_different_price():
    a = _make_property(
        external_id="a", source="immoscout24",
        price=250000, living_area_sqm=80, rooms=3,
    )
    b = _make_property(
        external_id="b", source="immowelt",
        price=400000, living_area_sqm=80, rooms=3,
    )
    assert not _is_duplicate(a, b)


def test_no_duplicate_different_rooms():
    a = _make_property(
        external_id="a", source="immoscout24",
        price=250000, living_area_sqm=80, rooms=3,
    )
    b = _make_property(
        external_id="b", source="immowelt",
        price=250000, living_area_sqm=80, rooms=5,
    )
    assert not _is_duplicate(a, b)


def test_save_property(session):
    prop = _make_property(price=300000, living_area_sqm=100)
    session.add(prop)
    session.commit()

    loaded = session.query(Property).first()
    assert loaded.price == 300000
    assert loaded.is_active is True
