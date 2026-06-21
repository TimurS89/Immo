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

from config.luxembourg import MORTGAGE
from src.lux_monitor.db import get_engine, session_scope
from src.lux_monitor.digest import buy_vs_rent_by_commune
from src.lux_monitor.finance import UPFRONT_BUY_COST_PCT, monthly_mortgage
from src.lux_monitor.models import Listing
from src.lux_monitor.snapshots import ALL_COMMUNES

st.set_page_config(page_title="LU Property Monitor", page_icon="🏠", layout="wide")


@st.cache_data(ttl=120)
def load_rows() -> pd.DataFrame:
    from src.lux_monitor.digest import days_on_market
    from src.lux_monitor.scoring import _effective_rooms

    with session_scope(get_engine()) as session:
        listings = (
            session.query(Listing)
            .filter(Listing.is_active.is_(True), Listing.duplicate_of_id.is_(None))
            .all()
        )
        records = []
        for l in listings:
            price = l.compare_price
            ppm2 = round(price / l.surface_m2) if price and l.surface_m2 else None
            records.append(
                {
                    "score": l.score_total,
                    "type": l.listing_type,
                    "commune": l.commune,
                    "rooms": _effective_rooms(l),  # pièces — matches the scorer's filter
                    "bd": l.bedrooms,
                    "m²": l.surface_m2,
                    # "€" = the sale price for a buy, blank for rentals (their
                    # monthly figure lives in €/mo, so the two columns never
                    # show the same number twice).
                    "€": l.price_eur if l.listing_type == "buy" else None,
                    # raw buy price (None for rentals) so the mortgage column can
                    # be recomputed live from the rate slider without re-querying.
                    "buy_price": l.price_eur if l.listing_type == "buy" else None,
                    # the rental monthly (rent/total); None for buys. Feeds €/mo.
                    "rent_mo": price if l.listing_type != "buy" else None,
                    "€/m²": ppm2,
                    "days": days_on_market(l),
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


# .copy() because we add live, slider-derived columns (mortgage/mo, €/mo) below;
# load_rows() is @st.cache_data, so mutating its return value in place would
# write slider-dependent data onto the shared cached object.
df = load_rows().copy()
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

# --- mortgage assumptions (live; recompute buy €/mo without re-querying) ---
st.sidebar.subheader("Mortgage (buy €/mo)")
rate = st.sidebar.slider("Interest rate %", 0.0, 7.0, float(MORTGAGE["annual_rate_pct"]), 0.1)
term = st.sidebar.slider("Term (years)", 10, 35, int(MORTGAGE["term_years"]), 1)
fin = st.sidebar.slider("Financing %", 50, 100, int(MORTGAGE["financing_pct"]), 5)

# Derive the live monthly cost (rent for rentals, mortgage for buys) + €/m².
df["mortgage/mo"] = df["buy_price"].apply(
    lambda p: monthly_mortgage(p, annual_rate_pct=rate, term_years=term, financing_pct=fin)
)
df["€/mo"] = df["mortgage/mo"].where(df["type"] == "buy", df["rent_mo"])

st.sidebar.subheader("Screen")
min_rooms = st.sidebar.number_input("Rooms ≥", min_value=0, max_value=12, value=0, step=1)
min_bd = st.sidebar.number_input("Bedrooms ≥", min_value=0, max_value=10, value=0, step=1)
min_m2 = st.sidebar.number_input("Surface m² ≥", min_value=0, max_value=600, value=0, step=10)
max_monthly = st.sidebar.number_input(
    "Monthly € ≤ (rent / est. mortgage)", min_value=0, max_value=20000, value=0, step=250,
    help="0 = no cap. For buy, compares the estimated mortgage payment.")
max_drive = st.sidebar.number_input("Drive min ≤", min_value=0, max_value=120, value=0, step=5)

sort_by = st.sidebar.selectbox(
    "Sort by", ["score", "€", "€/mo", "€/m²", "days", "first seen", "drive", "PT", "m²"])
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
if min_rooms:
    view = view[view["rooms"].fillna(0) >= min_rooms]
if min_bd:
    view = view[view["bd"].fillna(0) >= min_bd]
if min_m2:
    view = view[view["m²"].fillna(0) >= min_m2]
if max_monthly:
    view = view[view["€/mo"].fillna(1e12) <= max_monthly]
if max_drive:
    view = view[view["drive"].fillna(1e9) <= max_drive]
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
# drop internal helper columns (used only to derive €/mo live from the sliders)
table = view.drop(columns=["buy_price", "rent_mo"], errors="ignore")
st.dataframe(
    table,
    use_container_width=True,
    hide_index=True,
    column_config={
        "link": st.column_config.LinkColumn("link", display_text="open ↗"),
        "score": st.column_config.NumberColumn("score", format="%.1f"),
        "€": st.column_config.NumberColumn("buy price / —", format="%d",
            help="sale price for buy; blank for rentals (use €/mo for those)"),
        "€/mo": st.column_config.NumberColumn("€/mo (rent or mortgage)", format="%d",
            help="monthly: the rent for rentals, the estimated mortgage for a buy"),
        "€/m²": st.column_config.NumberColumn("€/m²", format="%d",
            help="buy: price per m²; rent: monthly rent per m²"),
        "mortgage/mo": st.column_config.NumberColumn("mortgage/mo", format="%d"),
        "m²": st.column_config.NumberColumn("m²", format="%d"),
        "days": st.column_config.NumberColumn("days", format="%d",
            help="days since WE first saw it (grows daily; 0 = first seen today)"),
    },
)
st.caption(
    f"Source: data/monitor.db (read-only) · refresh after a new run · a blank score "
    f"means the listing falls outside the hard filter. **€/mo** lets you compare buy "
    f"vs rent on one axis: for a buy it's the estimated mortgage payment "
    f"({rate:.1f}% over {term}y, {fin}% financing — adjust in the sidebar) — "
    f"**loan principal+interest only**, excluding notaire fees, maintenance and "
    f"impôt foncier (real ownership cost is higher)."
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

# --- buy vs rent, per commune (the core decision view) ----------------------
st.subheader("⚖️ Buy vs rent — per commune")
st.caption(
    f"Median **rent** vs median estimated **mortgage** (at {rate:.1f}% / {term}y / "
    f"{fin}% financing). “Break-even” ≈ upfront buying cost (~{int(UPFRONT_BUY_COST_PCT)}% "
    f"of price) ÷ the monthly rent-minus-mortgage saving. Directional only — ignores "
    f"equity, price growth, maintenance & tax."
)

# Aggregated by the tested digest helper (same rate/term/financing as the
# sliders), so the dashboard is a thin renderer of testable logic.
with session_scope(get_engine()) as _s:
    _bvr = buy_vs_rent_by_commune(_s, annual_rate_pct=rate, term_years=term, financing_pct=fin)
cmp_df = pd.DataFrame([
    {
        "commune": r.commune,
        "rentals": r.n_rent,
        "buys": r.n_buy,
        "median rent €/mo": r.median_rent,
        "median mortgage €/mo": r.median_mortgage,
        "Δ buy−rent €/mo": r.delta,
        "break-even yrs": r.break_even_years,
    }
    for r in _bvr
])
st.dataframe(
    cmp_df, hide_index=True, use_container_width=True,
    column_config={
        "median rent €/mo": st.column_config.NumberColumn(format="%d"),
        "median mortgage €/mo": st.column_config.NumberColumn(format="%d"),
        "Δ buy−rent €/mo": st.column_config.NumberColumn(format="%d", help="positive = buying costs more per month"),
        "break-even yrs": st.column_config.NumberColumn(format="%.1f", help="blank if buying costs more per month (no cash-flow break-even)"),
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
    default_idx = communes_for_type.index(ALL_COMMUNES) if ALL_COMMUNES in communes_for_type else 0
    # No widget key here: the option list shrinks when Type changes, and a
    # persisted stale commune value (not in the new options) would raise.
    c_sel = ccol.selectbox("Commune", communes_for_type, index=default_idx)
    metric = mcol.selectbox(
        "Metric", ["median €", "median €/m²", "count", "new", "median m²"], key="trend_metric")

    series = (
        snaps[(snaps["type"] == t_sel) & (snaps["commune"] == c_sel)]
        .sort_values("date")
        .set_index("date")
    )
    if len(series) < 2:
        st.info(f"Only {len(series)} snapshot so far for this segment — the line appears once there are ≥2 daily runs.")
    if series[metric].notna().any():
        st.line_chart(series[metric], height=320)
    else:
        st.caption(f"No '{metric}' data for this segment yet.")
    latest = series.iloc[-1]
    k1, k2, k3 = st.columns(3)
    k1.metric("Listings now", int(latest["count"]))
    if pd.notna(latest["median €"]):
        k2.metric("Median price", f"€{latest['median €']:,.0f}")
    if pd.notna(latest["median €/m²"]):
        k3.metric("Median €/m²", f"€{latest['median €/m²']:,.0f}")
    st.caption(
        "Each point is one run. Snapshots are recorded automatically every run."
    )

    # Compare price TREND across types (rent vs furnished vs buy) for a commune.
    st.markdown("**Compare price trend across types** (same commune)")
    cmp_communes = sorted(snaps["commune"].unique())
    cmp_default_idx = cmp_communes.index(ALL_COMMUNES) if ALL_COMMUNES in cmp_communes else 0
    cmp_commune = st.selectbox("Commune", cmp_communes, index=cmp_default_idx, key="cmp_commune")
    wide = (
        snaps[snaps["commune"] == cmp_commune]
        .pivot_table(index="date", columns="type", values="median €/m²", aggfunc="last")
        .sort_index()
    )
    # Buy €/m² (purchase, thousands) and rent €/m² (monthly, tens) live on wildly
    # different scales, so plotting raw values on one axis flattens the rent line.
    # Index each series to 100 at its first observed point: the chart then shows
    # RELATIVE movement (% change), which is the comparable rent-vs-buy signal.
    indexed = wide.apply(lambda s: s / s.dropna().iloc[0] * 100 if s.notna().any() else s)
    if len(wide) < 2:
        st.info("The comparison line fills in once there are ≥2 daily runs.")
    st.line_chart(indexed, height=320)
    st.caption(
        f"Median €/m² by type in **{cmp_commune}**, indexed to 100 at each series' first "
        "point — so buy and rent trends are comparable despite very different absolute "
        "scales. Rising buy vs flat rent ⇒ buying is getting relatively more expensive."
    )

# --- slow movers (days on market) -------------------------------------------
st.subheader("🐌 Long on the market (possible negotiation room)")
dom_min = st.slider("Online at least … days", 30, 180, 60, step=10)
slow = df[df["days"].notna() & (df["days"] >= dom_min)].sort_values("days", ascending=False)
if slow.empty:
    st.caption(f"No active listings have been online ≥ {dom_min} days yet "
               "(needs history to accrue — listings get their 'days' from when we first saw them).")
else:
    st.caption(f"{len(slow)} listing(s) online ≥ {dom_min} days — long-sitting adverts are "
               "more often open to a lower offer.")
    st.dataframe(
        slow[["days", "score", "type", "commune", "bd", "m²", "€", "link"]].head(50),
        hide_index=True, use_container_width=True,
        column_config={
            "link": st.column_config.LinkColumn("link", display_text="open ↗"),
            "€": st.column_config.NumberColumn("€", format="%d"),
            "m²": st.column_config.NumberColumn("m²", format="%d"),
            "score": st.column_config.NumberColumn("score", format="%.1f"),
        },
    )

