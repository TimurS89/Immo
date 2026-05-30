"""Tests for the Luxembourg hard-config (config/luxembourg.py) + registry wiring."""

from __future__ import annotations

from config.luxembourg import (
    DWS_OFFICE,
    HARD_FILTERS,
    HARD_FILTERS_BUY,
    HARD_FILTERS_RENT,
    LU_LOCALE,
    PRIMARY_COMMUNES,
    SCORING_WEIGHTS,
    TARGET_COMMUNES,
    lu_country_config,
)
from src.config import load_config


# --- target communes ----------------------------------------------------------

def test_target_communes_complete():
    assert len(TARGET_COMMUNES) == 7
    assert "Luxembourg" in TARGET_COMMUNES and "Leudelange" in TARGET_COMMUNES
    assert not ({"Sandweiler", "Howald"} & set(TARGET_COMMUNES))  # dropped
    required = {"foreign_pct", "has_train", "primary", "lat", "lng", "appeal"}
    for name, meta in TARGET_COMMUNES.items():
        assert required <= set(meta), f"{name} missing keys"
        assert 0 <= meta["foreign_pct"] <= 100
        assert 49.0 < meta["lat"] < 50.0   # plausible LU latitude
        assert 5.7 < meta["lng"] < 6.6      # plausible LU longitude


def test_primary_communes():
    assert set(PRIMARY_COMMUNES) == {"Luxembourg", "Strassen", "Bertrange", "Mamer", "Walferdange"}


# --- hard filters (unified: rooms / surface / commune; no price or commute) ---

def test_hard_filters():
    assert HARD_FILTERS["min_rooms"] == 3
    assert HARD_FILTERS["max_rooms"] == 8
    assert HARD_FILTERS["min_surface_m2"] == 80
    assert HARD_FILTERS["communes"] == list(TARGET_COMMUNES)
    # furnished / rent / buy share the same criteria now
    assert HARD_FILTERS_RENT is HARD_FILTERS and HARD_FILTERS_BUY is HARD_FILTERS
    # price and commute are no longer knockouts
    assert "min_rent_total_eur" not in HARD_FILTERS
    assert "max_drive_time_rush_min" not in HARD_FILTERS


# --- scoring weights ----------------------------------------------------------

def test_scoring_weights():
    expected_keys = {
        "drive_time", "pt_time", "foreign_pct", "energy_class",
        "has_garage", "has_garden", "llm_quality_score",
    }
    assert set(SCORING_WEIGHTS) == expected_keys
    assert sum(SCORING_WEIGHTS.values()) == 100
    assert all(w > 0 for w in SCORING_WEIGHTS.values())


# --- DWS office ---------------------------------------------------------------

def test_dws_office():
    assert DWS_OFFICE.address == "2 Boulevard Konrad Adenauer, L-1115 Luxembourg"
    assert DWS_OFFICE.coords == (49.6315, 6.1717)


# --- country config + registry ------------------------------------------------

def test_lu_country_config():
    cc = lu_country_config()
    assert cc.enabled is True
    assert cc.currency == "EUR"
    assert cc.locale == LU_LOCALE == "fr-LU"
    assert cc.timezone == "Europe/Luxembourg"
    assert cc.enabled_portals() == ["athome", "immotop", "wortimmo"]
    assert len(cc.areas) == len(TARGET_COMMUNES)
    assert {a.name for a in cc.areas} == set(TARGET_COMMUNES)


def test_load_config_registers_luxembourg():
    cfg = load_config()
    assert "LU" in cfg.search_areas
    lu = cfg.search_areas["LU"]
    assert lu.enabled is True
    assert lu.enabled_portals() == ["athome", "immotop", "wortimmo"]
    # DE/FR remain present but disabled (cross-border option).
    assert cfg.search_areas["DE"].enabled is False
    assert cfg.search_areas["FR"].enabled is False
    assert "LU" in cfg.enabled_countries()
