"""Recent-activity helpers: brand-new listings and price drops.

Reusable by the dashboard (and later by notifications). All queries are scoped to
active, non-duplicate listings, and default to ones that passed the hard filter
(``score_total`` set).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session, selectinload

from src.lux_monitor.models import Listing
from src.lux_monitor.timeutil import as_naive_utc, naive_utc_cutoff, naive_utc_now

# Back-compat local aliases (these names are used throughout this module/tests).
_as_naive_utc = as_naive_utc
_utc_cutoff = naive_utc_cutoff


def new_listings(session: Session, *, days: int = 7, scored_only: bool = True) -> list[Listing]:
    """Active, non-duplicate listings first seen within the last ``days``."""
    query = session.query(Listing).filter(
        Listing.is_active.is_(True),
        Listing.duplicate_of_id.is_(None),
        Listing.first_seen_at >= _utc_cutoff(days),
    )
    if scored_only:
        query = query.filter(Listing.score_total.isnot(None))
    return query.order_by(Listing.score_total.desc()).all()


def days_on_market(listing: Listing, *, now: datetime | None = None) -> int | None:
    """How long a listing has been (or was) online, in whole days.

    For an active listing: now - first_seen. For a delisted one: last_seen -
    first_seen (its lifetime). A long time on market is a buyer's signal — the
    price is often negotiable. Returns None if first_seen is missing.
    """
    if not listing.first_seen_at:
        return None
    start = _as_naive_utc(listing.first_seen_at)
    # Delisted only when explicitly inactive AND we have a last_seen to bound the
    # lifetime; otherwise measure to now (is_active defaults to None pre-flush).
    if listing.is_active is False and listing.last_seen_at is not None:
        end = _as_naive_utc(listing.last_seen_at)
    else:
        end = _as_naive_utc(now) if now else naive_utc_now()
    return max(0, (end - start).days)


def long_on_market(
    session: Session, *, min_days: int = 60, scored_only: bool = True, now: datetime | None = None
) -> list[tuple[Listing, int]]:
    """Active, non-duplicate listings online for at least ``min_days``.

    Sorted longest-first. Returns (listing, days) pairs — these are the
    slow-movers worth a lower offer.
    """
    query = session.query(Listing).filter(
        Listing.is_active.is_(True), Listing.duplicate_of_id.is_(None)
    )
    if scored_only:
        query = query.filter(Listing.score_total.isnot(None))

    out: list[tuple[Listing, int]] = []
    for listing in query.all():
        dom = days_on_market(listing, now=now)
        if dom is not None and dom >= min_days:
            out.append((listing, dom))
    out.sort(key=lambda pair: pair[1], reverse=True)
    return out


@dataclass
class PriceDrop:
    listing: Listing
    old_price: float
    new_price: float

    @property
    def delta(self) -> float:
        return self.new_price - self.old_price

    @property
    def pct(self) -> float:
        return round(100.0 * self.delta / self.old_price, 1) if self.old_price else 0.0


def price_drops(session: Session, *, days: int = 30, scored_only: bool = True) -> list[PriceDrop]:
    """Listings whose most recent recorded price is a *decrease*, within ``days``.

    Compares the last two price-history points; biggest percentage drop first.
    """
    cutoff = _utc_cutoff(days)
    query = (
        session.query(Listing)
        .options(selectinload(Listing.price_history_entries))  # avoid N+1
        .filter(Listing.is_active.is_(True), Listing.duplicate_of_id.is_(None))
    )
    if scored_only:
        query = query.filter(Listing.score_total.isnot(None))

    drops: list[PriceDrop] = []
    for listing in query.all():
        points = [
            (entry.recorded_at, entry.price_eur)
            for entry in sorted(listing.price_history_entries, key=lambda e: e.recorded_at)
            if entry.price_eur is not None
        ]
        if len(points) < 2:
            continue
        (_, previous), (last_when, latest) = points[-2], points[-1]
        if latest < previous and _as_naive_utc(last_when) >= cutoff:
            drops.append(PriceDrop(listing, previous, latest))

    drops.sort(key=lambda d: d.pct)  # most negative (biggest drop) first
    return drops


@dataclass
class BuyVsRent:
    commune: str
    n_rent: int
    n_buy: int
    median_rent: float | None       # monthly, long-term rent only
    median_mortgage: float | None   # monthly, estimated at the given rate
    break_even_years: float | None

    @property
    def delta(self) -> float | None:
        """Monthly buy − rent (positive = buying costs more per month)."""
        if self.median_rent is None or self.median_mortgage is None:
            return None
        return round(self.median_mortgage - self.median_rent)


def buy_vs_rent_by_commune(
    session: Session,
    *,
    annual_rate_pct: float | None = None,
    term_years: int | None = None,
    financing_pct: float | None = None,
) -> list[BuyVsRent]:
    """Per-commune median long-term rent vs median estimated mortgage + break-even.

    Pure aggregation over active, non-duplicate, scored listings, so the dashboard
    (and any notifier) renders identical numbers. Furnished is intentionally
    excluded from the rent median — it's a pricier, uncapped product that would
    bias the comparison.
    """
    import statistics

    from src.lux_monitor.finance import break_even_years, monthly_mortgage

    rows = (
        session.query(Listing)
        .filter(
            Listing.is_active.is_(True),
            Listing.duplicate_of_id.is_(None),
            Listing.score_total.isnot(None),
        )
        .all()
    )
    by_commune: dict[str, dict[str, list]] = {}
    for l in rows:
        bucket = by_commune.setdefault(l.commune, {"rent": [], "buy_mort": [], "buy_price": []})
        if l.listing_type == "rent" and l.compare_price is not None:
            bucket["rent"].append(l.compare_price)
        elif l.listing_type == "buy" and l.price_eur is not None:
            m = monthly_mortgage(
                l.price_eur, annual_rate_pct=annual_rate_pct,
                term_years=term_years, financing_pct=financing_pct,
            )
            if m is not None:
                bucket["buy_mort"].append(m)
                bucket["buy_price"].append(l.price_eur)

    out: list[BuyVsRent] = []
    for commune in sorted(by_commune):
        b = by_commune[commune]
        med_rent = statistics.median(b["rent"]) if b["rent"] else None
        med_mort = statistics.median(b["buy_mort"]) if b["buy_mort"] else None
        med_price = statistics.median(b["buy_price"]) if b["buy_price"] else None
        out.append(BuyVsRent(
            commune=commune,
            n_rent=len(b["rent"]),
            n_buy=len(b["buy_mort"]),
            median_rent=None if med_rent is None else round(med_rent),
            median_mortgage=None if med_mort is None else round(med_mort),
            break_even_years=break_even_years(med_price, med_mort, med_rent),
        ))
    return out
