"""Tests for recent-activity helpers (new listings + price drops)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.lux_monitor.digest import (
    days_on_market,
    long_on_market,
    new_listings,
    price_drops,
)
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


def test_days_on_market_active(lux_session):
    fresh = _mk("a", first_seen_at=_naive_utc_days_ago(10))
    # active -> now - first_seen (allow boundary: micros elapse -> 9 or 10)
    assert days_on_market(fresh) in (9, 10)


def test_days_on_market_delisted(lux_session):
    gone = _mk("g")
    gone.first_seen_at = _naive_utc_days_ago(40)
    gone.last_seen_at = _naive_utc_days_ago(15)
    gone.is_active = False
    assert days_on_market(gone) == 25  # delisted -> lifetime (last - first)


def test_long_on_market(lux_session):
    old = _mk("old", first_seen_at=_naive_utc_days_ago(90))
    recent = _mk("recent", first_seen_at=_naive_utc_days_ago(20))
    lux_session.add_all([old, recent])
    lux_session.commit()

    rows = long_on_market(lux_session, min_days=60)
    assert [l.portal_listing_id for l, _ in rows] == ["old"]
    assert rows[0][1] >= 90  # days value returned


def test_buy_vs_rent_by_commune(lux_session):
    from src.lux_monitor.digest import buy_vs_rent_by_commune

    def mk(lt, commune, price, ext, surface=120):
        o = ListingCreate(
            portal="athome", portal_listing_id=ext, url="https://x",
            commune=commune, listing_type=lt, bedrooms=4, surface_m2=surface,
            price_eur=price if lt == "buy" else None,
            rent_eur=None if lt == "buy" else price,
            description_raw="a long enough description here", description_lang="fr", title="t",
        ).to_orm()
        o.score_total = 50.0
        return o

    lux_session.add_all([
        mk("rent", "Strassen", 3500, "r1"),
        mk("rent", "Strassen", 3700, "r2"),
        mk("furnished", "Strassen", 9000, "f1"),   # must NOT pull median rent up
        mk("buy", "Strassen", 1_200_000, "b1"),
        mk("buy", "Strassen", 1_400_000, "b2"),
    ])
    lux_session.commit()

    rows = {r.commune: r for r in buy_vs_rent_by_commune(lux_session, annual_rate_pct=3.5,
                                                         term_years=30, financing_pct=100)}
    s = rows["Strassen"]
    assert s.n_rent == 2          # furnished excluded from the rent bucket
    assert s.median_rent == 3600  # median(3500, 3700), NOT pulled up by the 9000 furnished
    assert s.n_buy == 2 and s.median_mortgage is not None
    assert s.delta == s.median_mortgage - s.median_rent
