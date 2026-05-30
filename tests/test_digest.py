"""Tests for recent-activity helpers (new listings + price drops)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.lux_monitor.digest import new_listings, price_drops
from src.lux_monitor.schemas import ListingCreate


def _naive_utc_days_ago(days: int) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)


def _mk(ext: str, **over):
    obj = ListingCreate(
        portal="athome",
        portal_listing_id=ext,
        url="https://athome.lu/x",
        commune="Luxembourg",
        listing_type="rent",
        bedrooms=4,
        surface_m2=120.0,
        rent_eur=3000.0,
        description_raw="a long enough description here",
        description_lang="fr",
        title="t",
    ).to_orm()
    obj.score_total = 50.0
    for key, value in over.items():
        setattr(obj, key, value)
    return obj


def test_new_listings(lux_session):
    old = _mk("old", first_seen_at=_naive_utc_days_ago(30))
    fresh = _mk("fresh", first_seen_at=_naive_utc_days_ago(1))
    unscored = _mk("uns", first_seen_at=_naive_utc_days_ago(0), score_total=None)
    lux_session.add_all([old, fresh, unscored])
    lux_session.commit()

    ids = {l.portal_listing_id for l in new_listings(lux_session, days=7)}
    assert ids == {"fresh"}  # 'old' too old; 'uns' not scored

    # include unscored when asked
    ids2 = {l.portal_listing_id for l in new_listings(lux_session, days=7, scored_only=False)}
    assert ids2 == {"fresh", "uns"}


def test_price_drops(lux_session):
    dropper = _mk("drop")
    riser = _mk("rise")
    flat = _mk("flat")  # only one price point -> ignored
    lux_session.add_all([dropper, riser, flat])
    lux_session.commit()

    dropper.record_price(price=3000)
    dropper.record_price(price=2700)  # -10%
    riser.record_price(price=2000)
    riser.record_price(price=2200)   # increase -> not a drop
    flat.record_price(price=2500)
    lux_session.commit()

    drops = price_drops(lux_session, days=30)
    assert len(drops) == 1
    d = drops[0]
    assert d.listing.portal_listing_id == "drop"
    assert d.old_price == 3000 and d.new_price == 2700
    assert d.pct == -10.0


def test_price_drop_outside_window_ignored(lux_session):
    listing = _mk("old-drop")
    lux_session.add(listing)
    lux_session.commit()
    listing.record_price(price=3000, when=_naive_utc_days_ago(60))
    listing.record_price(price=2700, when=_naive_utc_days_ago(45))  # dropped 45d ago
    lux_session.commit()

    assert price_drops(lux_session, days=30) == []  # drop is older than the window
