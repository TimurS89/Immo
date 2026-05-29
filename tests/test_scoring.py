"""Tests for the Phase 5 hard filter + soft scoring."""

from __future__ import annotations

import itertools

from config.luxembourg import SCORING_WEIGHTS
from src.lux_monitor.models import Listing
from src.lux_monitor.schemas import ListingCreate
from src.lux_monitor.scoring import (
    NEUTRAL,
    apply_scores,
    passes_hard_filter,
    score_listing,
    top_listings,
)

_ids = itertools.count(1)


def _orm(
    commune: str = "Luxembourg",
    listing_type: str = "rent",
    *,
    bedrooms: int = 4,
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
        surface_m2=surface,
        rent_eur=rent if listing_type == "rent" else None,
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
def test_rent_passes():
    assert passes_hard_filter(_orm()).passed


def test_rent_knockouts():
    assert not passes_hard_filter(_orm(bedrooms=3)).passed
    assert not passes_hard_filter(_orm(surface=80)).passed
    assert not passes_hard_filter(_orm(rent=2000)).passed  # total < 2500
    assert not passes_hard_filter(_orm(rent=5000)).passed  # total > 4500
    res = passes_hard_filter(_orm(commune="Esch-sur-Alzette"))
    assert not res.passed and any("not in target" in r for r in res.reasons)


def test_commute_either_mode():
    assert passes_hard_filter(_orm(drive=35, pt=45)).passed  # PT within cap
    assert passes_hard_filter(_orm(drive=25, pt=90)).passed  # drive within cap
    assert not passes_hard_filter(_orm(drive=35, pt=90)).passed  # neither
    res = passes_hard_filter(_orm(drive=None, pt=None))
    assert not res.passed and any("not computed" in r for r in res.reasons)


def test_buy_filter():
    assert passes_hard_filter(_orm(listing_type="buy", price=1_000_000)).passed
    assert not passes_hard_filter(_orm(listing_type="buy", price=500_000)).passed
    assert not passes_hard_filter(_orm(listing_type="buy", price=2_000_000)).passed


# --- soft score ---------------------------------------------------------------
def test_score_range_and_sum():
    bd = score_listing(_orm())
    assert 0.0 <= bd.total <= 100.0
    assert round(sum(p["points"] for p in bd.parts.values()), 1) == bd.total
    assert set(bd.parts) == set(SCORING_WEIGHTS)


def test_strong_beats_weak():
    strong = score_listing(
        _orm(
            commune="Luxembourg", drive=12, pt=26, energy_class="A",
            has_garage=True, has_garden=True, floor=0,
            walk_to_school_min=5, llm_quality_score=90,
        )
    )
    weak = score_listing(_orm(commune="Bertrange", drive=23, pt=40))
    assert strong.total > weak.total
    assert strong.total > 75


def test_drive_monotonic_in_score():
    near = score_listing(_orm(drive=8)).parts["drive_time"]["score"]
    far = score_listing(_orm(drive=28)).parts["drive_time"]["score"]
    assert near > far


def test_energy_ordering():
    a = score_listing(_orm(energy_class="A")).parts["energy_class"]["score"]
    c = score_listing(_orm(energy_class="C")).parts["energy_class"]["score"]
    i = score_listing(_orm(energy_class="I")).parts["energy_class"]["score"]
    assert a > c > i


def test_ground_floor_with_garden():
    yes = score_listing(_orm(floor=0, has_garden=True)).parts["ground_floor_with_garden"]["score"]
    no = score_listing(_orm(floor=2, has_garden=True)).parts["ground_floor_with_garden"]["score"]
    assert yes == 1.0 and no == 0.0


def test_neutral_for_missing_llm():
    part = score_listing(_orm(llm_quality_score=None)).parts["llm_quality_score"]
    assert part["neutral"] and part["score"] == NEUTRAL


# --- apply + ranking ----------------------------------------------------------
def test_apply_scores_and_top(lux_session):
    passer = _orm(commune="Luxembourg", drive=12, pt=26, energy_class="A", has_garage=True)
    weak = _orm(commune="Bertrange", drive=23, pt=40)
    failer = _orm(bedrooms=2)
    lux_session.add_all([passer, weak, failer])
    lux_session.commit()

    dup = _orm(commune="Luxembourg", drive=12, pt=26)
    dup.duplicate_of_id = passer.id  # secondary duplicate -> skipped
    lux_session.add(dup)
    lux_session.commit()

    counts = apply_scores(lux_session)
    assert counts == {"scored": 2, "filtered_out": 1}

    assert failer.score_total is None
    assert passer.score_total is not None and weak.score_total is not None
    assert dup.score_total is None  # never scored

    top = top_listings(lux_session, limit=10)
    assert top[0].id == passer.id  # strongest first
    assert dup not in top and failer not in top
