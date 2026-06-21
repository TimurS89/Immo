"""Mortgage estimate for the buy-vs-rent comparison.

Turns a buy listing's sale price into an estimated **monthly mortgage payment**
(principal + interest) so it sits in the same column as a rent. This is the LOAN
payment only — it deliberately ignores notaire/registration fees, maintenance,
the impôt foncier, and insurance. With 100% financing (the default) there is no
down payment, so the whole price is borrowed.

Formula: standard fixed-rate annuity
    M = P * r / (1 - (1 + r)^-n)
where r is the monthly rate and n the number of monthly payments.
"""

from __future__ import annotations

from config.luxembourg import MORTGAGE


def monthly_mortgage(
    price: float | None,
    *,
    annual_rate_pct: float | None = None,
    term_years: int | None = None,
    financing_pct: float | None = None,
) -> float | None:
    """Estimated monthly mortgage payment for a purchase ``price``.

    Returns ``None`` when the price is unknown. Falls back to the ``MORTGAGE``
    config defaults for any unset parameter. A 0% rate degrades to a straight
    principal/term split (no division by zero).
    """
    if not price or price <= 0:
        return None

    rate_pct = MORTGAGE["annual_rate_pct"] if annual_rate_pct is None else annual_rate_pct
    years = MORTGAGE["term_years"] if term_years is None else term_years
    fin_pct = MORTGAGE["financing_pct"] if financing_pct is None else financing_pct

    loan = price * (fin_pct / 100.0)
    n = int(years * 12)
    if n <= 0:
        return None

    monthly_rate = (rate_pct / 100.0) / 12.0
    if monthly_rate == 0:
        return round(loan / n, 2)

    factor = (1 + monthly_rate) ** -n
    payment = loan * monthly_rate / (1 - factor)
    return round(payment, 2)


# Upfront cost of buying in Luxembourg, as a fraction of the purchase price:
# registration + transcription duties (~7%) plus notaire fees (~1%). This is the
# cash you don't recover, used to estimate a rent-vs-buy break-even horizon.
UPFRONT_BUY_COST_PCT = 8.0


def break_even_years(
    median_buy_price: float | None,
    median_buy_mortgage: float | None,
    median_rent: float | None,
    *,
    upfront_pct: float = UPFRONT_BUY_COST_PCT,
) -> float | None:
    """Rough years until buying beats renting, on monthly cash flow.

    Upfront buying cost (≈ ``upfront_pct`` of the price) divided by the monthly
    saving of a mortgage payment vs. an equivalent rent. Returns ``None`` if
    inputs are missing, and a sentinel large number is avoided — if the mortgage
    costs *more* per month than rent (no monthly saving) there is no break-even
    on cash flow alone, so we return ``None``.

    This is intentionally simple: it ignores equity build-up, price appreciation,
    maintenance and tax — a directional indicator, not financial advice.
    """
    if not median_buy_price or not median_buy_mortgage or not median_rent:
        return None
    monthly_saving = median_rent - median_buy_mortgage
    if monthly_saving <= 0:
        return None  # buying costs more per month → no cash-flow break-even
    upfront = median_buy_price * (upfront_pct / 100.0)
    return round(upfront / monthly_saving / 12.0, 1)
