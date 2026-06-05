"""Parser tests for the LU scrapers against synthetic fixtures + save_listings."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import AppConfig
from src.lux_monitor.models import Listing, PriceHistoryEntry
from src.lux_monitor.schemas import ListingCreate
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
    # rent search: 2 residential pass; office + rental-price-on-demand dropped.
    assert len(listings) == 2

    a = _by_id(listings, "1001")
    assert a.portal == "athome" and a.commune == "Strassen" and a.postcode == "8011"
    assert a.listing_type == "rent" and a.rent_eur == 3500
    assert a.bedrooms == 4 and a.surface_m2 == 180
    assert a.has_garden and a.has_garage and a.has_balcony_terrace
    assert a.lat == pytest.approx(49.6201) and a.lng == pytest.approx(6.0815)
    assert a.construction_year == 2015 and a.floor is None
    assert a.description_lang == "fr"
    assert a.url == "https://www.athome.lu/rent/house/strassen/id-1001.html"
    assert a.to_orm().rent_total_eur == 3500  # athome SERP has no charges
    assert a.photos_urls == []  # we intentionally don't store photos

    b = _by_id(listings, "1002")  # commune from address.district, not cityName
    assert b.commune == "Luxembourg" and b.postcode == "2551"
    assert b.listing_type == "furnished"  # "meublé" in description -> furnished
    assert b.bedrooms == 4 and b.surface_m2 == 120 and b.rent_eur == 2800
    assert b.floor == 2 and b.has_elevator is True
    assert b.has_garden is False and b.has_garage is False and b.parking_spaces == 1


def test_athome_furnished_detection_from_text():
    listings = AtHomeScraper.parse_serp(_load("athome_serp_rent.html"), "rent")
    # 1001 ("maison ... jardin et garage") -> long-term; 1002 ("meublé") -> furnished
    assert _by_id(listings, "1001").listing_type == "rent"
    assert _by_id(listings, "1002").listing_type == "furnished"


def test_athome_skips_nonresidential():
    ids = {x.portal_listing_id for x in AtHomeScraper.parse_serp(_load("athome_serp_rent.html"), "rent")}
    assert "1003" not in ids  # office (non-residential portal_group)
    assert "1004" not in ids  # rental price-on-demand dropped (it's a rent search)


def test_athome_sale_keeps_price_on_request():
    # Same entry 1004 (price 0 / on-demand) is KEPT when parsed as a buy search.
    buys = AtHomeScraper.parse_serp(_load("athome_serp_rent.html"), "buy")
    by_id = {x.portal_listing_id: x for x in buys}
    assert "1004" in by_id
    assert by_id["1004"].listing_type == "buy" and by_id["1004"].price_eur is None


def test_athome_parse_serp_page_counts():
    pr = AtHomeScraper.parse_serp_page(_load("athome_serp_rent.html"), "rent")
    assert pr.total == 1234 and pr.total_pages == 62  # from the fixture's paginator
    assert pr.rows_on_page == 4 and len(pr.listings) == 2


def test_athome_search_url_uses_hkey_and_server_side_filters():
    url = AtHomeScraper(AppConfig()).search_url("Strassen", "rent", 2)
    assert "q=e7677861" in url and "tr=rent" in url and "page=2" in url
    # server-side filters derived from HARD_FILTERS (min_rooms 3 -> 2 bedrooms, 80 m²)
    assert "bedrooms_min=2" in url and "srf_min=80" in url


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
    assert len(listings) == 2

    res1 = scraper.save_listings(lux_session, listings)
    assert res1 == {"new": 2, "updated": 0, "deactivated": 0}
    assert lux_session.query(Listing).count() == 2

    # Second run: change rent on 1001 and stop seeing 1002.
    changed = listings[0].model_copy(update={"rent_eur": 3300})  # id 1001
    res2 = scraper.save_listings(lux_session, [changed])
    assert res2["new"] == 0
    assert res2["updated"] == 1
    assert res2["deactivated"] == 1  # 1002 no longer seen

    a = lux_session.query(Listing).filter_by(portal_listing_id="1001").one()
    assert a.rent_eur == 3300 and a.rent_total_eur == 3300
    # initial 3500 + changed 3300 -> 2 price-history rows
    entries = lux_session.query(PriceHistoryEntry).filter_by(listing_id=a.id).all()
    assert len(entries) == 2
    assert {e.price_eur for e in entries} == {3500, 3300}

    d = lux_session.query(Listing).filter_by(portal_listing_id="1002").one()
    assert d.is_active is False


def test_run_luxembourg_survives_a_failing_scraper(lux_session, monkeypatch):
    """A portal raising (DNS/network/parse) must not abort the whole run."""
    import asyncio

    from src.config import load_config
    from src.scrapers.luxembourg import LU_SCRAPERS, run_luxembourg

    async def boom(self):
        raise RuntimeError("simulated network failure")

    for cls in LU_SCRAPERS.values():
        monkeypatch.setattr(cls, "scrape", boom)

    totals = asyncio.run(run_luxembourg(load_config(), lux_session))

    assert totals.get("scraper_errors", 0) >= 1  # failures caught, not raised
    assert "scored" in totals and "analyzed" in totals  # offline stages still ran


def test_run_luxembourg_persists_only_aligning(lux_session, monkeypatch):
    """Only listings passing the hard filter (commune/rooms/surface) are saved."""
    import asyncio

    from src.config import load_config
    from src.scrapers.luxembourg import LU_SCRAPERS, run_luxembourg

    def lc(commune, bedrooms, surface, ext):
        return ListingCreate(
            portal="athome", portal_listing_id=ext, url="https://athome.lu/x",
            commune=commune, listing_type="rent", bedrooms=bedrooms, surface_m2=surface,
            rent_eur=2500, description_raw="a long enough description here",
            description_lang="fr", title="t",
        )

    good = lc("Strassen", 4, 120, "g1")
    off_target = lc("Differdange", 4, 120, "o1")   # commune not in target set
    too_small = lc("Strassen", 4, 60, "s1")        # surface < 80 m²

    async def fake_athome(self):
        return [good, off_target, too_small]

    async def empty(self):
        return []

    monkeypatch.setattr(LU_SCRAPERS["athome"], "scrape", fake_athome)
    monkeypatch.setattr(LU_SCRAPERS["immotop"], "scrape", empty)
    monkeypatch.setattr(LU_SCRAPERS["wortimmo"], "scrape", empty)

    asyncio.run(run_luxembourg(load_config(), lux_session))

    saved = lux_session.query(Listing).all()
    assert {l.portal_listing_id for l in saved} == {"g1"}  # off-target + too-small dropped
