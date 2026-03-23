"""Chart generation using Plotly."""

from __future__ import annotations

import logging
from datetime import datetime

import plotly.graph_objects as go
from plotly.subplots import make_subplots
from sqlalchemy.orm import Session

from src.analysis.market_stats import get_historical_stats
from src.database.models import MarketSnapshot, Property

logger = logging.getLogger(__name__)


def create_price_trend_chart(session: Session, weeks: int = 26) -> str:
    """Create a price trend chart (median price per sqm over time).

    Returns HTML string with the chart.
    """
    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=("Germany (Baden-Baden) - Buy", "France (Alsace) - Buy"),
    )

    for col, country in enumerate(["DE", "FR"], 1):
        stats = get_historical_stats(session, country=country, listing_type="buy")
        if not stats:
            continue

        # Group by property type
        by_type: dict[str, list[MarketSnapshot]] = {}
        for s in stats:
            by_type.setdefault(s.property_type, []).append(s)

        for ptype, snapshots in by_type.items():
            dates = [s.snapshot_date for s in snapshots]
            prices = [s.median_price_per_sqm for s in snapshots]

            fig.add_trace(
                go.Scatter(
                    x=dates, y=prices,
                    name=f"{ptype.capitalize()} ({country})",
                    mode="lines+markers",
                ),
                row=1, col=col,
            )

    fig.update_layout(
        title="Median Price per m² - Buy",
        height=400,
        template="plotly_white",
        showlegend=True,
    )
    fig.update_yaxes(title_text="EUR/m²")

    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def create_listings_count_chart(session: Session, weeks: int = 26) -> str:
    """Create a chart showing total active listings over time."""
    fig = go.Figure()

    for country, label in [("DE", "Germany"), ("FR", "France")]:
        stats = get_historical_stats(session, country=country, listing_type="buy")
        if not stats:
            continue

        # Aggregate total per date
        by_date: dict[datetime, int] = {}
        for s in stats:
            by_date[s.snapshot_date] = by_date.get(s.snapshot_date, 0) + s.total_listings

        dates = sorted(by_date.keys())
        counts = [by_date[d] for d in dates]

        fig.add_trace(go.Bar(x=dates, y=counts, name=f"{label} - Buy"))

    fig.update_layout(
        title="Total Active Listings Over Time",
        xaxis_title="Date",
        yaxis_title="Number of Listings",
        barmode="group",
        height=350,
        template="plotly_white",
    )

    return fig.to_html(full_html=False, include_plotlyjs=False)


def create_price_distribution_chart(session: Session) -> str:
    """Create price distribution histograms for current active listings."""
    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=("Germany - Buy Prices", "France - Buy Prices"),
    )

    for col, country in enumerate(["DE", "FR"], 1):
        prices = [
            p.price for p in session.query(Property).filter(
                Property.is_active.is_(True),
                Property.country == country,
                Property.listing_type == "buy",
                Property.price.isnot(None),
            ).all()
        ]

        if prices:
            fig.add_trace(
                go.Histogram(
                    x=prices,
                    nbinsx=30,
                    name=f"{country} Prices",
                    marker_color="#4A90D9" if country == "DE" else "#E74C3C",
                ),
                row=1, col=col,
            )

    fig.update_layout(
        title="Price Distribution (Buy)",
        height=350,
        template="plotly_white",
        showlegend=False,
    )
    fig.update_xaxes(title_text="Price (EUR)")
    fig.update_yaxes(title_text="Count")

    return fig.to_html(full_html=False, include_plotlyjs=False)


def create_new_vs_removed_chart(snapshots: list[MarketSnapshot]) -> str:
    """Create a chart comparing new vs removed listings per week."""
    fig = go.Figure()

    dates = sorted(set(s.snapshot_date for s in snapshots))

    for date in dates:
        week_snaps = [s for s in snapshots if s.snapshot_date == date]
        new_total = sum(s.new_listings_this_week for s in week_snaps)
        removed_total = sum(s.removed_listings_this_week for s in week_snaps)

        fig.add_trace(go.Bar(x=[date], y=[new_total], name="New", marker_color="#2ECC71", showlegend=(date == dates[0])))
        fig.add_trace(go.Bar(x=[date], y=[-removed_total], name="Removed", marker_color="#E74C3C", showlegend=(date == dates[0])))

    fig.update_layout(
        title="New vs Removed Listings per Week",
        xaxis_title="Week",
        yaxis_title="Count",
        barmode="relative",
        height=350,
        template="plotly_white",
    )

    return fig.to_html(full_html=False, include_plotlyjs=False)
