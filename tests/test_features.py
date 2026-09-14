"""Tests for text-derived property features (land size, AC, solar, type)."""

from __future__ import annotations

import pytest

from src.lux_monitor.features import (
    classify_property,
    detect_air_conditioning,
    detect_heat_pump,
    detect_solar_panels,
    extract_land_m2,
)


# --- air conditioning ---------------------------------------------------------
@pytest.mark.parametrize("text", [
    "Appartement avec climatisation réversible",
    "Séjour climatisé, cuisine équipée",
    "Wohnung mit Klimaanlage",
    "Spacious flat with air conditioning",
    "air-conditioned throughout",
])
def test_ac_detected(text):
    assert detect_air_conditioning(text) is True


def test_ac_negated_and_unknown():
    assert detect_air_conditioning("Appartement sans climatisation") is False
    assert detect_air_conditioning("Wohnung ohne Klimaanlage") is False
    # not mentioned -> unknown, NOT False
    assert detect_air_conditioning("Belle maison avec jardin") is None
    assert detect_air_conditioning(None) is None


# --- solar --------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "Maison avec panneaux solaires",
    "panneaux photovoltaïques récents",
    "Haus mit Solaranlage",
    "Photovoltaik auf dem Dach",
    "fitted with solar panels",
])
def test_solar_detected(text):
    assert detect_solar_panels(text) is True


def test_solar_negated_and_unknown():
    assert detect_solar_panels("sans panneaux solaires") is False
    assert detect_solar_panels("Appartement lumineux au centre") is None


# --- heat pump ----------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "Chauffage par pompe à chaleur",
    "Maison avec PAC air-eau",
    "Neubau mit Wärmepumpe",
    "geothermal heat pump installed 2022",
])
def test_heat_pump_detected(text):
    assert detect_heat_pump(text) is True


def test_heat_pump_negated_and_unknown():
    assert detect_heat_pump("sans pompe à chaleur") is False
    assert detect_heat_pump("Chauffage au mazout") is None


# --- land / plot size ---------------------------------------------------------
def test_land_ares_converted_to_m2():
    # Luxembourg quotes plots in ares; 1 are = 100 m²
    assert extract_land_m2("Maison sur un terrain de 5 ares") == 500.0
    assert extract_land_m2("terrain de 5,5 ares") == 550.0
    assert extract_land_m2("6 ares de terrain plat") == 600.0
    assert extract_land_m2("Grundstück von 8 Ar") == 800.0


def test_land_m2_direct():
    assert extract_land_m2("terrain de 500 m²") == 500.0
    assert extract_land_m2("plot of 800 sqm") == 800.0
    assert extract_land_m2("terrain 1.500 m2") == 1500.0  # EU thousands


def test_land_absent_or_implausible():
    assert extract_land_m2("Bel appartement au 2e étage") is None
    assert extract_land_m2(None) is None
    # implausible values are rejected rather than returned as nonsense
    assert extract_land_m2("terrain de 2 m2") is None          # too small
    assert extract_land_m2("terrain de 5000 ares") is None     # 500k m² — too big


def test_land_does_not_grab_unrelated_numbers():
    # surface/price numbers must not be mistaken for a plot
    assert extract_land_m2("Appartement 120 m², 3 chambres, 450.000 €") is None


# --- property classification --------------------------------------------------
@pytest.mark.parametrize("label,exp_type,exp_sub", [
    ("Detached house", "house", "detached_house"),
    ("Semi-detached", "house", "semi_detached"),
    ("Terraced", "house", "terraced_house"),
    ("Villa", "house", "villa"),
    ("Apartment", "apartment", "apartment"),
    ("Studio", "apartment", "studio"),
    ("Duplex", "apartment", "duplex"),
    ("Penthouse", "apartment", "penthouse"),
])
def test_classify_known_labels(label, exp_type, exp_sub):
    assert classify_property(label) == (exp_type, exp_sub)


def test_classify_falls_back_to_portal_group():
    # unknown label -> slugified through, coarse type from the portal's group
    assert classify_property("Maison de maître", "house") == ("house", "maison_de_maitre")
    assert classify_property("Kangourou", "flat") == ("apartment", "kangourou")


def test_classify_unknown_everything():
    assert classify_property("Indoor garage") == ("other", "indoor_garage")
    assert classify_property(None) == (None, None)
