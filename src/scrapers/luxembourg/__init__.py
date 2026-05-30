"""Luxembourg portal scrapers (athome.lu, immotop.lu, wortimmo.lu).

These feed the canonical ``src.lux_monitor`` model. Live scraping needs the
Playwright browser + network egress and is validated on the workstation; the
pure parsing logic here is unit-tested against fixtures in
``tests/fixtures/luxembourg/``.
"""

from __future__ import annotations

import logging

from .athome import AtHomeScraper
from .immotop import ImmotopScraper
from .wortimmo import WortimmoScraper

logger = logging.getLogger(__name__)

# LU source registry (name -> scraper class). Mirrors the legacy SCRAPER_REGISTRY
# but targets the lux_monitor schema.
LU_SCRAPERS = {
    AtHomeScraper.SOURCE_NAME: AtHomeScraper,
    ImmotopScraper.SOURCE_NAME: ImmotopScraper,
    WortimmoScraper.SOURCE_NAME: WortimmoScraper,
}

__all__ = ["AtHomeScraper", "ImmotopScraper", "WortimmoScraper", "LU_SCRAPERS", "run_luxembourg"]


async def run_luxembourg(
    config,
    session,
    *,
    scrape: bool = True,
    dedup: bool = True,
    commute: bool = True,
    analyze: bool = True,
    score: bool = True,
) -> dict:
    """Scrape all enabled LU portals, persist, de-dup, estimate commutes,
    analyze descriptions, and score.

    Intended entry point for the workstation live run (Phase 3 item 7). The
    post-scrape stages run in dependency order: dedup -> commute -> analyze ->
    score (scoring reads both the commute times and the description quality).
    Pass ``scrape=False`` to recompute the offline stages on existing data
    without any network access.
    """
    from src.lux_monitor.analysis import apply_analysis
    from src.lux_monitor.commute import populate_commute_times
    from src.lux_monitor.dedup import mark_duplicates
    from src.lux_monitor.scoring import apply_scores, passes_hard_filter

    lu = config.search_areas.get("LU")
    enabled = lu.enabled_portals() if (lu and lu.enabled) else list(LU_SCRAPERS)

    totals = {"new": 0, "updated": 0, "deactivated": 0}
    if scrape:
        errors = 0
        for name in enabled:
            scraper_cls = LU_SCRAPERS.get(name)
            if not scraper_cls:
                continue
            scraper = scraper_cls(config)
            try:
                listings = await scraper.scrape()
                # Persist only listings that align with the hard filter (commune /
                # rooms / surface) — portal searches return far more than we want.
                listings = [lc for lc in listings if passes_hard_filter(lc).passed]
                counts = scraper.save_listings(session, listings)
            except Exception as exc:
                # A single portal failing (DNS, network, parse, site change) must
                # never abort the whole run — log it concisely (full traceback only
                # at debug), drop any partial state, and carry on with the others.
                logger.warning("scraper %r failed; skipping it: %s", name, exc)
                logger.debug("scraper %r traceback", name, exc_info=True)
                session.rollback()
                errors += 1
                continue
            for k in ("new", "updated", "deactivated"):
                totals[k] += counts.get(k, 0)
        if errors:
            totals["scraper_errors"] = errors

    if dedup:
        totals["duplicates"] = mark_duplicates(session)
    if commute:
        totals["commute_filled"] = populate_commute_times(session)
    if analyze:
        totals["analyzed"] = apply_analysis(session)
    if score:
        totals.update(apply_scores(session))
    return totals
