"""Luxembourg portal scrapers (athome.lu, immotop.lu, wortimmo.lu).

These feed the canonical ``src.lux_monitor`` model. Live scraping needs the
Playwright browser + network egress and is validated on the workstation; the
pure parsing logic here is unit-tested against fixtures in
``tests/fixtures/luxembourg/``.
"""

from __future__ import annotations

from .athome import AtHomeScraper
from .immotop import ImmotopScraper
from .wortimmo import WortimmoScraper

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
    from src.lux_monitor.scoring import apply_scores

    lu = config.search_areas.get("LU")
    enabled = lu.enabled_portals() if (lu and lu.enabled) else list(LU_SCRAPERS)

    totals = {"new": 0, "updated": 0, "deactivated": 0}
    if scrape:
        for name in enabled:
            scraper_cls = LU_SCRAPERS.get(name)
            if not scraper_cls:
                continue
            scraper = scraper_cls(config)
            listings = await scraper.scrape()
            counts = scraper.save_listings(session, listings)
            for k in totals:
                totals[k] += counts.get(k, 0)

    if dedup:
        totals["duplicates"] = mark_duplicates(session)
    if commute:
        totals["commute_filled"] = populate_commute_times(session)
    if analyze:
        totals["analyzed"] = apply_analysis(session)
    if score:
        totals.update(apply_scores(session))
    return totals
