"""Streamlit dashboard for the Luxembourg property monitor.

Run it (after `pip install streamlit`):

    python -m src.lux_monitor dashboard        # serves on 0.0.0.0:8501
    # then open http://<this-machine-LAN-IP>:8501 on your phone (same Wi-Fi)

or directly:  streamlit run src/lux_monitor/dashboard.py

Read-only view of data/monitor.db — browse / filter / sort the shortlist and
click through to the adverts. Refresh the page after a new scrape run.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from src.lux_monitor.db import get_engine, session_scope
from src.lux_monitor.models import Listing

st.set_page_config(page_title="LU Property Monitor", page_icon="🏠", layout="wide")


@st.cache_data(ttl=120)
def load_rows() -> pd.DataFrame:
    with session_scope(get_engine()) as session:
        listings = (
            session.query(Listing)
            .filter(Listing.is_active.is_(True), Listing.duplicate_of_id.is_(None))
            .all()
        )
        records = []
        for l in listings:
            price = l.price_eur if l.listing_type == "buy" else l.rent_total_eur
            records.append(
                {
                    "score": l.score_total,
                    "type": l.listing_type,
                    "commune": l.commune,
                    "rooms": l.rooms_total or l.bedrooms,
                    "bd": l.bedrooms,
                    "m²": l.surface_m2,
                    "€": price,
                    "drive": l.drive_time_rush_min,
                    "PT": l.pt_time_rush_min,
                    "energy": l.energy_class,
                    "garage": bool(l.has_garage),
                    "garden": bool(l.has_garden),
                    "highlights": ", ".join(l.llm_highlights or []),
                    "flags": ", ".join(l.llm_red_flags or []),
                    "portal": l.portal,
                    "first seen": l.first_seen_at.date().isoformat() if l.first_seen_at else None,
                    "link": l.url,
                }
            )
    return pd.DataFrame(records)


@st.cache_data(ttl=120)
def load_snapshots() -> pd.DataFrame:
    from src.lux_monitor.models import MarketSnapshot

    with session_scope(get_engine()) as session:
        rows = session.query(MarketSnapshot).all()
        recs = [
            {
                "date": r.snapshot_date.date(),
                "type": r.listing_type,
                "commune": r.commune,
                "count": r.count,
                "median €": r.median_price_eur,
                "median €/m²": r.median_price_per_m2_eur,
                "median m²": r.median_surface_m2,
                "new": r.new_count,
            }
            for r in rows
        ]
    return pd.DataFrame(recs)


@st.cache_data(ttl=120)
def load_price_drops() -> pd.DataFrame:
    from src.lux_monitor.digest import price_drops

    with session_scope(get_engine()) as session:
        rows = [
            {
                "commune": d.listing.commune,
                "type": d.listing.listing_type,
                "old €": d.old_price,
                "new €": d.new_price,
                "Δ%": d.pct,
                "score": d.listing.score_total,
                "link": d.listing.url,
            }
            for d in price_drops(session, days=30)
        ]
    return pd.DataFrame(rows)


df = load_rows()
st.title("🏠 Luxembourg Property Monitor")

if df.empty:
    st.warning("No listings yet — run `python -m src.lux_monitor run` first.")
    st.stop()

# --- filters (sidebar) ---
st.sidebar.header("Filters")
if st.sidebar.button("🔄 Reload data"):  # cache_data persists across refreshes
    st.cache_data.clear()
    st.rerun()
scored_only = st.sidebar.toggle("Only matches (scored)", value=True)
all_types = sorted(df["type"].dropna().unique())
all_communes = sorted(df["commune"].dropna().unique())
types = st.sidebar.multiselect("Type", all_types, default=all_types)
communes = st.sidebar.multiselect("Commune", all_communes, default=all_communes)
min_score = st.sidebar.slider("Min score", 0, 100, 0)
sort_by = st.sidebar.selectbox("Sort by", ["score", "€", "first seen", "drive", "PT", "m²"])
ascending = st.sidebar.toggle("Ascending", value=False)
st.sidebar.divider()
new_only = st.sidebar.toggle("🆕 New only", value=False)
new_days = st.sidebar.slider("…first seen within (days)", 1, 30, 7) if new_only else 7

view = df.copy()
if scored_only:
    view = view[view["score"].notna()]
view = view[view["type"].isin(types) & view["commune"].isin(communes)]
if min_score:
    view = view[view["score"].fillna(0) >= min_score]
if new_only:
    cutoff = (date.today() - timedelta(days=new_days)).isoformat()
    view = view[view["first seen"].fillna("") >= cutoff]
view = view.sort_values(by=sort_by, ascending=ascending, na_position="last")

# --- KPIs ---
c1, c2, c3, c4 = st.columns(4)
c1.metric("Shown", len(view))
c2.metric("Matches (scored)", int(df["score"].notna().sum()))
c3.metric("Total active", len(df))
c4.metric("Best score", f"{view['score'].max():.0f}" if view["score"].notna().any() else "–")

# --- table ---
st.dataframe(
    view,
    use_container_width=True,
    hide_index=True,
    column_config={
        "link": st.column_config.LinkColumn("link", display_text="open ↗"),
        "score": st.column_config.NumberColumn("score", format="%.1f"),
        "€": st.column_config.NumberColumn("€", format="%d"),
        "m²": st.column_config.NumberColumn("m²", format="%d"),
    },
)
st.caption(
    "Source: data/monitor.db (read-only) · refresh after a new run · "
    "a blank score means the listing was stored but falls outside the hard filter."
)

st.subheader("📉 Recent price drops (last 30 days)")
drops_df = load_price_drops()
if drops_df.empty:
    st.caption("No price drops recorded yet.")
else:
    st.dataframe(
        drops_df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "link": st.column_config.LinkColumn("link", display_text="open ↗"),
            "old €": st.column_config.NumberColumn("old €", format="%d"),
            "new €": st.column_config.NumberColumn("new €", format="%d"),
            "Δ%": st.column_config.NumberColumn("Δ%", format="%.1f%%"),
            "score": st.column_config.NumberColumn("score", format="%.1f"),
        },
    )

# --- market trends over time (the rent-vs-buy / now-vs-later view) ---
st.subheader("📈 Market trends over time")
snaps = load_snapshots()
if snaps.empty:
    st.caption("No snapshots yet — they accumulate one point per run. Come back after a few daily runs.")
else:
    tcol, ccol, mcol = st.columns(3)
    t_opts = sorted(snaps["type"].unique())
    t_sel = tcol.selectbox("Type", t_opts, key="trend_type")
    communes_for_type = sorted(snaps[snaps["type"] == t_sel]["commune"].unique())
    default_commune = "All" if "All" in communes_for_type else communes_for_type[0]
    c_sel = ccol.selectbox("Commune", communes_for_type,
                           index=communes_for_type.index(default_commune), key="trend_commune")
    metric = mcol.selectbox(
        "Metric", ["median €", "median €/m²", "count", "new", "median m²"], key="trend_metric")

    series = (
        snaps[(snaps["type"] == t_sel) & (snaps["commune"] == c_sel)]
        .sort_values("date")
        .set_index("date")
    )
    if len(series) < 2:
        st.info(f"Only {len(series)} snapshot so far for this segment — the line appears once there are ≥2 daily runs.")
    st.line_chart(series[metric], height=320)
    latest = series.iloc[-1]
    k1, k2, k3 = st.columns(3)
    k1.metric("Listings now", int(latest["count"]))
    if pd.notna(latest["median €"]):
        k2.metric("Median price", f"€{latest['median €']:,.0f}")
    if pd.notna(latest["median €/m²"]):
        k3.metric("Median €/m²", f"€{latest['median €/m²']:,.0f}")
    st.caption(
        "Each point is one run. Compare buy €/m² vs rent over months to judge whether to "
        "buy or rent, now or later. Snapshots are recorded automatically every run."
    )

