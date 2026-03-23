"""Streamlit dashboard for browsing property listings."""

from __future__ import annotations

import streamlit as st
from sqlalchemy import func

from src.config import load_config
from src.database.db import get_session, init_db
from src.database.models import MarketSnapshot, PriceHistory, Property


def main():
    st.set_page_config(
        page_title="Immo Property Dashboard",
        page_icon="🏠",
        layout="wide",
    )

    config = load_config()
    init_db(config.database.path)
    session = get_session(config.database.path)

    st.title("Property Search Dashboard")
    st.caption("Baden-Baden (DE) & Alsace (FR)")

    # Sidebar filters
    st.sidebar.header("Filters")
    country = st.sidebar.selectbox("Country", ["All", "DE", "FR"])
    listing_type = st.sidebar.selectbox("Type", ["All", "buy", "rent"])
    property_type = st.sidebar.selectbox("Property", ["All", "apartment", "house", "land"])
    min_price = st.sidebar.number_input("Min Price (EUR)", value=0, step=10000)
    max_price = st.sidebar.number_input("Max Price (EUR)", value=1000000, step=10000)
    min_area = st.sidebar.number_input("Min Area (m²)", value=0, step=10)
    only_active = st.sidebar.checkbox("Active only", value=True)

    # Build query
    query = session.query(Property)
    if only_active:
        query = query.filter(Property.is_active.is_(True))
    if country != "All":
        query = query.filter(Property.country == country)
    if listing_type != "All":
        query = query.filter(Property.listing_type == listing_type)
    if property_type != "All":
        query = query.filter(Property.property_type == property_type)
    if min_price > 0:
        query = query.filter(Property.price >= min_price)
    if max_price < 1000000:
        query = query.filter(Property.price <= max_price)
    if min_area > 0:
        query = query.filter(Property.living_area_sqm >= min_area)

    total = query.count()
    properties = query.order_by(Property.first_seen_at.desc()).limit(200).all()

    # Summary metrics
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Matching", total)

    active_total = session.query(func.count(Property.id)).filter(Property.is_active.is_(True)).scalar()
    col2.metric("Total Active", active_total)

    avg_price = query.with_entities(func.avg(Property.price)).scalar()
    col3.metric("Avg Price", f"€{avg_price:,.0f}" if avg_price else "N/A")

    avg_ppsm = query.with_entities(func.avg(Property.price_per_sqm)).scalar()
    col4.metric("Avg €/m²", f"€{avg_ppsm:,.0f}" if avg_ppsm else "N/A")

    # Listings table
    st.subheader(f"Listings ({total} total, showing max 200)")

    if properties:
        table_data = []
        for p in properties:
            table_data.append({
                "Title": p.title[:60],
                "Price (EUR)": f"{p.price:,.0f}" if p.price else "N/A",
                "EUR/m²": f"{p.price_per_sqm:,.0f}" if p.price_per_sqm else "N/A",
                "Area (m²)": f"{p.living_area_sqm:.0f}" if p.living_area_sqm else "N/A",
                "Rooms": p.rooms or "N/A",
                "City": p.address_city or "",
                "Source": p.source,
                "Country": p.country,
                "Type": p.listing_type,
                "URL": p.listing_url,
            })
        st.dataframe(table_data, use_container_width=True)
    else:
        st.info("No listings found matching your filters.")

    # Market trends
    st.subheader("Market Trends")
    snapshots = (
        session.query(MarketSnapshot)
        .order_by(MarketSnapshot.snapshot_date.desc())
        .limit(100)
        .all()
    )

    if snapshots:
        import plotly.graph_objects as go

        fig = go.Figure()
        for ct in ["DE", "FR"]:
            filtered = [s for s in snapshots if s.country == ct and s.listing_type == "buy"]
            if filtered:
                fig.add_trace(go.Scatter(
                    x=[s.snapshot_date for s in filtered],
                    y=[s.median_price_per_sqm for s in filtered],
                    name=f"{ct} - Median EUR/m²",
                    mode="lines+markers",
                ))
        fig.update_layout(title="Median Price per m² (Buy)", xaxis_title="Date", yaxis_title="EUR/m²")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No market data yet. Run a scrape first.")

    session.close()


if __name__ == "__main__":
    main()
