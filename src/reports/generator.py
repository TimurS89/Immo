"""Report orchestrator - combines data, charts, and templates."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from src.analysis.market_stats import compute_market_snapshots, get_historical_stats
from src.analysis.price_tracker import track_price_changes
from src.config import AppConfig
from src.database.models import Property, ScrapeRun
from src.reports.charts import (
    create_listings_count_chart,
    create_new_vs_removed_chart,
    create_price_distribution_chart,
    create_price_trend_chart,
)
from src.reports.email_sender import send_report_email
from src.reports.pdf import generate_pdf

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"


def generate_report(session: Session, config: AppConfig, scrape_runs: list[ScrapeRun] | None = None) -> dict:
    """Generate the full weekly report.

    Returns a dict with keys: html, pdf_path, email_sent.
    """
    today = datetime.now(timezone.utc)
    one_week_ago = today - timedelta(days=7)
    report_date = today.strftime("%B %d, %Y")

    # --- Data collection ---

    # Active listings counts
    total_active = session.query(Property).filter(Property.is_active.is_(True)).count()

    # New listings this week
    new_properties = (
        session.query(Property)
        .filter(
            Property.is_active.is_(True),
            Property.first_seen_at >= one_week_ago,
        )
        .order_by(Property.first_seen_at.desc())
        .all()
    )

    # Removed listings this week
    removed_listings = (
        session.query(Property)
        .filter(
            Property.is_active.is_(False),
            Property.last_seen_at >= one_week_ago,
        )
        .order_by(Property.last_seen_at.desc())
        .all()
    )

    # Price changes
    price_changes = track_price_changes(session, days=7)
    price_reductions = [pc for pc in price_changes if pc.change_amount < 0]
    price_increases = [pc for pc in price_changes if pc.change_amount > 0]

    # Market snapshots
    snapshots = compute_market_snapshots(session)

    # --- Organize new listings by section ---
    new_listings_sections = []
    for country, country_label in [("DE", "Germany (Baden-Baden)"), ("FR", "France (Alsace)")]:
        for lt, lt_label in [("buy", "Buy"), ("rent", "Rent")]:
            section_listings = [
                p for p in new_properties
                if p.country == country and p.listing_type == lt
            ]
            if section_listings:
                new_listings_sections.append(
                    (f"New: {lt_label} - {country_label}", section_listings)
                )

    # --- Charts ---
    chart_price_trend = create_price_trend_chart(session)
    chart_listings_count = create_listings_count_chart(session)
    chart_price_distribution = create_price_distribution_chart(session)

    all_snapshots = get_historical_stats(session, weeks=26)
    chart_new_vs_removed = create_new_vs_removed_chart(all_snapshots) if all_snapshots else ""

    # --- Render HTML ---
    css = (TEMPLATES_DIR / "styles.css").read_text()

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("report.html")

    html = template.render(
        report_date=report_date,
        css=css,
        total_active=total_active,
        new_this_week=len(new_properties),
        removed_this_week=len(removed_listings),
        price_changes=len(price_changes),
        scrape_summary=scrape_runs,
        new_listings_sections=new_listings_sections,
        price_reductions=price_reductions,
        price_increases=price_increases,
        chart_price_trend=chart_price_trend,
        chart_listings_count=chart_listings_count,
        chart_price_distribution=chart_price_distribution,
        removed_listings=removed_listings,
    )

    # --- PDF ---
    pdf_path = generate_pdf(html, config)

    # --- Email ---
    email_sent = False
    subject = f"Weekly Property Report - {report_date}"
    if config.reports.email.enabled:
        email_sent = send_report_email(config, subject, html, pdf_path)

    result = {
        "html": html,
        "pdf_path": pdf_path,
        "email_sent": email_sent,
        "total_active": total_active,
        "new_count": len(new_properties),
        "removed_count": len(removed_listings),
        "price_changes_count": len(price_changes),
    }

    logger.info(
        f"Report generated: {total_active} active, {len(new_properties)} new, "
        f"{len(removed_listings)} removed, {len(price_changes)} price changes"
    )
    return result
