"""Phase 5 — hard filter + soft scoring for Luxembourg listings.

Two stages:

1. **Hard filter** (:func:`passes_hard_filter`) — non-negotiable knockouts from
   ``config.luxembourg.HARD_FILTERS_{RENT,BUY}``: commune membership, bedrooms,
   surface, price/rent band, and commute. Commute is acceptable when *either*
   mode is within its own cap (drive ≤ max OR PT ≤ max) — you'd take whichever is
   faster — rather than requiring both. Returns a :class:`FilterResult` with
   human-readable reasons for any knockout.

2. **Soft score** (:func:`score_listing`) — a 0–100 weighted blend of normalized
   subscores using ``config.luxembourg.SCORING_WEIGHTS`` (weights sum to 100, so
   the total is already on a 0–100 scale). Missing inputs score *neutral* (0.5)
   rather than best/worst, so absent data neither rewards nor unfairly punishes a
   listing. ``llm_quality_score`` is populated by the heuristic description
   analysis (``analysis.py``); it scores neutral when absent.

:func:`apply_scores` filters + scores every active, non-duplicate listing and
writes ``Listing.score_total`` (``None`` ⇔ filtered out). A small Rich CLI
(``python -m src.lux_monitor.scoring``) shows the ranked shortlist.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from config.luxembourg import (
    BED_BEST,
    HARD_FILTERS,
    MAX_PRICE_EUR,
    SCORING_WEIGHTS,
    TARGET_COMMUNES,
)
from src.lux_monitor.models import ENERGY_CLASSES, Listing

logger = logging.getLogger(__name__)

NEUTRAL = 0.5  # subscore for missing/unknown inputs

# Commute normalization (minutes): 0 -> 1.0, >= MAX -> 0.0. The commute is an
# indicator (not a hard filter), so these are just the good/bad anchors.
DRIVE_NORM_MAX = 45
PT_NORM_MAX = 75

# foreign_pct normalization band (%): <=LO -> 0.0, >=HI -> 1.0.
FOREIGN_LO, FOREIGN_HI = 30, 65


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


# --------------------------------------------------------------------------- #
# Stage 1: hard filter — commune, room count, surface (no price/commute caps)
# --------------------------------------------------------------------------- #
@dataclass
class FilterResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)


def _listing_price(listing: Listing) -> float | None:
    """The price to compare against the cap: sale price for buy, monthly total
    (rent + charges, falling back to rent) for rentals."""
    if listing.listing_type == "buy":
        return listing.price_eur
    if listing.rent_total_eur is not None:
        return listing.rent_total_eur
    return listing.rent_eur


def _effective_rooms(listing: Listing) -> int:
    """Total rooms (pièces). athome rarely reports rooms_total, so when it's
    missing estimate pièces as bedrooms + 1 (a living room): a "3-room" flat is
    2 bedrooms + living. This matches how LU listings advertise "X pièces" and
    keeps "min 3 rooms" from silently meaning "min 3 bedrooms"."""
    if listing.rooms_total:
        return listing.rooms_total
    if listing.bedrooms:
        return listing.bedrooms + 1
    return listing.bedrooms


def passes_hard_filter(listing: Listing, filters: dict | None = None) -> FilterResult:
    """Apply the (deliberately small) knockouts; collect reasons for any failure."""
    f = filters or HARD_FILTERS
    reasons: list[str] = []

    if listing.commune not in f["communes"]:
        reasons.append(f"commune {listing.commune!r} not in target set")

    rooms = _effective_rooms(listing)
    if rooms is None or not (f["min_rooms"] <= rooms <= f["max_rooms"]):
        reasons.append(f"rooms {rooms} outside [{f['min_rooms']}, {f['max_rooms']}]")

    if listing.surface_m2 < f["min_surface_m2"]:
        reasons.append(f"surface {listing.surface_m2:.0f} m² < {f['min_surface_m2']}")

    # Per-type price ceiling (furnished is uncapped). An unknown price passes —
    # only a price strictly above the cap is rejected.
    cap = MAX_PRICE_EUR.get(listing.listing_type)
    price = _listing_price(listing)
    if cap is not None and price is not None and price > cap:
        reasons.append(f"price {price:.0f} > {cap} cap for {listing.listing_type}")

    return FilterResult(passed=not reasons, reasons=reasons)


# --------------------------------------------------------------------------- #
# Stage 2: soft score
# --------------------------------------------------------------------------- #
def _energy_subscore(energy_class: str | None) -> float | None:
    if not energy_class:
        return None
    cls = energy_class.upper()
    if cls not in ENERGY_CLASSES:
        return None
    idx = ENERGY_CLASSES.index(cls)  # A=0 ... I=8
    return 1.0 - idx / (len(ENERGY_CLASSES) - 1)


def _lower_is_better(value: int | None, cap: int) -> float | None:
    if value is None:
        return None
    return _clip01(1.0 - value / cap)


@dataclass
class ScoreBreakdown:
    total: float
    parts: dict[str, dict]  # name -> {value, score, neutral, weight, points}


def score_listing(listing: Listing, filters: dict | None = None) -> ScoreBreakdown:
    """Weighted 0–100 score with a per-subscore breakdown (pure; no DB write)."""
    commune_meta = TARGET_COMMUNES.get(listing.commune, {})
    foreign_pct = commune_meta.get("foreign_pct")
    llm = listing.llm_quality_score

    # name -> (displayed raw value, normalized subscore 0..1 or None=unknown)
    raw: dict[str, tuple[object, float | None]] = {
        "bedrooms": (
            listing.bedrooms,
            None if not listing.bedrooms
            else _clip01((listing.bedrooms - 1) / (BED_BEST - 1)),
        ),
        "drive_time": (listing.drive_time_rush_min, _lower_is_better(listing.drive_time_rush_min, DRIVE_NORM_MAX)),
        "pt_time": (listing.pt_time_rush_min, _lower_is_better(listing.pt_time_rush_min, PT_NORM_MAX)),
        "foreign_pct": (
            foreign_pct,
            None if foreign_pct is None
            else _clip01((foreign_pct - FOREIGN_LO) / (FOREIGN_HI - FOREIGN_LO)),
        ),
        "energy_class": (listing.energy_class, _energy_subscore(listing.energy_class)),
        "has_garage": (listing.has_garage, 1.0 if listing.has_garage else 0.0),
        "has_garden": (listing.has_garden, 1.0 if listing.has_garden else 0.0),
        "llm_quality_score": (llm, None if llm is None else _clip01(llm / 100)),
    }

    parts: dict[str, dict] = {}
    total = 0.0
    for name, weight in SCORING_WEIGHTS.items():
        value, score = raw[name]
        effective = NEUTRAL if score is None else score
        points = effective * weight
        total += points
        parts[name] = {
            "value": value,
            "score": round(effective, 3),
            "neutral": score is None,
            "weight": weight,
            "points": round(points, 2),
        }
    return ScoreBreakdown(total=round(total, 1), parts=parts)


def prune_nonmatching(session: Session) -> int:
    """Deactivate already-stored listings that no longer pass the hard filter.

    Retroactively applies the current filters (commune / rooms / surface / price
    cap) to the existing DB — so tightening a filter trims stale rows on the next
    run without a full re-scrape. Deactivates (is_active=False) rather than
    deleting, so it's reversible and keeps price history. Returns the count
    deactivated.
    """
    active = session.query(Listing).filter(Listing.is_active.is_(True)).all()
    pruned = 0
    for listing in active:
        if not passes_hard_filter(listing).passed:
            listing.mark_inactive()
            pruned += 1
    session.commit()
    if pruned:
        logger.info("prune_nonmatching: deactivated %d listing(s) now outside the filters", pruned)
    return pruned


def apply_scores(session: Session, *, only_active: bool = True) -> dict[str, int]:
    """Filter + score active, non-duplicate listings; write ``score_total``.

    ``score_total`` is set for passers and reset to ``None`` for anything that
    fails the hard filter (so it can never go stale). Secondary duplicates
    (``duplicate_of_id`` set) are skipped. Returns ``{scored, filtered_out}``.
    """
    query = session.query(Listing).filter(Listing.duplicate_of_id.is_(None))
    if only_active:
        query = query.filter(Listing.is_active.is_(True))

    scored = filtered = 0
    for listing in query.all():
        if passes_hard_filter(listing).passed:
            listing.score_total = score_listing(listing).total
            scored += 1
        else:
            listing.score_total = None
            filtered += 1
    session.commit()
    logger.info("apply_scores: %d scored, %d filtered out", scored, filtered)
    return {"scored": scored, "filtered_out": filtered}


def top_listings(
    session: Session, *, limit: int = 20, listing_type: str | None = None
) -> list[Listing]:
    """Highest-scoring active, non-duplicate, filter-passing listings."""
    query = session.query(Listing).filter(
        Listing.score_total.isnot(None),
        Listing.is_active.is_(True),
        Listing.duplicate_of_id.is_(None),
    )
    if listing_type:
        query = query.filter(Listing.listing_type == listing_type)
    return query.order_by(Listing.score_total.desc()).limit(limit).all()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def render_shortlist_table(rows: list[Listing]) -> None:
    from rich.console import Console
    from rich.table import Table

    from src.lux_monitor.finance import monthly_mortgage

    table = Table(title="Luxembourg shortlist — top by score", show_lines=False)
    cols = ("#", "Score", "Type", "Commune", "Bd", "m²", "€", "€/mo", "Drive", "PT", "Energy", "Gar", "Grd")
    right = {"Score", "Bd", "m²", "€", "€/mo", "Drive", "PT"}
    for col in cols:
        table.add_column(col, justify="right" if col in right else "left")
    for i, l in enumerate(rows, 1):
        price = l.price_eur if l.listing_type == "buy" else l.rent_total_eur
        # €/mo: rent for rentals; estimated mortgage payment for a buy.
        monthly = monthly_mortgage(l.price_eur) if l.listing_type == "buy" else price
        table.add_row(
            str(i),
            f"{l.score_total:.1f}" if l.score_total is not None else "-",
            l.listing_type,
            l.commune,
            str(l.bedrooms),
            f"{l.surface_m2:.0f}",
            f"{price:,.0f}" if price is not None else "-",
            f"{monthly:,.0f}" if monthly is not None else "-",
            str(l.drive_time_rush_min if l.drive_time_rush_min is not None else "-"),
            str(l.pt_time_rush_min if l.pt_time_rush_min is not None else "-"),
            l.energy_class or "-",
            "✓" if l.has_garage else "",
            "✓" if l.has_garden else "",
        )
    Console().print(table)


def _explain(listing: Listing) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    flt = passes_hard_filter(listing)
    status = "[green]PASS[/green]" if flt.passed else "[red]FAIL[/red] — " + "; ".join(flt.reasons)
    console.print(f"Listing {listing.id} ({listing.portal} {listing.portal_listing_id}) "
                  f"{listing.listing_type} in {listing.commune}: {status}")
    bd = score_listing(listing)
    table = Table(title=f"score_total = {bd.total}")
    for col in ("subscore", "value", "score", "weight", "points", "neutral?"):
        table.add_column(col)
    for name, p in bd.parts.items():
        table.add_row(name, str(p["value"]), f"{p['score']:.2f}", str(p["weight"]),
                      f"{p['points']:.2f}", "yes" if p["neutral"] else "")
    console.print(table)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.lux_monitor.scoring")
    parser.add_argument("--db", help="SQLite path (default: data/monitor.db or $LUX_MONITOR_DB_URL)")
    parser.add_argument("--apply", action="store_true", help="(re)filter + score before listing")
    parser.add_argument("--top", type=int, default=20, help="how many to show (default 20)")
    parser.add_argument("--type", choices=("rent", "buy"), help="restrict to one listing type")
    parser.add_argument("--explain", type=int, metavar="ID", help="show the score breakdown for one listing id")
    args = parser.parse_args(argv)

    from src.lux_monitor.db import get_engine, session_scope

    engine = get_engine(args.db)
    with session_scope(engine) as session:
        if args.apply:
            counts = apply_scores(session)
            print(f"scored={counts['scored']} filtered_out={counts['filtered_out']}")
        if args.explain is not None:
            listing = session.get(Listing, args.explain)
            if listing is None:
                print(f"No listing with id={args.explain}")
                return 1
            _explain(listing)
            return 0
        rows = top_listings(session, limit=args.top, listing_type=args.type)
        if not rows:
            print("No scored listings yet — run with --apply (after scraping + commute).")
            return 0
        render_shortlist_table(rows)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
