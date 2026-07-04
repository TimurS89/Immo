"""Tests for daily market snapshots."""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta

from src.lux_monitor.models import MarketSnapshot
from src.lux_monitor.schemas import ListingCreate
from src.lux_monitor.snapshots import ALL_COMMUNES, record_snapshot

_ids = itertools.count(1)


def _orm(listing_type="buy", commune="Luxembourg", *, surface=100.0, price=1_000_000.0):
    from src.lux_monitor.scoring import passes_hard_filter, score_listing

    obj = ListingCreate(
        portal="athome",
        portal_listing_id=f"s{next(_ids)}",
        url="https://athome.lu/x",
        commune=commune,
        listing_type=listing_type,
        bedrooms=4,
        surface_m2=surface,
        price_eur=price if listing_type == "buy" else None,
        rent_eur=None if listing_type == "buy" else price,
        description_raw="a long enough description here",
        description_lang="fr",
        title="t",
    ).to_orm()
    # Mirror the pipeline: apply_scores sets score_total (float ⇔ passes filter,
    # None otherwise) BEFORE record_snapshot runs, which snapshots keys off.
    obj.score_total = score_listing(obj).total if passes_hard_filter(obj).passed else None
    return obj


def test_snapshot_aggregates_per_segment(lux_session):
    lux_session.add_all([
        _orm("buy", "Luxembourg", surface=100, price=1_000_000),
        _orm("buy", "Luxembourg", surface=200, price=2_000_000),
        _orm("rent", "Strassen", surface=120, price=3_000),
    ])
    lux_session.commit()

    written = record_snapshot(lux_session)
    # buy: (Luxembourg + All), rent: (Strassen + All) = 4 segments
    assert written == 4

    lux_buy = lux_session.query(MarketSnapshot).filter_by(
        listing_type="buy", commune="Luxembourg").one()
    assert lux_buy.count == 2
    assert lux_buy.median_price_eur == 1_500_000  # median of 1M, 2M
    assert lux_buy.median_price_per_m2_eur == 10_000  # median of 10000, 10000

    all_buy = lux_session.query(MarketSnapshot).filter_by(
        listing_type="buy", commune=ALL_COMMUNES).one()
    assert all_buy.count == 2  # rollup of all buy listings

    rent = lux_session.query(MarketSnapshot).filter_by(listing_type="rent").filter(
        MarketSnapshot.commune == "Strassen").one()
    assert rent.count == 1 and rent.median_price_eur == 3_000


def test_snapshot_excludes_nonmatching(lux_session):
    lux_session.add_all([
        _orm("buy", "Luxembourg", surface=100, price=1_000_000),   # ok
        _orm("buy", "Esch-sur-Alzette", surface=100, price=1_000_000),  # off-target
        _orm("buy", "Luxembourg", surface=50, price=1_000_000),    # < 80 m²
    ])
    lux_session.commit()
    record_snapshot(lux_session)
    seg = lux_session.query(MarketSnapshot).filter_by(
        listing_type="buy", commune="Luxembourg").one()
    assert seg.count == 1  # only the matching one counted


def test_snapshot_idempotent_per_day(lux_session):
    lux_session.add(_orm("buy", "Luxembourg"))
    lux_session.commit()

    when = datetime(2026, 6, 2, 9, 0)
    record_snapshot(lux_session, when=when)
    record_snapshot(lux_session, when=when.replace(hour=18))  # same day, later

    # one row per segment for that day (not duplicated)
    rows = lux_session.query(MarketSnapshot).filter_by(
        listing_type="buy", commune="Luxembourg").all()
    assert len(rows) == 1


def test_snapshot_series_accumulates(lux_session):
    lux_session.add(_orm("buy", "Luxembourg", price=1_000_000))
    lux_session.commit()
    record_snapshot(lux_session, when=datetime(2026, 6, 1, 9, 0))
    record_snapshot(lux_session, when=datetime(2026, 6, 2, 9, 0))

    dates = [r.snapshot_date for r in lux_session.query(MarketSnapshot).filter_by(
        listing_type="buy", commune="Luxembourg").order_by(MarketSnapshot.snapshot_date).all()]
    assert len(dates) == 2  # a 2-point time series


def test_new_count_excludes_prior_day_arrivals(lux_session):
    """new_count must count only listings first seen AFTER the prior snapshot day,
    not everything from that day's midnight (the over-count bug)."""
    from datetime import datetime

    # A listing first seen on day 1 at 14:00.
    old = _orm("buy", "Luxembourg", surface=120, price=1_000_000)
    old.first_seen_at = datetime(2026, 6, 1, 14, 0)
    lux_session.add(old)
    lux_session.commit()

    # Day-1 snapshot.
    record_snapshot(lux_session, when=datetime(2026, 6, 1, 18, 0))

    # Day-2 snapshot, no new arrivals.
    record_snapshot(lux_session, when=datetime(2026, 6, 2, 18, 0))
    seg = lux_session.query(MarketSnapshot).filter_by(
        listing_type="buy", commune="Luxembourg",
        snapshot_date=datetime(2026, 6, 2)).one()
    # 'old' arrived during day 1 (already counted then) -> NOT new on day 2.
    assert seg.new_count == 0
    assert seg.count == 1  # still in the market
