"""Tests for the mortgage estimate (buy-vs-rent comparison)."""

from __future__ import annotations

from src.lux_monitor.finance import break_even_years, monthly_mortgage


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


def test_break_even_basic():
    # price 800k, mortgage 3000/mo, rent 3500/mo -> saving 500/mo,
    # upfront 8% of 800k = 64k -> 64000/500/12 = ~10.7 yrs
    be = break_even_years(800_000, 3000, 3500)
    assert abs(be - 10.7) < 0.1


def test_break_even_none_when_buy_costs_more():
    # mortgage 4000 > rent 3500 -> no monthly saving -> None
    assert break_even_years(800_000, 4000, 3500) is None


def test_break_even_missing_inputs():
    assert break_even_years(None, 3000, 3500) is None
    assert break_even_years(800_000, None, 3500) is None
    assert break_even_years(800_000, 3000, None) is None
