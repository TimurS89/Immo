"""Unit tests for the pure LU parsing helpers."""

from __future__ import annotations

import pytest

from src.scrapers.luxembourg.parsing import (
    bedrooms_from_chambres_pieces,
    detect_features,
    detect_lang,
    parse_decimal,
    parse_energy_class,
    parse_floor,
    parse_postcode,
    parse_surface_m2,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2.500 €", 2500.0),       # EU thousands (dot)
        ("2 500", 2500.0),         # space thousands
        ("€ 4,200/month", 4200.0),  # EN thousands (comma)
        ("2.800 €/mois", 2800.0),   # EU
        ("85,5 m²", 85.5),          # EU decimal (comma)
        ("160 m²", 160.0),
        ("1.234,56", 1234.56),      # EU decimal
        ("1,234.56", 1234.56),      # EN decimal
        ("850.000 €", 850000.0),
        ("1,250,000", 1250000.0),   # EN thousands, two groups
        ("n/a", None),
        (None, None),
    ],
)
def test_parse_decimal(text, expected):
    assert parse_decimal(text) == expected


def test_parse_surface():
    assert parse_surface_m2("surface habitable 130 m²") == 130.0


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1er étage", 1),
        ("rez-de-chaussée", 0),
        ("RDC", 0),
        ("Ground floor", 0),
        ("2e étage", 2),
        ("sous-sol", -1),
        (None, None),
        ("", None),
    ],
)
def test_parse_floor(text, expected):
    assert parse_floor(text) == expected


@pytest.mark.parametrize(
    "chambres,pieces,expected",
    [
        (4, 5, 4),       # chambres explicit wins
        (None, 5, 4),    # infer pièces - 1
        (None, 1, 0),    # never negative
        (None, None, None),
        (3, None, 3),
    ],
)
def test_bedrooms_inference(chambres, pieces, expected):
    assert bedrooms_from_chambres_pieces(chambres, pieces) == expected


def test_detect_features():
    f = detect_features("Belle maison avec jardin et garage")
    assert f["has_garden"] and f["has_garage"]
    assert f["has_balcony_terrace"] is False
    assert f["has_elevator"] is None  # unknown when not mentioned

    f2 = detect_features("Appartement avec terrasse et ascenseur")
    assert f2["has_balcony_terrace"] and f2["has_elevator"] is True
    assert f2["has_garden"] is False


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Walferdange (L-7220)", "7220"),
        ("Mamer L-8245", "8245"),
        ("1115 Luxembourg", "1115"),
        ("Strassen, Luxembourg", None),
        (None, None),
    ],
)
def test_parse_postcode(text, expected):
    assert parse_postcode(text) == expected


def test_parse_energy_class():
    assert parse_energy_class("Classe énergétique B") == "B"
    assert parse_energy_class("passeport A") == "A"
    assert parse_energy_class("Z") is None
    assert parse_energy_class(None) is None


def test_detect_lang():
    assert detect_lang("Spacious house with 4 bedroom and kitchen") == "en"
    assert detect_lang("Belle maison avec 4 chambres et cuisine") == "fr"
    assert detect_lang("Schöne Wohnung mit Küche und Zimmer") == "de"
