"""Parser tests for the LU scrapers against synthetic fixtures + save_listings."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import AppConfig
from src.lux_monitor.models import Listing, PriceHistoryEntry
from src.scrapers.luxembourg.athome import AtHomeScraper
from src.scrapers.luxembourg.immotop import ImmotopScraper
from src.scrapers.luxembourg.wortimmo import WortimmoScraper

FIXTURES = Path(__file__).parent / "fixtures" / "luxembourg"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _by_id(listings, ext_id):
    return next(x for x in listings if x.portal_listing_id == ext_id)


# --- atHome -------------------------------------------------------------------

def test_athome_parse():
    listings = AtHomeScraper.parse_serp(_load("athome_serp_rent.html"), "rent")
    # 4 cards, one invalid (no surface) -> 3 parsed.
    assert len(listings) == 3

    a = _by_id(listings, "6543210")
    assert a.portal == "athome" and a.commune == "Walferdange" and a.postcode == "7220"
    assert a.rent_eur == 2500 and a.charges_eur == 250
    assert a.bedrooms == 4 and a.rooms_total == 5
    assert a.surface_m2 == 130 and a.floor == 1
    assert a.energy_class == "B" and a.thermal_class == "C"
    assert a.has_garden and a.has_garage and a.description_lang == "fr"
    assert a.to_orm().rent_total_eur == 2750  # derived

    b = _by_id(listings, "6543211")  # pièces-only -> bedrooms inferred
    assert b.bedrooms == 4 and b.rooms_total == 5
    assert b.floor == 0 and b.charges_eur is None and b.has_balcony_terrace
    assert b.commune == "Strassen"


def test_athome_skips_invalid_card():
    listings = AtHomeScraper.parse_serp(_load("athome_serp_rent.html"), "rent")
    assert all(x.portal_listing_id != "6543212" for x in listings)  # no-surface card


# --- immotop (EN/FR, comma-thousands gotcha) ----------------------------------

def test_immotop_parse():
    listings = ImmotopScraper.parse_serp(_load("immotop_serp_rent.html"), "rent")
    assert len(listings) == 2

    en = _by_id(listings, "98765")
    assert en.rent_eur == 4200  # '€ 4,200/month' -> comma is THOUSANDS
    assert en.bedrooms == 4 and en.rooms_total == 5 and en.surface_m2 == 160
    assert en.floor == 0 and en.has_garden and en.has_garage
    assert en.description_lang == "en"

    fr = _by_id(listings, "98766")  # pièces only -> 4-1=3 bedrooms
    assert fr.bedrooms == 3 and fr.rooms_total == 4
    assert fr.rent_eur == 2800 and fr.charges_eur == 150
    assert fr.to_orm().rent_total_eur == 2950
    assert fr.floor == 1 and fr.has_balcony_terrace


# --- wortimmo -----------------------------------------------------------------

def test_wortimmo_parse():
    listings = WortimmoScraper.parse_serp(_load("wortimmo_serp_rent.html"), "rent")
    assert len(listings) == 2

    m = _by_id(listings, "W-44521")
    assert m.commune == "Mamer" and m.postcode == "8245"
    assert m.rent_eur == 3100 and m.charges_eur == 250 and m.bedrooms == 4
    assert m.surface_m2 == 145 and m.floor == 0 and m.energy_class == "A"
    assert m.has_garden and m.has_garage

    w = _by_id(listings, "W-44522")  # pièces only
    assert w.bedrooms == 4 and w.rooms_total == 5 and w.floor == 2


# --- persistence: upsert + price history + deactivate -------------------------

def test_save_listings_upsert_and_price_history(lux_session):
    scraper = AtHomeScraper(AppConfig())
    listings = AtHomeScraper.parse_serp(_load("athome_serp_rent.html"), "rent")
    assert len(listings) == 3

    res1 = scraper.save_listings(lux_session, listings)
    assert res1 == {"new": 3, "updated": 0, "deactivated": 0}
    assert lux_session.query(Listing).count() == 3

    # Second run: drop one listing, drop another, change rent on the first.
    changed = listings[0].model_copy(update={"rent_eur": 2400})  # 6543210
    res2 = scraper.save_listings(lux_session, [changed, listings[1]])
    assert res2["new"] == 0
    assert res2["updated"] == 2
    assert res2["deactivated"] == 1  # 6543213 no longer seen

    a = lux_session.query(Listing).filter_by(portal_listing_id="6543210").one()
    assert a.rent_eur == 2400 and a.rent_total_eur == 2650
    # initial 2750 + changed 2650 -> 2 price-history rows
    entries = lux_session.query(PriceHistoryEntry).filter_by(listing_id=a.id).all()
    assert len(entries) == 2
    assert {e.price_eur for e in entries} == {2750, 2650}

    d = lux_session.query(Listing).filter_by(portal_listing_id="6543213").one()
    assert d.is_active is False
