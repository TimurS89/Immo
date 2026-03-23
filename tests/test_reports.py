"""Tests for report generation."""

import pytest
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from src.config import AppConfig


TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "src" / "reports" / "templates"


def test_template_renders():
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("report.html")
    html = template.render(
        report_date="March 23, 2026",
        css="body { color: black; }",
        total_active=42,
        new_this_week=5,
        removed_this_week=2,
        price_changes=3,
        scrape_summary=[],
        new_listings_sections=[],
        price_reductions=[],
        price_increases=[],
        chart_price_trend="",
        chart_listings_count="",
        chart_price_distribution="",
        removed_listings=[],
    )
    assert "Weekly Property Report" in html
    assert "42" in html
    assert "March 23, 2026" in html


def test_styles_css_exists():
    css_path = TEMPLATES_DIR / "styles.css"
    assert css_path.exists()
    css = css_path.read_text()
    assert "body" in css
    assert ".listing-table" in css
