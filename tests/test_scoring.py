"""Tests for the hard filter + soft scoring (rooms/surface/commune; commute soft)."""

from __future__ import annotations

import itertools

from config.luxembourg import SCORING_WEIGHTS
from src.lux_monitor.models import Listing
from src.lux_monitor.schemas import ListingCreate
from src.lux_monitor.scoring import (
    NEUTRAL,
    apply_scores,
    passes_hard_filter,
    prune_nonmatching,
    score_listing,
    top_listings,
)

_ids = itertools.count(1)


def _orm(
    commune: str = "Luxembourg",
    listing_type: str = "rent",
    *,
    bedrooms: int = 4,
    rooms_total: int | None = None,
    surface: float = 120.0,
    rent: float = 3000.0,
    price: float = 1_000_000.0,
    drive: int | None = 12,
    pt: int | None = 26,
    **over,
) -> Listing:
    obj = ListingCreate(
        portal="athome",
        portal_listing_id=f"id-{next(_ids)}",
        url="https://athome.lu/x",
        commune=commune,
        listing_type=listing_type,
        bedrooms=bedrooms,
        rooms_total=rooms_total,
        surface_m2=surface,
        rent_eur=rent if listing_type in ("rent", "furnished") else None,
        price_eur=price if listing_type == "buy" else None,
        description_raw="A sufficiently long description for the listing.",
        description_lang="fr",
        title="t",
    ).to_orm()
    obj.drive_time_rush_min = drive
    obj.pt_time_rush_min = pt
    for key, value in over.items():
        setattr(obj, key, value)
    return obj


# --- hard filter --------------------------------------------------------------
def test_passes_default():
    assert passes_hard_filter(_orm()).passed


def test_knockouts():
    # rooms = bedrooms+1 when rooms_total missing, so 1 bed -> 2 pièces < 3 -> fail
    assert not passes_hard_filter(_orm(bedrooms=1)).passed
    assert not passes_hard_filter(_orm(bedrooms=8)).passed   # 9 pièces > 8
    assert not passes_hard_filter(_orm(surface=70)).passed   # < 80 m²
    res = passes_hard_filter(_orm(commune="Esch-sur-Alzette"))
    assert not res.passed and any("not in target" in r for r in res.reasons)


def test_rooms_uses_total_then_estimates_from_bedrooms():
    assert passes_hard_filter(_orm(bedrooms=1, rooms_total=3)).passed       # 3 pièces ok
    assert not passes_hard_filter(_orm(bedrooms=1, rooms_total=2)).passed   # 2 pièces too few
    # no rooms_total -> estimate bedrooms+1: 2 bed -> 3 pièces passes (the key fix)
    assert passes_hard_filter(_orm(bedrooms=2)).passed
    assert passes_hard_filter(_orm(bedrooms=4)).passed


def test_commute_is_only_an_indicator():
    # commute is no longer a knockout — a long/absent commute still passes the filter
    assert passes_hard_filter(_orm(drive=120, pt=200)).passed
    assert passes_hard_filter(_orm(drive=None, pt=None)).passed


def test_price_cap_buy():
    assert passes_hard_filter(_orm(listing_type="buy", price=2_900_000)).passed
    assert not passes_hard_filter(_orm(listing_type="buy", price=3_100_000)).passed  # > €3M


def test_price_cap_rent():
    assert passes_hard_filter(_orm(listing_type="rent", rent=5_500)).passed
    assert not passes_hard_filter(_orm(listing_type="rent", rent=6_500)).passed  # > €6000/mo


def test_furnished_is_uncapped():
    # furnished has no price ceiling
    assert passes_hard_filter(_orm(listing_type="furnished", rent=20_000)).passed


def test_unknown_price_passes():
    # a sale with price on request (None) is not rejected by the cap
    l = _orm(listing_type="buy")
    l.price_eur = None
    assert passes_hard_filter(l).passed


# --- soft score ---------------------------------------------------------------
def test_score_range_and_sum():
    bd = score_listing(_orm())
    assert 0.0 <= bd.total <= 100.0
    assert round(sum(p["points"] for p in bd.parts.values()), 1) == bd.total
    assert set(bd.parts) == set(SCORING_WEIGHTS)


def test_strong_beats_weak():
    strong = score_listing(
        _orm(commune="Luxembourg", drive=10, pt=20, energy_class="A",
             has_garage=True, has_garden=True, llm_quality_score=90)
    )
    weak = score_listing(_orm(commune="Leudelange", drive=30, pt=45))
    assert strong.total > weak.total and strong.total > 70


def test_drive_monotonic_in_score():
    near = score_listing(_orm(drive=8)).parts["drive_time"]["score"]
    far = score_listing(_orm(drive=40)).parts["drive_time"]["score"]
    assert near > far


def test_energy_ordering():
    a = score_listing(_orm(energy_class="A")).parts["energy_class"]["score"]
    c = score_listing(_orm(energy_class="C")).parts["energy_class"]["score"]
    i = score_listing(_orm(energy_class="I")).parts["energy_class"]["score"]
    assert a > c > i


def test_neutral_for_missing_llm():
    part = score_listing(_orm(llm_quality_score=None)).parts["llm_quality_score"]
    assert part["neutral"] and part["score"] == NEUTRAL


# --- apply + ranking ----------------------------------------------------------
def test_apply_scores_and_top(lux_session):
    passer = _orm(commune="Luxembourg", drive=10, pt=20, energy_class="A", has_garage=True)
    weak = _orm(commune="Leudelange", drive=30, pt=45)
    failer = _orm(bedrooms=1)  # 2 pièces < 3
    lux_session.add_all([passer, weak, failer])
    lux_session.commit()

    dup = _orm(commune="Luxembourg")
    dup.duplicate_of_id = passer.id  # secondary duplicate -> skipped
    lux_session.add(dup)
    lux_session.commit()

    counts = apply_scores(lux_session)
    assert counts == {"scored": 2, "filtered_out": 1}

    assert failer.score_total is None
    assert passer.score_total is not None and weak.score_total is not None
    assert dup.score_total is None

    top = top_listings(lux_session, limit=10)
    assert top[0].id == passer.id
    assert dup not in top and failer not in top


def test_furnished_is_scored(lux_session):
    furnished = _orm(listing_type="furnished", commune="Strassen", rent=2500)
    lux_session.add(furnished)
    lux_session.commit()
    apply_scores(lux_session)
    assert furnished.score_total is not None  # furnished passes the same filter


def test_prune_nonmatching(lux_session):
    keep = _orm(commune="Luxembourg", bedrooms=4)
    over_cap = _orm(listing_type="buy", commune="Luxembourg", price=5_000_000)  # > €3M now
    off_target = _orm(commune="Esch-sur-Alzette")
    lux_session.add_all([keep, over_cap, off_target])
    lux_session.commit()

    pruned = prune_nonmatching(lux_session)
    assert pruned == 2  # over_cap + off_target deactivated

    assert keep.is_active is True
    assert over_cap.is_active is False and off_target.is_active is False
    # idempotent: a second prune deactivates nothing more
    assert prune_nonmatching(lux_session) == 0
