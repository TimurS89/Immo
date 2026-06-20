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
