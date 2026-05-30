"""Tests for cross-portal de-duplication of LU listings."""

from __future__ import annotations

from src.lux_monitor.dedup import is_duplicate_pair, mark_duplicates
from src.lux_monitor.models import Listing
from src.lux_monitor.schemas import ListingCreate


def _listing(portal, commune, *, bedrooms=4, surface=140.0, rent=3100.0,
             listing_type="rent", charges=None) -> Listing:
    return ListingCreate(
        portal=portal,
        portal_listing_id=f"{portal}-{commune}-{int(rent)}-{int(surface)}",
        url=f"https://{portal}.lu/x",
        commune=commune,
        listing_type=listing_type,
        bedrooms=bedrooms,
        surface_m2=surface,
        rent_eur=rent,
        charges_eur=charges,
        description_raw="A reasonably long description used for completeness scoring.",
        description_lang="fr",
        title="Listing",
    ).to_orm()


def test_is_duplicate_pair_positive():
    a = _listing("athome", "Strassen", surface=140, rent=3100)
    b = _listing("immotop", "Strassen", surface=141, rent=3050)  # ±2 m², <5%
    assert is_duplicate_pair(a, b)


def test_is_duplicate_pair_negatives():
    base = _listing("athome", "Strassen", surface=140, rent=3100)
    assert not is_duplicate_pair(base, _listing("immotop", "Mamer"))            # commune
    assert not is_duplicate_pair(base, _listing("immotop", "Strassen", bedrooms=3))  # beds
    assert not is_duplicate_pair(base, _listing("immotop", "Strassen", surface=145))  # >2 m²
    assert not is_duplicate_pair(base, _listing("immotop", "Strassen", rent=3500))    # >5%


def test_mark_duplicates_links_secondary(lux_session):
    a = _listing("athome", "Strassen", surface=140, rent=3100)
    b = _listing("immotop", "Strassen", surface=141, rent=3050)  # dup of a
    c = _listing("wortimmo", "Mamer", surface=150, rent=3200)    # unique
    lux_session.add_all([a, b, c])
    lux_session.commit()

    marked = mark_duplicates(lux_session)
    assert marked == 1

    # Exactly one of the Strassen pair points at the other; Mamer untouched.
    a2 = lux_session.query(Listing).filter_by(portal="athome").one()
    b2 = lux_session.query(Listing).filter_by(portal="immotop").one()
    c2 = lux_session.query(Listing).filter_by(portal="wortimmo").one()
    links = [x.duplicate_of_id for x in (a2, b2) if x.duplicate_of_id is not None]
    assert len(links) == 1
    assert links[0] in {a2.id, b2.id}
    assert c2.duplicate_of_id is None


def test_mark_duplicates_ignores_same_portal(lux_session):
    # Two near-identical listings from the SAME portal are NOT merged.
    a = _listing("athome", "Bertrange", surface=120, rent=2800)
    b = _listing("athome", "Bertrange", surface=120, rent=2800)
    b.portal_listing_id = "athome-Bertrange-dupe2"
    lux_session.add_all([a, b])
    lux_session.commit()
    assert mark_duplicates(lux_session) == 0
