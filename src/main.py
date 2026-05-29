"""Main entry point / orchestrator for the property search automation."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from src.analysis.deduplication import deduplicate_properties
from src.config import AppConfig, load_config
from src.database.db import get_session, init_db
from src.database.models import Property, ScrapeRun
from src.reports.generator import generate_report
from src.scrapers.base import BaseScraper

console = Console()

# Scraper registry: source_name -> scraper class
SCRAPER_REGISTRY: dict[str, type[BaseScraper]] = {}


def _register_scrapers():
    """Lazy-import and register all scraper classes."""
    from src.scrapers.germany.immoscout24 import ImmoScout24Scraper
    from src.scrapers.germany.immowelt import ImmoweltScraper
    from src.scrapers.germany.kleinanzeigen import KleinanzeigenScraper
    from src.scrapers.germany.wohnungsboerse import WohnungsboerseScraper
    from src.scrapers.france.leboncoin import LeBonCoinScraper
    from src.scrapers.france.seloger import SeLogerScraper
    from src.scrapers.france.bienici import BienIciScraper
    from src.scrapers.france.pap import PAPScraper
    from src.scrapers.france.paruvendu import ParuVenduScraper

    SCRAPER_REGISTRY.update({
        "immoscout24": ImmoScout24Scraper,
        "immowelt": ImmoweltScraper,
        "kleinanzeigen": KleinanzeigenScraper,
        "wohnungsboerse": WohnungsboerseScraper,
        "leboncoin": LeBonCoinScraper,
        "seloger": SeLogerScraper,
        "bienici": BienIciScraper,
        "pap": PAPScraper,
        "paruvendu": ParuVenduScraper,
    })


def setup_logging(config: AppConfig) -> None:
    """Configure logging with Rich handler."""
    log_file = Path(config.logging.file)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    handlers: list[logging.Handler] = [
        RichHandler(console=console, rich_tracebacks=True, show_time=True),
        logging.FileHandler(str(log_file)),
    ]

    logging.basicConfig(
        level=getattr(logging, config.logging.level.upper(), logging.INFO),
        format="%(message)s",
        datefmt="[%X]",
        handlers=handlers,
    )


def get_enabled_scrapers(config: AppConfig) -> list[str]:
    """Enabled scraper names: the enabled portals of every enabled country."""
    enabled: list[str] = []
    for country_cfg in config.search_areas.values():
        if country_cfg.enabled:
            enabled.extend(country_cfg.enabled_portals())
    return enabled


async def run_scrapers(config: AppConfig) -> list[ScrapeRun]:
    """Run all enabled scrapers and save results."""
    logger = logging.getLogger(__name__)
    _register_scrapers()

    enabled = get_enabled_scrapers(config)
    scrape_runs = []

    with get_session(config.database.path) as session:
        for source_name in enabled:
            scraper_cls = SCRAPER_REGISTRY.get(source_name)
            if not scraper_cls:
                logger.warning(f"Unknown scraper: {source_name}")
                continue

            logger.info(f"Starting scraper: {source_name}")
            scraper = scraper_cls(config)

            try:
                properties = await scraper.scrape()
                run = scraper.save_results(session, properties)
                scrape_runs.append(run)
                logger.info(f"Completed {source_name}: {run.listings_found} found, {run.new_listings} new")
            except Exception:
                logger.exception(f"Scraper {source_name} failed")
                run = ScrapeRun(
                    source=source_name,
                    started_at=datetime.now(timezone.utc),
                    completed_at=datetime.now(timezone.utc),
                    status="failed",
                )
                session.add(run)
                session.commit()
                scrape_runs.append(run)

        # Mark properties not seen in this run as potentially inactive
        _mark_inactive(session, enabled)

    return scrape_runs


def _mark_inactive(session, scraped_sources: list[str]) -> None:
    """Mark properties as inactive if they weren't seen in this scrape run."""
    logger = logging.getLogger(__name__)
    # Only mark inactive for sources that were actually scraped
    for source in scraped_sources:
        # Get the latest scrape run for this source
        latest_run = (
            session.query(ScrapeRun)
            .filter(ScrapeRun.source == source, ScrapeRun.status.in_(["success", "partial"]))
            .order_by(ScrapeRun.completed_at.desc())
            .first()
        )
        if not latest_run or not latest_run.completed_at:
            continue

        # Properties from this source that weren't updated in this run
        stale = (
            session.query(Property)
            .filter(
                Property.source == source,
                Property.is_active.is_(True),
                Property.last_seen_at < latest_run.started_at,
            )
            .all()
        )

        for prop in stale:
            prop.is_active = False

        if stale:
            logger.info(f"Marked {len(stale)} {source} listings as inactive")

    session.commit()


async def run_pipeline(config: AppConfig) -> None:
    """Run the complete pipeline: scrape -> analyze -> report."""
    logger = logging.getLogger(__name__)

    console.rule("[bold blue]Immo Property Search Automation")
    logger.info("Starting property search pipeline")

    # 1. Initialize database
    init_db(config.database.path)
    logger.info("Database initialized")

    # 2. Run scrapers
    console.rule("[bold]Scraping")
    scrape_runs = await run_scrapers(config)
    logger.info(f"Scraping complete: {len(scrape_runs)} sources processed")

    # 3. Deduplication
    console.rule("[bold]Analysis")
    with get_session(config.database.path) as session:
        dupe_count = deduplicate_properties(session)
        logger.info(f"Deduplication: {dupe_count} duplicates found")

        # 4. Generate report
        console.rule("[bold]Report Generation")
        result = generate_report(session, config, scrape_runs)
        logger.info(
            f"Report: {result['total_active']} active, {result['new_count']} new, "
            f"email_sent={result['email_sent']}"
        )

    if result["pdf_path"]:
        console.print(f"[green]PDF report saved: {result['pdf_path']}")
    if result["email_sent"]:
        console.print("[green]Report email sent successfully")

    console.rule("[bold green]Pipeline Complete")


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Immo Property Search Automation")
    parser.add_argument("--config", "-c", type=str, help="Path to config.yaml")
    parser.add_argument(
        "--scrape-only", action="store_true", help="Only run scrapers, skip report"
    )
    parser.add_argument(
        "--report-only", action="store_true", help="Only generate report from existing data"
    )
    parser.add_argument(
        "--dashboard", action="store_true", help="Launch Streamlit dashboard"
    )
    parser.add_argument(
        "--init-db", action="store_true", help="Initialize database and exit"
    )
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config)
    logger = logging.getLogger(__name__)

    if args.dashboard:
        import subprocess
        subprocess.run([
            sys.executable, "-m", "streamlit", "run",
            str(Path(__file__).parent / "dashboard" / "app.py"),
            "--server.port", str(config.dashboard.port),
            "--server.address", config.dashboard.host,
        ])
        return

    if args.init_db:
        init_db(config.database.path)
        console.print("[green]Database initialized successfully")
        return

    if args.report_only:
        init_db(config.database.path)
        with get_session(config.database.path) as session:
            result = generate_report(session, config)
        console.print(f"[green]Report generated: {result['total_active']} active listings")
        return

    # Full pipeline or scrape-only
    asyncio.run(run_pipeline(config))


if __name__ == "__main__":
    main()
