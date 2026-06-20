"""Tests for the mortgage estimate (buy-vs-rent comparison)."""

from __future__ import annotations

from src.lux_monitor.finance import monthly_mortgage


def test_known_annuity_value():
    # €500k at 3.5% over 30y -> ~€2,245/mo (standard annuity).
    m = monthly_mortgage(500_000, annual_rate_pct=3.5, term_years=30, financing_pct=100)
    assert abs(m - 2245.22) < 1.0


def test_zero_rate_is_principal_over_term():
    # 0% -> price / months, exactly.
    m = monthly_mortgage(360_000, annual_rate_pct=0, term_years=30)
    assert m == round(360_000 / 360, 2)  # 1000.0


def test_financing_pct_scales_loan():
    full = monthly_mortgage(400_000, annual_rate_pct=3.0, term_years=25, financing_pct=100)
    half = monthly_mortgage(400_000, annual_rate_pct=3.0, term_years=25, financing_pct=50)
    assert abs(half - full / 2) < 0.01


def test_higher_rate_costs_more():
    lo = monthly_mortgage(500_000, annual_rate_pct=2.0, term_years=30)
    hi = monthly_mortgage(500_000, annual_rate_pct=5.0, term_years=30)
    assert hi > lo


def test_none_or_zero_price():
    assert monthly_mortgage(None) is None
    assert monthly_mortgage(0) is None
    assert monthly_mortgage(-100) is None


def test_uses_config_defaults():
    # Calling with no overrides uses config MORTGAGE; just assert it returns a
    # sensible positive number for a typical price.
    m = monthly_mortgage(1_000_000)
    assert m is not None and m > 0
