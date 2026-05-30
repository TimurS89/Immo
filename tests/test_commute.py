"""Tests for the crude offline commute estimator (no Google Maps)."""

from __future__ import annotations

import pytest

from config.luxembourg import DWS_OFFICE, TARGET_COMMUNES
from src.lux_monitor.commute import (
    MIN_DRIVE_MIN,
    commune_anchor,
    estimate_drive_min,
    estimate_for_commune,
    estimate_pt_min,
    estimate_walk_min,
    haversine_km,
    populate_commute_times,
)
from src.lux_monitor.models import Listing
from src.lux_monitor.schemas import ListingCreate


def test_haversine():
    assert haversine_km(49.6315, 6.1717, 49.6315, 6.1717) == 0.0
    assert haversine_km(0, 0, 1, 0) == pytest.approx(111.19, abs=0.5)


def test_drive_floor_and_type():
    d = estimate_drive_min(DWS_OFFICE.lat, DWS_OFFICE.lng)  # at the office
    assert isinstance(d, int) and d >= MIN_DRIVE_MIN


def test_drive_monotonic_with_distance():
    near = estimate_drive_min(49.6300, 6.1700)  # ~at Kirchberg
    far = estimate_drive_min(49.6300, 6.0200)   # far west
    assert far > near


def test_walk_increases_with_distance():
    a = estimate_walk_min(49.6117, 6.1250, 49.6130, 6.1260)
    b = estimate_walk_min(49.6117, 6.1250, 49.6300, 6.1700)
    assert b > a >= 0


def test_commune_estimates():
    assert estimate_for_commune("Luxembourg")["drive_min"] < estimate_for_commune("Mamer")["drive_min"]
    # Every target commune is within the 60-min PT cap under the crude model.
    for commune in TARGET_COMMUNES:
        est = estimate_for_commune(commune)
        assert 0 < est["drive_min"] < 60
        assert est["pt_min"] < 60
    assert estimate_for_commune("Nowhere") is None


def _listing(commune, *, lat=None, lng=None, ext="x") -> Listing:
    return ListingCreate(
        portal="athome",
        portal_listing_id=f"{commune}-{ext}",
        url="https://athome.lu/x",
        commune=commune,
        listing_type="rent",
        bedrooms=4,
        surface_m2=120.0,
        rent_eur=3000.0,
        lat=lat,
        lng=lng,
        description_raw="A sufficiently long description for the listing.",
        description_lang="fr",
        title="t",
    ).to_orm()


def test_populate_commute_times(lux_session):
    no_coords = _listing("Strassen", ext="1")  # falls back to commune anchor
    with_coords = _listing("Walferdange", lat=49.6600, lng=6.1350, ext="2")
    off_target = _listing("Esch-sur-Alzette", ext="3")  # no anchor, no coords
    lux_session.add_all([no_coords, with_coords, off_target])
    lux_session.commit()

    n = populate_commute_times(lux_session)
    assert n == 2  # off-target skipped (no origin)

    s = lux_session.query(Listing).filter_by(commune="Strassen").one()
    assert s.drive_time_rush_min and s.pt_time_rush_min
    assert s.walk_to_school_min is None  # walk distances are no longer computed

    w = lux_session.query(Listing).filter_by(commune="Walferdange").one()
    assert w.drive_time_rush_min and w.pt_time_rush_min
    assert w.walk_to_school_min is None

    off = lux_session.query(Listing).filter_by(commune="Esch-sur-Alzette").one()
    assert off.drive_time_rush_min is None

    # only_missing -> nothing left to do.
    assert populate_commute_times(lux_session) == 0


def test_commune_anchor():
    assert commune_anchor("Luxembourg") == (49.6116, 6.1319)
    assert commune_anchor("Nowhere") is None
