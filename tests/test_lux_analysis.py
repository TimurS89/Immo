"""Tests for the Phase 6 offline (heuristic) description analysis.

(Named test_lux_analysis to avoid colliding with the legacy tests/test_analysis.py
which covers the old src/analysis package.)
"""

from __future__ import annotations

import itertools

from src.lux_monitor.analysis import (
    BASE_SCORE,
    analyze_listing,
    apply_analysis,
)
from src.lux_monitor.models import Listing
from src.lux_monitor.schemas import ListingCreate

_ids = itertools.count(1)

NEUTRAL_DESC = "Bel appartement au centre ville avec vue agreable et beaucoup de rangements."


def _orm(desc: str = NEUTRAL_DESC, title: str = "Appartement", **over) -> Listing:
    obj = ListingCreate(
        portal="athome",
        portal_listing_id=f"a{next(_ids)}",
        url="https://athome.lu/x",
        commune="Luxembourg",
        listing_type="rent",
        bedrooms=4,
        surface_m2=120.0,
        rent_eur=3000.0,
        description_raw=desc,
        description_lang="fr",
        title=title,
    ).to_orm()
    for key, value in over.items():
        setattr(obj, key, value)
    return obj


def test_neutral_baseline():
    a = analyze_listing(_orm())
    assert a.quality_score == BASE_SCORE
    assert a.red_flags == [] and a.highlights == []


def test_renovation_flag_lowers_score():
    a = analyze_listing(_orm(desc="Appartement a renover entierement, travaux a prevoir."))
    assert "needs renovation" in a.red_flags
    assert a.quality_score < BASE_SCORE
    # 'a renover' must NOT also be read as 'renovated'
    assert "renovated / new" not in a.highlights


def test_renovated_is_a_highlight():
    a = analyze_listing(_orm(desc="Appartement entierement renove, lumineux et calme."))
    assert "renovated / new" in a.highlights
    assert "bright / sunny" in a.highlights
    assert "quiet / residential" in a.highlights
    assert a.quality_score > BASE_SCORE


def test_multilingual_and_accents():
    assert "needs renovation" in analyze_listing(_orm(desc="renovierungsbedürftig, Sanierung nötig")).red_flags
    assert "needs renovation" in analyze_listing(_orm(desc="charming flat, needs renovation")).red_flags
    assert "renovated / new" in analyze_listing(_orm(desc="entièrement rénové")).highlights
    assert "renovated / new" in analyze_listing(_orm(desc="entierement renove")).highlights


def test_viager_heavy_penalty():
    a = analyze_listing(_orm(desc="Vente en viager occupe."))
    assert "viager (life annuity)" in a.red_flags
    assert a.quality_score <= BASE_SCORE - 40 or a.quality_score == 0


def test_score_is_clipped():
    great = analyze_listing(
        _orm(
            desc="Neuf, lumineux, calme, proche transports, spacieux.",
            construction_year=2025,
            renovation_year=2025,
        ),
        now_year=2026,
    )
    assert great.quality_score == 100  # would exceed 100 before clipping

    awful = analyze_listing(_orm(desc="A renover, viager, nuisances sonores, meuble."))
    assert awful.quality_score == 0  # would go negative before clipping


def test_structured_signals():
    a = analyze_listing(_orm(has_elevator=False, floor=4), now_year=2026)
    assert any("no elevator" in f for f in a.red_flags)

    b = analyze_listing(_orm(construction_year=2024), now_year=2026)
    assert any("recent construction" in h for h in b.highlights)
    assert b.quality_score > BASE_SCORE

    c = analyze_listing(_orm(rent_eur=3000.0, charges_eur=1000.0))  # 33% of rent
    assert any("high charges" in f for f in c.red_flags)

    d = analyze_listing(_orm(deposit_months=3))
    assert any("high deposit" in f for f in d.red_flags)


def test_summary_mentions_commune_and_beds():
    a = analyze_listing(_orm(desc="lumineux et calme"))
    assert "Luxembourg" in a.summary and "4-bed" in a.summary
    assert "Pluses:" in a.summary


def test_apply_analysis(lux_session):
    good = _orm(desc="entierement renove, lumineux")
    plain = _orm(desc=NEUTRAL_DESC)
    lux_session.add_all([good, plain])
    lux_session.commit()

    dup = _orm(desc="a renover")
    dup.duplicate_of_id = good.id  # secondary -> skipped
    lux_session.add(dup)
    lux_session.commit()

    n = apply_analysis(lux_session)
    assert n == 2  # dup skipped

    assert good.llm_quality_score is not None
    assert "renovated / new" in good.llm_highlights
    assert good.llm_summary
    assert dup.llm_quality_score is None  # never analyzed

    assert apply_analysis(lux_session) == 0  # only_missing -> nothing left
