"""
streamlit_app.py
----------------
Interactive live dashboard for the Skroutz price-tracking pipeline.

Queries PostgreSQL directly on every interaction — no pre-generated JSON.
Results are cached for 1 hour to avoid hammering the DB on every widget change.

Run:  streamlit run streamlit_app.py
URL:  http://localhost:8501

Requires:  TELEGRAM_BOT_TOKEN / GMAIL settings in .env are optional;
           DB_* variables in .env are required (same as the rest of the pipeline).
"""

import datetime
import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import text

from db import get_engine

load_dotenv()

BASE       = Path(__file__).parent
CHARTS_DIR = BASE / "charts"
WATCHLIST  = BASE / "watchlist.json"

st.set_page_config(
    page_title  = "Skroutz Price Tracker",
    page_icon   = "📊",
    layout      = "wide",
    initial_sidebar_state = "collapsed",
)

st.markdown("""
<style>
#MainMenu { visibility: hidden; }
footer    { visibility: hidden; }

[data-testid="stMetricValue"] {
    font-size: 1.85rem !important;
    font-weight: 700 !important;
}
[data-testid="stMetricLabel"] {
    font-size: 0.82rem !important;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    opacity: 0.75;
}
[data-testid="stExpander"] details {
    border: 1px solid rgba(128,128,128,0.18) !important;
    border-radius: 10px !important;
}
button[data-baseweb="tab"] {
    font-size: 0.93rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.01em;
}
[data-testid="stDownloadButton"] > button {
    font-size: 0.82rem !important;
    padding: 0.2rem 0.75rem !important;
}
</style>
""", unsafe_allow_html=True)

# ── Shared DB helpers ──────────────────────────────────────────────────────────

@st.cache_data(ttl=3600)
def _q(sql: str, **params) -> pd.DataFrame:
    """Run a SQL query and return a DataFrame, cached for 1 hour."""
    with get_engine().connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or None)


@st.cache_data(ttl=3600)
def _scalar(sql: str) -> object:
    with get_engine().connect() as conn:
        return conn.execute(text(sql)).scalar()


def _fmt_eur(v) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"€{v:,.0f}"


CATEGORIES = ["phone", "laptop", "smartwatch", "tablet"]
CAT_LABEL  = {"phone": "📱 Phones", "laptop": "💻 Laptops",
               "smartwatch": "⌚ Smartwatches", "tablet": "📟 Tablets"}

# ── Sidebar ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("Skroutz Tracker")
    st.caption("Live price intelligence dashboard")
    st.divider()
    if st.button("🔄 Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("Data is cached for 1 hour.\nClick Refresh to force a reload.")

# ── Top-level stats (header) ───────────────────────────────────────────────────

def _header():
    total_products  = _scalar("SELECT COUNT(*) FROM products")
    total_snapshots = _scalar("SELECT COUNT(*) FROM price_snapshots")
    last_updated    = _scalar("SELECT MAX(date) FROM price_snapshots")
    total_drops     = _scalar(
        "SELECT COUNT(*) FROM vw_biggest_drops WHERE drop_date = CURRENT_DATE"
    )
    drops_yesterday = _scalar(
        "SELECT COUNT(*) FROM vw_biggest_drops WHERE drop_date = CURRENT_DATE - 1"
    )

    drops_today = total_drops or 0
    drops_delta = drops_today - (drops_yesterday or 0)

    today = datetime.date.today()
    if last_updated:
        last_date  = last_updated.date() if hasattr(last_updated, "date") else last_updated
        last_str   = "Today ✓" if last_date == today else str(last_date)
        late_delta = f"⚠️ {(today - last_date).days}d behind" if last_date < today else None
    else:
        last_str   = "—"
        late_delta = None

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Products tracked",  f"{total_products:,}")
    c2.metric("Price snapshots",   f"{total_snapshots:,}")
    c3.metric("Price drops today", str(drops_today), f"{drops_delta:+d} vs yesterday")
    c4.metric("Last updated",      last_str, late_delta, delta_color="off")


# ── Tab 1 — Overview ───────────────────────────────────────────────────────────

def tab_overview():
    st.subheader("Category snapshot")
    with st.spinner("Loading category stats…"):
        cat_df = _q("""
            SELECT p.category,
                   COUNT(DISTINCT p.id)       AS products,
                   ROUND(AVG(s.price_eur), 0) AS avg_price,
                   ROUND(MIN(s.price_eur), 0) AS min_price,
                   ROUND(MAX(s.price_eur), 0) AS max_price
            FROM products p
            JOIN price_snapshots s ON s.product_id = p.id
            WHERE s.date = (SELECT MAX(date) FROM price_snapshots)
            GROUP BY p.category ORDER BY p.category
        """)

    if not cat_df.empty:
        cols = st.columns(len(cat_df))
        for col, (_, row) in zip(cols, cat_df.iterrows()):
            with col:
                with st.container(border=True):
                    st.markdown(f"**{CAT_LABEL.get(row.category, row.category)}**")
                    r1, r2 = st.columns(2)
                    r1.metric("Products", f"{int(row.products):,}")
                    r2.metric("Avg price", _fmt_eur(row.avg_price))
                    r3, r4 = st.columns(2)
                    r3.metric("Min", _fmt_eur(row.min_price))
                    r4.metric("Max", _fmt_eur(row.max_price))

    if not cat_df.empty:
        cat_plot = cat_df.copy()
        cat_plot["Category"] = cat_plot["category"].map(lambda c: CAT_LABEL.get(c, c))
        melted = cat_plot.melt(
            id_vars="Category",
            value_vars=["min_price", "avg_price", "max_price"],
            var_name="metric", value_name="price",
        )
        melted["metric"] = melted["metric"].map(
            {"min_price": "Min", "avg_price": "Avg", "max_price": "Max"}
        )
        fig = px.bar(
            melted, x="Category", y="price", color="metric",
            barmode="group",
            labels={"price": "Price (€)", "Category": "", "metric": ""},
            template="plotly_dark",
            color_discrete_map={"Min": "#42a5f5", "Avg": "#66bb6a", "Max": "#ef5350"},
        )
        fig.update_layout(
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            margin=dict(l=0, r=0, t=30, b=0),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            height=280,
        )
        st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.subheader("Brand price trends")
    cat_filter = st.selectbox("Category", CATEGORIES,
                               format_func=lambda c: CAT_LABEL[c], key="ov_cat")

    chart_path = CHARTS_DIR / f"price_trend_{cat_filter}.png"
    if chart_path.exists():
        st.image(str(chart_path), use_container_width=True)
    else:
        lookback = st.slider("Days back", 30, 180, 90, step=30, key="ov_days")
        with st.spinner("Loading trend data…"):
            trend_df = _q("""
                WITH ranked AS (
                    SELECT brand,
                           ROW_NUMBER() OVER (ORDER BY product_count DESC) AS rn
                    FROM vw_brand_summary
                    WHERE category = :cat AND brand IS NOT NULL
                )
                SELECT bt.brand, bt.date, bt.avg_price
                FROM vw_brand_price_trend bt
                JOIN ranked r ON r.brand = bt.brand
                WHERE bt.category = :cat
                  AND bt.date >= CURRENT_DATE - :days
                  AND r.rn <= 6
                ORDER BY bt.brand, bt.date
            """, cat=cat_filter, days=lookback)

        if trend_df.empty:
            st.info("No trend data yet — run the pipeline first.")
        else:
            fig = px.line(
                trend_df, x="date", y="avg_price", color="brand",
                labels={"avg_price": "Avg Price (€)", "date": "Date", "brand": "Brand"},
                template="plotly_dark",
            )
            fig.update_layout(
                hovermode="x unified",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(l=0, r=0, t=30, b=0),
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                height=320,
            )
            fig.update_traces(line_width=2.5)
            st.plotly_chart(fig, use_container_width=True)


# ── Tab 2 — Price Drops ────────────────────────────────────────────────────────

def tab_drops():
    st.subheader("Price drops")

    col1, col2 = st.columns([2, 1])
    with col1:
        days_back = st.slider("Show drops from last N days", 1, 30, 7, key="dr_days")
    with col2:
        cat_sel = st.multiselect("Category", CATEGORIES,
                                 format_func=lambda c: CAT_LABEL[c], key="dr_cat")

    cat_filter_sql = ""
    params: dict = {"days": days_back}
    if cat_sel:
        placeholders = ", ".join(f":cat{i}" for i in range(len(cat_sel)))
        cat_filter_sql = f"AND category IN ({placeholders})"
        for i, c in enumerate(cat_sel):
            params[f"cat{i}"] = c

    with st.spinner("Loading drops…"):
        drops_df = _q(f"""
            SELECT brand, model, category,
                   ROUND(prev_price, 2)        AS "Was €",
                   ROUND(new_price,  2)        AS "Now €",
                   ABS(ROUND(drop_eur, 2))     AS "Saved €",
                   ABS(ROUND(drop_pct,   1))   AS "Drop %",
                   drop_date,
                   skroutz_link
            FROM vw_biggest_drops
            WHERE drop_date >= CURRENT_DATE - :days
            {cat_filter_sql}
            ORDER BY "Saved €" DESC
            LIMIT 100
        """, **params)

    if drops_df.empty:
        st.info("No price drops in the selected range.")
    else:
        hdr_l, hdr_r = st.columns([5, 1])
        hdr_l.caption(f"{len(drops_df)} drops found")
        with hdr_r:
            st.download_button(
                "⬇ Export CSV",
                data=drops_df.to_csv(index=False).encode("utf-8"),
                file_name="price_drops.csv",
                mime="text/csv",
                use_container_width=True,
            )
        st.dataframe(
            drops_df,
            column_config={
                "Was €":        st.column_config.NumberColumn("Was €",   format="€%.2f"),
                "Now €":        st.column_config.NumberColumn("Now €",   format="€%.2f"),
                "Saved €":      st.column_config.NumberColumn("Saved €", format="€%.2f"),
                "Drop %":       st.column_config.NumberColumn("Drop %",  format="%.1f%%"),
                "drop_date":    st.column_config.DateColumn("Date"),
                "skroutz_link": st.column_config.LinkColumn("Link", display_text="🔗 View"),
            },
            use_container_width=True,
            hide_index=True,
        )
        if drops_df["drop_date"].nunique() > 1:
            st.caption("Drop activity")
            daily = (
                drops_df.groupby("drop_date", as_index=False)
                .agg(Drops=("Saved €", "count"))
            )
            fig = px.bar(
                daily, x="drop_date", y="Drops",
                labels={"drop_date": "Date", "Drops": "Drops"},
                template="plotly_dark",
                color_discrete_sequence=["#42a5f5"],
            )
            fig.update_layout(
                margin=dict(l=0, r=0, t=10, b=0),
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                height=160,
            )
            st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.subheader("Hot deals (drop + review surge)")
    with st.spinner("Loading hot deals…"):
        hot_df = _q("""
            SELECT brand, model, category,
                   ROUND(price_prev,    2) AS "Was €",
                   ROUND(price_latest,  2) AS "Now €",
                   ROUND(price_chg_pct, 1) AS "Δ%",
                   new_reviews             AS "New reviews",
                   ROUND(hot_score,     1) AS "Score",
                   skroutz_link
            FROM vw_hot_deals
            LIMIT 20
        """)
    if hot_df.empty:
        st.info("No hot deals data yet.")
    else:
        st.dataframe(
            hot_df,
            column_config={
                "Was €":        st.column_config.NumberColumn("Was €", format="€%.2f"),
                "Now €":        st.column_config.NumberColumn("Now €", format="€%.2f"),
                "Δ%":           st.column_config.NumberColumn("Δ%",   format="%.1f%%"),
                "Score":        st.column_config.NumberColumn("Score", format="%.1f 🔥"),
                "skroutz_link": st.column_config.LinkColumn("Link",   display_text="🔗 View"),
            },
            use_container_width=True,
            hide_index=True,
        )


# ── Tab 3 — Products ───────────────────────────────────────────────────────────

def tab_products():
    st.subheader("Product search")

    col1, col2, col3 = st.columns(3)
    with col1:
        cat_f = st.multiselect("Category", CATEGORIES,
                               format_func=lambda c: CAT_LABEL[c], key="pr_cat")
    with col2:
        price_min, price_max = st.slider(
            "Price range (€)", 0, 5000, (0, 5000), step=50, key="pr_price"
        )
    with col3:
        brand_search = st.text_input("Brand contains", key="pr_brand").strip()

    cat_sql   = ""
    brand_sql = ""
    params: dict = {"pmin": price_min, "pmax": price_max}
    if cat_f:
        ph = ", ".join(f":c{i}" for i in range(len(cat_f)))
        cat_sql = f"AND lp.category IN ({ph})"
        for i, c in enumerate(cat_f):
            params[f"c{i}"] = c
    if brand_search:
        brand_sql = "AND LOWER(lp.brand) LIKE :brand"
        params["brand"] = f"%{brand_search.lower()}%"

    with st.spinner("Searching products…"):
        prod_df = _q(f"""
            SELECT lp.category, lp.brand, lp.model,
                   ROUND(lp.price_eur, 2)               AS "Price €",
                   lp.rating                            AS "Rating",
                   lp.reviews                           AS "Reviews",
                   COALESCE(ROUND(pv.cv_pct, 1), 0)    AS "Volatility %",
                   lp.skroutz_link
            FROM vw_latest_prices lp
            LEFT JOIN vw_price_volatility pv ON pv.product_id = lp.id
            WHERE lp.price_eur BETWEEN :pmin AND :pmax
            {cat_sql}
            {brand_sql}
            ORDER BY lp.reviews DESC NULLS LAST
            LIMIT 500
        """, **params)

    if prod_df.empty:
        st.info("No products match the selected filters.")
    else:
        hdr_l, hdr_r = st.columns([5, 1])
        hdr_l.write(f"**{len(prod_df):,}** products matching filters")
        with hdr_r:
            st.download_button(
                "⬇ Export CSV",
                data=prod_df.to_csv(index=False).encode("utf-8"),
                file_name="products.csv",
                mime="text/csv",
                use_container_width=True,
            )
        st.dataframe(
            prod_df,
            column_config={
                "Price €":      st.column_config.NumberColumn("Price €",  format="€%.2f"),
                "Rating":       st.column_config.NumberColumn("Rating",   format="⭐ %.1f"),
                "Reviews":      st.column_config.NumberColumn("Reviews",  format="%.0f"),
                "Volatility %": st.column_config.ProgressColumn(
                                    "Volatility %", min_value=0, max_value=50, format="%.1f%%"
                                ),
                "skroutz_link": st.column_config.LinkColumn("Link", display_text="🔗 View"),
            },
            use_container_width=True,
            hide_index=True,
        )


# ── Tab 4 — Watchlist ──────────────────────────────────────────────────────────

def tab_watchlist():
    st.subheader("Watchlist")

    if not WATCHLIST.exists():
        st.info(
            "No watchlist.json found. Create one in the project root:\n\n"
            "```json\n"
            '[\n  {"url": "https://www.skroutz.gr/s/...", '
            '"label": "iPhone 17 Pro Max", "threshold_eur": 1400}\n]\n'
            "```"
        )
        return

    try:
        items = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    except Exception as e:
        st.error(f"Could not parse watchlist.json: {e}")
        return

    if not items:
        st.info("watchlist.json is empty.")
        return

    for item in items:
        url       = item.get("url", "").strip()
        label     = item.get("label", url)
        threshold = float(item.get("threshold_eur", 0))

        try:
            row_df = _q(
                "SELECT price_eur, skroutz_link FROM vw_latest_prices "
                "WHERE skroutz_link = :url OR skroutz_link LIKE :prefix",
                url=url, prefix=url.split("?")[0] + "%",
            )
        except Exception:
            row_df = pd.DataFrame()

        raw_price = row_df.iloc[0]["price_eur"] if not row_df.empty else None
        price     = float(raw_price) if raw_price is not None else None
        hit       = price is not None and price <= threshold

        with st.expander(f"{'🎯 ' if hit else ''}{label}", expanded=True):
            if row_df.empty or price is None:
                st.warning("Not found in DB — URL may not match.")
                continue

            delta     = price - threshold
            delta_str = f"{delta:+.2f} €"

            c1, c2, c3 = st.columns(3)
            c1.metric("Current price", f"€{price:,.2f}", delta_str, delta_color="inverse")
            c2.metric("Your target",   f"€{threshold:,.2f}")
            c3.metric("Status", "✅ Buy now!" if hit else "⏳ Waiting")

            try:
                hist_df = _q("""
                    SELECT s.date, ROUND(s.price_eur, 2) AS price
                    FROM price_snapshots s
                    JOIN products p ON p.id = s.product_id
                    WHERE p.skroutz_link = :url OR p.skroutz_link LIKE :prefix
                    ORDER BY s.date
                """, url=url, prefix=url.split("?")[0] + "%")
                if not hist_df.empty and len(hist_df) > 1:
                    fig = px.line(
                        hist_df, x="date", y="price",
                        labels={"price": "Price (€)", "date": ""},
                        template="plotly_dark",
                    )
                    fig.add_hline(
                        y=threshold, line_dash="dash", line_color="#ef5350",
                        annotation_text=f"Target €{threshold:,.0f}",
                        annotation_position="bottom right",
                    )
                    fig.update_layout(
                        height=200,
                        margin=dict(l=0, r=0, t=10, b=0),
                        showlegend=False,
                        plot_bgcolor="rgba(0,0,0,0)",
                        paper_bgcolor="rgba(0,0,0,0)",
                    )
                    fig.update_traces(line_width=2, line_color="#42a5f5")
                    st.plotly_chart(fig, use_container_width=True)
            except Exception:
                pass

            st.link_button("🔗 View on Skroutz", url)


# ── Tab 5 — Analytics ─────────────────────────────────────────────────────────

def tab_analytics():
    st.subheader("Brand analytics")

    cat_a = st.selectbox("Category", CATEGORIES,
                         format_func=lambda c: CAT_LABEL[c], key="an_cat")

    # --- Brand discount frequency ---
    try:
        with st.spinner("Loading discount frequency…"):
            disc_df = _q("""
                SELECT brand, discount_days, tracked_days,
                       ROUND(discount_freq_pct, 1) AS "On-sale %"
                FROM vw_brand_discount_freq
                WHERE category = :cat AND brand IS NOT NULL
                ORDER BY discount_freq_pct DESC NULLS LAST
                LIMIT 12
            """, cat=cat_a)
        if not disc_df.empty:
            st.markdown("**Discount frequency** — how often each brand has a price drop (last 90 days)")
            fig = px.bar(
                disc_df.sort_values("On-sale %"),
                x="On-sale %", y="brand",
                orientation="h",
                labels={"On-sale %": "On-sale days (%)", "brand": ""},
                template="plotly_dark",
                color_discrete_sequence=["#42a5f5"],
            )
            fig.update_traces(texttemplate="%{x:.1f}%", textposition="outside")
            fig.update_layout(
                margin=dict(l=0, r=50, t=10, b=0),
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                height=max(220, len(disc_df) * 32),
                xaxis=dict(range=[0, min(100, max(1.0, disc_df["On-sale %"].max()) * 1.3)]),
            )
            st.plotly_chart(fig, use_container_width=True)
    except Exception:
        st.info("Discount frequency view not yet available (run analytics.sql v2).")

    st.divider()
    st.subheader("Disappeared products (last 30 days)")
    with st.spinner("Loading disappeared products…"):
        dis_df = _q("""
            SELECT category, brand, model,
                   last_seen, days_since_last_seen AS "Days gone",
                   skroutz_link
            FROM vw_disappeared
            WHERE days_since_last_seen <= 30
            ORDER BY last_seen DESC LIMIT 50
        """)
    if dis_df.empty:
        st.success("No disappeared products in the last 30 days.")
    else:
        st.dataframe(
            dis_df,
            column_config={
                "last_seen":    st.column_config.DateColumn("Last seen"),
                "Days gone":    st.column_config.NumberColumn("Days gone", format="%.0f days"),
                "skroutz_link": st.column_config.LinkColumn("Link", display_text="🔗 View"),
            },
            use_container_width=True,
            hide_index=True,
        )

    st.divider()
    st.subheader("Category price index (last 90 days)")
    try:
        with st.spinner("Loading market index…"):
            idx_df = _q("""
                SELECT category, date, avg_price AS "Avg €"
                FROM vw_daily_market_index
                WHERE date >= CURRENT_DATE - 90
                ORDER BY category, date
            """)
        if not idx_df.empty:
            idx_df["Category"] = idx_df["category"].map(
                lambda c: CAT_LABEL.get(c, c)
            )
            fig = px.line(
                idx_df, x="date", y="Avg €", color="Category",
                labels={"Avg €": "Avg Price (€)", "date": "Date"},
                template="plotly_dark",
            )
            fig.update_layout(
                hovermode="x unified",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(l=0, r=0, t=30, b=0),
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
            )
            fig.update_layout(height=300)
            fig.update_traces(line_width=2.5)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No market index data yet — run the pipeline first.")
    except Exception:
        st.info("Market index view not yet available (run analytics.sql).")

    st.divider()
    st.subheader("Price trend direction")
    st.caption("Compares each product's 7-day avg vs 30-day avg to classify momentum.")
    try:
        with st.spinner("Loading trend direction…"):
            td_df = _q("""
                SELECT lp.brand, lp.model,
                       ROUND(lp.price_eur, 2) AS "Price €",
                       ROUND(td.avg_7d, 2)    AS "7d Avg €",
                       ROUND(td.avg_30d, 2)   AS "30d Avg €",
                       td.trend               AS "Trend"
                FROM vw_price_trend_direction td
                JOIN vw_latest_prices lp ON lp.id = td.product_id
                WHERE lp.category = :cat
                ORDER BY td.trend, lp.price_eur DESC
                LIMIT 100
            """, cat=cat_a)
        if not td_df.empty:
            st.dataframe(
                td_df,
                column_config={
                    "Price €":  st.column_config.NumberColumn("Price €",  format="€%.2f"),
                    "7d Avg €": st.column_config.NumberColumn("7d Avg €", format="€%.2f"),
                    "30d Avg €":st.column_config.NumberColumn("30d Avg €",format="€%.2f"),
                },
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No trend data yet — needs 30+ days of scraping.")
    except Exception:
        st.info("Trend direction view not yet available (run analytics.sql).")

    st.divider()
    st.subheader("Near all-time low")
    st.caption("Products currently within 10% of their historical price floor (min. 10 snapshots).")
    try:
        with st.spinner("Loading near-ATL products…"):
            atl_df = _q("""
                SELECT brand, model,
                       ROUND(current_price, 2) AS "Price €",
                       ROUND(all_time_low,  2) AS "ATL €",
                       pct_above_atl           AS "% Above ATL",
                       snapshot_count          AS "Snapshots",
                       skroutz_link
                FROM vw_near_atl
                WHERE category = :cat
                  AND pct_above_atl <= 10
                ORDER BY pct_above_atl ASC
                LIMIT 30
            """, cat=cat_a)
        if not atl_df.empty:
            st.dataframe(
                atl_df,
                column_config={
                    "Price €": st.column_config.NumberColumn("Price €", format="€%.2f"),
                    "ATL €":   st.column_config.NumberColumn("ATL €",   format="€%.2f"),
                    "% Above ATL": st.column_config.ProgressColumn(
                                       "% Above ATL", min_value=0, max_value=10, format="%.1f%%"
                                   ),
                    "skroutz_link": st.column_config.LinkColumn("Link", display_text="🔗 View"),
                },
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No products within 10% of their ATL right now.")
    except Exception:
        st.info("Near ATL view not yet available (run analytics.sql).")


# ── Main ───────────────────────────────────────────────────────────────────────

st.title("📊 Skroutz Price Tracker")
st.caption(f"Live data from PostgreSQL · Last loaded: {datetime.datetime.now():%Y-%m-%d %H:%M}")

try:
    _header()
except Exception as e:
    st.error(f"Database connection failed: {e}")
    st.stop()

tabs = st.tabs(["🏠 Overview", "📉 Price Drops", "🔍 Products", "🎯 Watchlist", "📊 Analytics"])

with tabs[0]:
    tab_overview()
with tabs[1]:
    tab_drops()
with tabs[2]:
    tab_products()
with tabs[3]:
    tab_watchlist()
with tabs[4]:
    tab_analytics()
