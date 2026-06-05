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
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

*, body, [class*="st-"] {
    font-family: 'Inter', -apple-system, sans-serif !important;
}

#MainMenu { visibility: hidden; }
footer    { visibility: hidden; }

/* Metric cards */
[data-testid="stMetric"] {
    padding: 0.9rem 1rem !important;
    border-radius: 12px !important;
    border: 1px solid rgba(255,255,255,0.08) !important;
    background: rgba(26,29,39,0.6) !important;
}
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

/* Active tab underline */
button[data-baseweb="tab"] {
    font-size: 0.93rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.01em;
}
button[data-baseweb="tab"][aria-selected="true"] {
    border-bottom: 3px solid #4f8ef7 !important;
    color: #4f8ef7 !important;
}

/* Dataframe headers */
[data-testid="stDataFrame"] th,
[data-testid="stDataFrame"] [role="columnheader"] {
    font-weight: 600 !important;
    letter-spacing: 0.03em !important;
}

/* Expanders — left accent bar + tighter border */
[data-testid="stExpander"] details {
    border: 1px solid rgba(128,128,128,0.18) !important;
    border-left: 3px solid #4f8ef7 !important;
    border-radius: 10px !important;
    padding-left: 2px !important;
}

/* Sidebar darker bg */
[data-testid="stSidebar"] {
    background: rgba(10,12,18,0.97) !important;
}

/* Download buttons as pill */
[data-testid="stDownloadButton"] > button {
    border-radius: 20px !important;
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
CAT_COLORS = {"phone": "#3b82f6", "laptop": "#a78bfa", "smartwatch": "#22c55e", "tablet": "#f59e0b"}

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
    # 4-metric summary row
    try:
        new_this_week  = _scalar("SELECT COUNT(*) FROM products WHERE first_seen >= CURRENT_DATE - 7")
        snaps_today    = _scalar("SELECT COUNT(*) FROM price_snapshots WHERE date = CURRENT_DATE")
        biggest_drop   = _scalar("SELECT MAX(ABS(drop_eur)) FROM vw_biggest_drops WHERE drop_date = CURRENT_DATE")
        near_atl_count = _scalar("SELECT COUNT(*) FROM vw_near_atl WHERE pct_above_atl <= 10")
        mc1, mc2, mc3, mc4 = st.columns(4)
        mc1.metric("New arrivals this week",  f"{new_this_week or 0:,}")
        mc2.metric("Snapshots today",         f"{snaps_today or 0:,}")
        mc3.metric("Biggest drop today",      f"€{biggest_drop or 0:.0f}")
        mc4.metric("Near all-time low",       f"{near_atl_count or 0:,}")
    except Exception:
        pass

    st.markdown("### Category snapshot")
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
        cols = st.columns(4)
        for i, cat in enumerate(["phone", "laptop", "smartwatch", "tablet"]):
            row = cat_df[cat_df["category"] == cat]
            if row.empty:
                continue
            row = row.iloc[0]
            color = CAT_COLORS.get(cat, "#4f8ef7")
            with cols[i]:
                st.markdown(
                    f'<div style="border-left:4px solid {color};padding-left:10px;'
                    f'margin-bottom:6px;font-weight:600;font-size:0.95rem">'
                    f'{CAT_LABEL.get(cat, cat)}</div>',
                    unsafe_allow_html=True,
                )
                with st.container(border=True):
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
    st.markdown("### Brand price trends")
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
                height=380,
            )
            fig.update_traces(mode="lines+markers", line_width=2.5)
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
        if len(drops_df) > 3:
            top10 = drops_df.head(10).copy()
            top10["label"] = (
                top10["brand"].fillna("") + " " + top10["model"].fillna("")
            ).str.strip().str.slice(0, 40)
            fig_bar = px.bar(
                top10.sort_values("Saved €"),
                x="Saved €", y="label",
                orientation="h",
                color="category",
                labels={"label": "", "Saved €": "Saved (€)"},
                template="plotly_dark",
                color_discrete_map={
                    "phone": "#3b82f6", "laptop": "#a78bfa",
                    "smartwatch": "#22c55e", "tablet": "#f59e0b",
                },
                height=300,
            )
            fig_bar.update_layout(
                margin=dict(l=0, r=10, t=10, b=0),
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            st.plotly_chart(fig_bar, use_container_width=True)

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
                color_discrete_sequence=["#4f8ef7", "#a78bfa", "#22c55e", "#f59e0b"],
                text_auto=True,
            )
            fig.update_layout(
                margin=dict(l=0, r=0, t=10, b=0),
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                height=200,
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

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        cat_f = st.multiselect("Category", CATEGORIES,
                               format_func=lambda c: CAT_LABEL[c], key="pr_cat")
    with col2:
        price_min, price_max = st.slider(
            "Price range (€)", 0, 5000, (0, 5000), step=50, key="pr_price"
        )
    with col3:
        brand_search = st.text_input("Brand contains", key="pr_brand").strip()
    with col4:
        sort_by = st.selectbox(
            "Sort by",
            ["Reviews (default)", "Price ↑", "Price ↓", "Rating", "Volatility"],
            key="pr_sort",
        )

    sort_map = {
        "Reviews (default)": "lp.reviews DESC NULLS LAST",
        "Price ↑":           "lp.price_eur ASC",
        "Price ↓":           "lp.price_eur DESC",
        "Rating":            "lp.rating DESC NULLS LAST",
        "Volatility":        "pv.cv_pct DESC NULLS LAST",
    }
    order_by = sort_map.get(sort_by, "lp.reviews DESC NULLS LAST")

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
                   ROUND(na.pct_above_atl, 1)           AS "% Above ATL",
                   lp.skroutz_link
            FROM vw_latest_prices lp
            LEFT JOIN vw_price_volatility pv ON pv.product_id = lp.id
            LEFT JOIN vw_near_atl na ON na.product_id = lp.id
            WHERE lp.price_eur BETWEEN :pmin AND :pmax
            {cat_sql}
            {brand_sql}
            ORDER BY {order_by}
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
                "% Above ATL":  st.column_config.ProgressColumn(
                                    "% Above ATL", min_value=0, max_value=20, format="%.1f%%"
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

    enriched = []
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
        diff      = (price - threshold) if price is not None else float("inf")
        enriched.append({"url": url, "label": label, "threshold": threshold,
                         "price": price, "hit": hit, "diff": diff, "row_df": row_df})

    enriched.sort(key=lambda x: (not x["hit"], x["diff"]))

    for e in enriched:
        url, label, threshold = e["url"], e["label"], e["threshold"]
        price, hit, row_df    = e["price"], e["hit"], e["row_df"]

        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
            with c1:
                st.markdown(f"**{label}**")
                st.link_button("🔗 View on Skroutz", url)
            if price is not None:
                delta     = price - threshold
                delta_str = f"{delta:+.2f} €"
                c2.metric("Current price", f"€{price:,.2f}", delta_str, delta_color="inverse")
            else:
                c2.metric("Current price", "—")
            c3.metric("Target", f"€{threshold:,.2f}")
            c4.metric("Status", "✅ Buy now!" if hit else "⏳ Waiting")

            if price is not None and threshold > 0:
                progress_val = min(1.0, threshold / price)
                st.progress(progress_val)

            if row_df.empty or price is None:
                st.warning("Not found in DB — URL may not match.")
                continue

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
            median_val = float(disc_df["On-sale %"].median())
            fig = px.bar(
                disc_df.sort_values("On-sale %"),
                x="On-sale %", y="brand",
                orientation="h",
                labels={"On-sale %": "On-sale days (%)", "brand": ""},
                template="plotly_dark",
                color_discrete_sequence=["#42a5f5"],
            )
            fig.update_traces(texttemplate="%{x:.1f}%", textposition="outside")
            fig.add_vline(
                x=median_val,
                line_dash="dot",
                line_color="#64748b",
                annotation_text=f"Median {median_val:.1f}%",
                annotation_position="top right",
                annotation_font_color="#64748b",
            )
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
                height=300,
            )
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
                SELECT brand, model, category,
                       ROUND(current_price, 2) AS "Price €",
                       ROUND(all_time_low,  2) AS "ATL €",
                       ROUND(pct_above_atl, 1) AS "% Above ATL",
                       snapshot_count          AS "Snapshots",
                       skroutz_link
                FROM vw_near_atl
                WHERE category = :cat
                  AND pct_above_atl <= 10
                ORDER BY pct_above_atl ASC
                LIMIT 30
            """, cat=cat_a)
        if not atl_df.empty:
            if len(atl_df) > 2:
                fig_sc = px.scatter(
                    atl_df,
                    x="% Above ATL",
                    y="brand",
                    size="Snapshots",
                    color="category",
                    labels={"brand": "", "% Above ATL": "% Above ATL"},
                    template="plotly_dark",
                    color_discrete_map={
                        "phone": "#3b82f6", "laptop": "#a78bfa",
                        "smartwatch": "#22c55e", "tablet": "#f59e0b",
                    },
                    height=320,
                )
                fig_sc.update_layout(
                    margin=dict(l=0, r=0, t=10, b=0),
                    plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)",
                    showlegend=False,
                )
                st.plotly_chart(fig_sc, use_container_width=True)

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

    st.divider()
    st.subheader("Price volatility leaderboard")
    st.caption("Top 20 most volatile products — 30-day coefficient of variation.")
    try:
        with st.spinner("Loading volatility data…"):
            vol_df = _q("""
                SELECT lp.brand, lp.model, lp.category,
                       ROUND(pv.cv_pct, 1)    AS "Volatility %",
                       ROUND(lp.price_eur, 2) AS "Price €",
                       lp.reviews             AS "Reviews"
                FROM vw_price_volatility pv
                JOIN vw_latest_prices lp ON lp.id = pv.product_id
                ORDER BY pv.cv_pct DESC NULLS LAST
                LIMIT 20
            """)
        if not vol_df.empty:
            vol_df["label"] = (
                vol_df["brand"].fillna("") + " " + vol_df["model"].fillna("")
            ).str.strip().str.slice(0, 40)
            fig_vol = px.bar(
                vol_df.sort_values("Volatility %"),
                x="Volatility %", y="label",
                orientation="h",
                color="Volatility %",
                color_continuous_scale=[[0, "#22c55e"], [0.5, "#f59e0b"], [1, "#ef4444"]],
                labels={"label": "", "Volatility %": "CV %"},
                template="plotly_dark",
                height=max(260, len(vol_df) * 28),
            )
            fig_vol.update_layout(
                margin=dict(l=0, r=10, t=10, b=0),
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                showlegend=False,
                coloraxis_showscale=False,
            )
            st.plotly_chart(fig_vol, use_container_width=True)
        else:
            st.info("No volatility data yet — needs 30+ days of scraping.")
    except Exception:
        st.info("Volatility view not yet available (run analytics.sql).")


# ── Main ───────────────────────────────────────────────────────────────────────

st.markdown(
    "<h1 style='background:linear-gradient(135deg,#4f8ef7,#a78bfa);"
    "-webkit-background-clip:text;-webkit-text-fill-color:transparent;"
    "font-size:2.2rem;font-weight:700;margin-bottom:0'>📊 Skroutz Price Tracker</h1>",
    unsafe_allow_html=True,
)
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
