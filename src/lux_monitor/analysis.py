"""Phase 6 — offline, rule-based description analysis (no LLM/API).

Per the chosen approach we do *not* call an external model. Instead we scan the
listing title + description with a small multilingual (FR/DE/EN), accent-
insensitive keyword ruleset, combine it with a few structured cross-checks, and
produce the four fields the rest of the system already expects:

    llm_quality_score : int 0..100   (the 7% scoring subscore)
    llm_red_flags     : list[str]
    llm_highlights    : list[str]
    llm_summary       : str

The ``llm_`` column names are kept (they predate this decision) to avoid a schema
migration — they're now a misnomer: this is heuristics, not an LLM.

Scope note: signals already weighted by ``scoring.py`` (drive/PT/foreign%/walk/
energy/garage/garden/ground-floor) are deliberately **excluded** here to avoid
double-counting. This module focuses on what those miss — condition, lease terms,
noise, brightness, elevator-vs-floor, charges ratio, and build/renovation recency.
All weights are tunable constants.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from src.lux_monitor.models import Listing

logger = logging.getLogger(__name__)

BASE_SCORE = 62  # a description with no notable signals lands here
RECENT_YEARS = 5  # construction/renovation within N years counts as "recent"


def _normalize(text: str) -> str:
    """Lowercase, strip diacritics, and collapse whitespace.

    De-accenting lets 'rénové' and 'renove' both match; whitespace collapsing
    keeps multi-word keywords ('proche transports') matching across the newlines
    / runs of spaces that appear in live HTML.
    """
    decomposed = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped)


@dataclass(frozen=True)
class _Rule:
    label: str
    patterns: tuple[re.Pattern[str], ...]
    weight: int  # magnitude; sign comes from which list it's in


def _rule(label: str, weight: int, *keywords: str) -> _Rule:
    # Word-boundary match so 'renove' (renovated) doesn't fire on 'renover'
    # (to renovate). Keywords are normalized (lowercased, de-accented) first.
    patterns = tuple(re.compile(rf"\b{re.escape(_normalize(k))}\b") for k in keywords)
    return _Rule(label, patterns, weight)


# Penalties (subtracted). Order = output order.
RED_FLAG_RULES: tuple[_Rule, ...] = (
    _rule("needs renovation", 18,
          "à rénover", "travaux à prévoir", "à rafraîchir", "rénovation nécessaire",
          "renovierungsbedürftig", "sanierungsbedürftig", "to renovate",
          "needs renovation", "fixer-upper", "in need of renovation"),
    _rule("viager (life annuity)", 40, "viager"),
    _rule("busy road / noise", 12,
          "axe passant", "route passante", "proche autoroute", "nuisances sonores",
          "vielbefahren", "hauptstrasse", "hauptstraße", "busy road", "main road",
          "traffic noise"),
    _rule("furnished / short-term lease", 8,
          "meublé", "bail de courte durée", "location temporaire", "befristet",
          "möbliert", "short-term", "short term", "furnished", "temporary lease"),
    _rule("no pets", 5,
          "pas d'animaux", "animaux non admis", "keine haustiere", "no pets"),
)

# Bonuses (added).
HIGHLIGHT_RULES: tuple[_Rule, ...] = (
    _rule("renovated / new", 14,
          # French adjective inflects for gender/number (maison rénovée, ...).
          # "neuf" is also "nine" but room counts use digits, so we keep it.
          "rénové", "rénovée", "rénovés", "rénovées", "neuf", "neuve",
          "construction récente", "entièrement rénové", "entièrement rénovée",
          "renoviert", "neubau", "neuwertig", "renovated", "new build",
          "brand new", "newly built"),
    _rule("bright / sunny", 5,
          "lumineux", "lumineuse", "ensoleillé", "ensoleillée", "hell",
          "lichtdurchflutet", "bright", "sunny"),
    _rule("quiet / residential", 6,
          "calme", "quartier résidentiel", "ruhig", "quiet", "residential area"),
    _rule("near transport / school", 6,
          "proche transports", "proche gare", "proche écoles", "proche école",
          "nähe schule", "öffentliche verkehrsmittel", "near school",
          "close to transport", "near station", "public transport"),
    _rule("spacious", 5, "spacieux", "spacieuse", "geräumig", "spacious"),
)


@dataclass
class DescriptionAnalysis:
    quality_score: int
    red_flags: list[str] = field(default_factory=list)
    highlights: list[str] = field(default_factory=list)
    summary: str = ""


def _structured_signals(listing: Listing, year: int) -> tuple[list[str], list[str], int]:
    """Signals from structured fields not already covered by scoring.py."""
    red: list[str] = []
    high: list[str] = []
    delta = 0

    if listing.has_elevator is False and listing.floor is not None and listing.floor >= 3:
        red.append(f"no elevator (floor {listing.floor})")
        delta -= 8
    if listing.charges_eur and listing.rent_eur and listing.charges_eur > 0.25 * listing.rent_eur:
        pct = round(100 * listing.charges_eur / listing.rent_eur)
        red.append(f"high charges ({pct}% of rent)")
        delta -= 6
    if listing.deposit_months and listing.deposit_months >= 3:
        red.append(f"high deposit ({listing.deposit_months} months)")
        delta -= 3
    if listing.construction_year and listing.construction_year >= year - RECENT_YEARS:
        high.append(f"recent construction ({listing.construction_year})")
        delta += 10
    if listing.renovation_year and listing.renovation_year >= year - RECENT_YEARS:
        high.append(f"recently renovated ({listing.renovation_year})")
        delta += 8

    return red, high, delta


def _summarize(listing: Listing, highlights: list[str], red_flags: list[str]) -> str:
    head = f"{listing.bedrooms}-bed {listing.surface_m2:.0f} m² in {listing.commune}."
    parts = []
    if highlights:
        parts.append("Pluses: " + ", ".join(highlights[:3]))
    if red_flags:
        parts.append("Watch: " + ", ".join(red_flags[:3]))
    return f"{head} " + (" | ".join(parts) if parts else "No notable description signals.")


def analyze_listing(listing: Listing, *, now_year: int | None = None) -> DescriptionAnalysis:
    """Heuristic analysis of one listing (pure; no DB write)."""
    year = now_year or datetime.now().year
    text = _normalize(f"{listing.title or ''}\n{listing.description_raw or ''}")

    red_flags: list[str] = []
    highlights: list[str] = []
    score = BASE_SCORE

    for rule in RED_FLAG_RULES:
        if any(p.search(text) for p in rule.patterns):
            red_flags.append(rule.label)
            score -= rule.weight
    for rule in HIGHLIGHT_RULES:
        if any(p.search(text) for p in rule.patterns):
            highlights.append(rule.label)
            score += rule.weight

    s_red, s_high, delta = _structured_signals(listing, year)
    red_flags.extend(s_red)
    highlights.extend(s_high)
    score += delta

    score = max(0, min(100, score))
    return DescriptionAnalysis(
        quality_score=score,
        red_flags=red_flags,
        highlights=highlights,
        summary=_summarize(listing, highlights, red_flags),
    )


def apply_analysis(
    session: Session, *, only_missing: bool = True, now_year: int | None = None
) -> int:
    """Analyze active, non-duplicate listings and write the ``llm_*`` fields.

    Returns the number updated. Run before scoring so the quality subscore is
    populated (otherwise it stays neutral).
    """
    query = session.query(Listing).filter(
        Listing.duplicate_of_id.is_(None), Listing.is_active.is_(True)
    )
    if only_missing:
        query = query.filter(Listing.llm_quality_score.is_(None))

    updated = 0
    for listing in query.all():
        analysis = analyze_listing(listing, now_year=now_year)
        listing.llm_quality_score = analysis.quality_score
        listing.llm_red_flags = analysis.red_flags
        listing.llm_highlights = analysis.highlights
        listing.llm_summary = analysis.summary
        updated += 1
    session.commit()
    logger.info("apply_analysis: %d listings analyzed", updated)
    return updated
