"""
generate_dashboard.py
---------------------
Generates a self-contained dashboard.html from the PostgreSQL database.

Embeds all data as JSON so the file works offline with no server.
Run standalone:  python generate_dashboard.py
Auto-called by run_pipeline.py after each successful scrape.

Output: dashboard/dashboard_<YYYY-MM-DD>.html  +  dashboard/dashboard_latest.html
"""
from core.paths import ROOT, CHARTS_DIR as _CHARTS_DIR, DASHBOARD_DIR, TEMPLATES_DIR

import os
import sys
import json
import logging
import datetime

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

import pandas as pd

import reporting.queries as queries
from reporting.drops import BIG_DROP_LABEL_EN, BIG_DROP_PCT
import reporting.dashboard_payload as payload
import reporting.buy_or_wait as buy_or_wait
from core.db import get_engine

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

BASE       = ROOT
CHARTS_DIR = _CHARTS_DIR
OUT_DIR    = DASHBOARD_DIR
OUT_DIR.mkdir(exist_ok=True)

# ── Display limits and KPI thresholds ──────────────────────────────────────────
# Lists are truncated for display; KPIs come from COUNT(*) queries instead.
DROPS_TODAY_LIMIT   = 25
DROPS_WEEK_LIMIT    = 30
NEW_PRODUCTS_LIMIT  = 50
DISAPPEARED_LIMIT   = 50
PRODUCTS_LIMIT      = 3000   # product search / price tiers / price-vs-rating sample
HOT_DEALS_LIMIT     = 20
SPARK_POINTS        = 8      # points per product sparkline (last 30 days)

# A drop of SUSPECT_DROP_PCT percent or more is shown and counted, but flagged
# "μεγάλη πτώση, έλεγξε" (check it). Hot Deals, which still compares only the
# last two scrapes, keeps excluding them. One constant: reporting/drops.py.
SUSPECT_DROP_PCT       = BIG_DROP_PCT
# Hot Deals must fall at least this much, in percent and in euros.
HOT_DEAL_MIN_DROP_PCT  = 3.0
HOT_DEAL_MIN_DROP_EUR  = 5.0


# Words the title parser takes for a brand when a listing has none
# ("Smartwatch ...", "Έξυπνο ρολόι ..."). Hidden on the dashboard only; the DB keeps them.
FAKE_BRANDS = frozenset({"Smartwatch", "Έξυπνο"})


def is_real_brand(brand):
    return bool(brand) and brand not in FAKE_BRANDS


def display_brand(brand):
    """Brand as shown on the dashboard: empty for missing or fake brands."""
    return brand if is_real_brand(brand) else ""


def is_suspect_drop(drop_pct, threshold=SUSPECT_DROP_PCT):
    """True when a drop is threshold percent or larger. drop_pct is signed
    (negative for a drop) as in vw_drops_7d; None is never suspect."""
    if drop_pct is None:
        return False
    return abs(float(drop_pct)) >= threshold


def is_hot_deal(price_prev, price_now, min_pct=HOT_DEAL_MIN_DROP_PCT,
                min_eur=HOT_DEAL_MIN_DROP_EUR, suspect_pct=SUSPECT_DROP_PCT):
    """Python mirror of the SQL filter passed to queries.hot_deals()."""
    if not price_prev or price_now is None or price_now >= price_prev:
        return False
    drop_eur = price_prev - price_now
    drop_pct = drop_eur / price_prev * 100
    return drop_pct >= min_pct and drop_eur >= min_eur and drop_pct < suspect_pct


def _coerce_drops(df):
    out = df.to_dict("records")
    for d in out:
        for k in ("prev_price", "new_price", "drop_eur", "drop_pct"):
            d[k] = round(float(d[k]), 2) if pd.notna(d[k]) else None
        d["drop_date"] = str(d["drop_date"])
        d["suspect"] = is_suspect_drop(d["drop_pct"])
        d["brand"] = display_brand(d.get("brand"))
    return out


def fetch_counts(conn):
    """Real KPI totals (COUNT(*)), independent of the display-list LIMITs."""
    return {
        "drops_today":         queries.count_drops(conn, 0),
        "drops_week":          queries.count_drops(conn, 7),
        "suspect_drops_today": queries.count_suspect_drops(conn, 0, SUSPECT_DROP_PCT),
        "suspect_drops_week":  queries.count_suspect_drops(conn, 7, SUSPECT_DROP_PCT),
        "new_week":            queries.count_new_products(conn, days=7),
        "disappeared_30d":     queries.count_disappeared(conn, days=30),
        "latest_products":     queries.count_latest_products(conn),
        "listed_today":        queries.count_listed(conn),
        "listed_prev":         queries.count_listed(conn, prev=True),
        "drops_prev":          queries.count_drops_prev_day(conn),
    }


def fetch_data(conn):
    today = datetime.date.today()

    total_products  = conn.execute(text("SELECT COUNT(*) FROM products")).scalar()
    total_snapshots = conn.execute(text("SELECT COUNT(*) FROM price_snapshots")).scalar()
    last_updated    = conn.execute(text("SELECT MAX(date) FROM price_snapshots")).scalar()
    window_start    = conn.execute(text(
        "SELECT MIN(date) FROM price_snapshots WHERE date >= (SELECT MAX(date) FROM price_snapshots) - 90"
    )).scalar()

    # Per-category stats (latest day)
    cat_rows = queries.category_snapshot(conn).itertuples()
    by_category = {
        r.category: {
            "count":     r.product_count,
            "median_price": round(float(r.median_price), 2) if pd.notna(r.median_price) else 0.0,
            "min_price": round(float(r.min_price), 2) if pd.notna(r.min_price) else 0.0,
            "max_price": round(float(r.max_price), 2) if pd.notna(r.max_price) else 0.0,
        }
        for r in cat_rows
    }

    # Today's drops
    drops = _coerce_drops(queries.biggest_drops(conn, days_back=0, limit=DROPS_TODAY_LIMIT))

    # This week's drops
    weekly_drops = _coerce_drops(queries.biggest_drops(conn, days_back=7, limit=DROPS_WEEK_LIMIT))

    # Brand summary — top 10 per category with median
    brand_rows = conn.execute(text("""
        SELECT category, brand, product_count,
               ROUND(avg_price,    2) AS avg_price,
               ROUND(min_price,    2) AS min_price,
               ROUND(max_price,    2) AS max_price,
               ROUND(median_price, 2) AS median_price
        FROM vw_brand_summary
        WHERE brand IS NOT NULL
        ORDER BY category, product_count DESC
    """)).fetchall()
    brand_data = {}
    for r in brand_rows:
        cat = r.category
        if cat not in brand_data:
            brand_data[cat] = []
        if len(brand_data[cat]) < 10 and is_real_brand(r.brand):
            brand_data[cat].append({
                "brand":         r.brand,
                "product_count": r.product_count,
                "avg_price":     float(r.avg_price    or 0),
                "min_price":     float(r.min_price    or 0),
                "max_price":     float(r.max_price    or 0),
                "median_price":  float(r.median_price or 0),
            })

    # All latest prices for search table (+ color + 30-day price volatility)
    lp_rows = conn.execute(text("""
        SELECT lp.id, lp.category, lp.brand, lp.model, lp.product_name,
               ROUND(lp.price_eur, 2)              AS price_eur,
               lp.rating, lp.reviews,
               lp.installments_per_month,
               lp.skroutz_link,
               p.color,
               COALESCE(ROUND(pv.cv_pct, 1), 0.0) AS cv_pct
        FROM vw_latest_prices lp
        JOIN products p ON p.id = lp.id
        LEFT JOIN vw_price_volatility pv ON pv.product_id = lp.id
        ORDER BY lp.reviews DESC NULLS LAST
        LIMIT :lim
    """), {"lim": PRODUCTS_LIMIT}).fetchall()

    try:
        conn.execute(text("SAVEPOINT sp_floor"))
        floor_rows = conn.execute(text(
            "SELECT product_id, all_time_low FROM vw_price_floor"
        )).fetchall()
        floor_map = {r.product_id: float(r.all_time_low)
                     for r in floor_rows if r.all_time_low}
        conn.execute(text("RELEASE SAVEPOINT sp_floor"))
    except SQLAlchemyError as e:
        conn.execute(text("ROLLBACK TO SAVEPOINT sp_floor"))
        logger.warning(f"vw_price_floor query failed, near-ATL data will be empty: {e}")
        floor_map = {}

    try:
        conn.execute(text("SAVEPOINT sp_trend"))
        trend_rows = conn.execute(text(
            "SELECT product_id, trend FROM vw_price_trend_direction"
        )).fetchall()
        trend_map = {r.product_id: r.trend for r in trend_rows}
        conn.execute(text("RELEASE SAVEPOINT sp_trend"))
    except SQLAlchemyError as e:
        conn.execute(text("ROLLBACK TO SAVEPOINT sp_trend"))
        logger.warning(f"vw_price_trend_direction query failed, trend arrows will be empty: {e}")
        trend_map = {}

    products = []
    for r in lp_rows:
        price = float(r.price_eur) if r.price_eur else None
        atl   = floor_map.get(r.id)
        floor_pct = (
            round((price - atl) / atl * 100, 1)
            if price is not None and atl and atl > 0 else None
        )
        products.append({
            "id":        r.id,
            "category":  r.category or "",
            "brand":     display_brand(r.brand),
            "model":     r.model or "",
            "name":      r.product_name or "",
            "price":     price,
            "rating":    float(r.rating)    if r.rating    else None,
            "reviews":   r.reviews,
            "monthly":   float(r.installments_per_month) if r.installments_per_month else None,
            "url":       r.skroutz_link or "",
            "color":     r.color or "",
            "cv":        float(r.cv_pct) if r.cv_pct else 0.0,
            "floor_pct": floor_pct,
            "atl":       atl,
            "trend":     trend_map.get(r.id, ""),
        })

    # Sparkline: last 30 days of prices per listed product, a few points each
    spark_map = {}
    if products:
        spark_rows = conn.execute(text("""
            SELECT product_id, ROUND(price_eur) AS price
            FROM price_snapshots
            WHERE product_id = ANY(:ids) AND price_eur IS NOT NULL
              AND date >= (SELECT MAX(date) FROM price_snapshots) - 30
            ORDER BY product_id, date
        """), {"ids": [p["id"] for p in products]}).fetchall()
        for r in spark_rows:
            spark_map.setdefault(r.product_id, []).append(int(r.price))
    for p in products:
        p["spark"] = payload.downsample(spark_map.get(p["id"], []), SPARK_POINTS)

    # New arrivals (first seen in last 7 days)
    new_rows = conn.execute(text("""
        SELECT p.id, p.category, p.brand, p.model, p.product_name,
               p.first_seen::text    AS first_seen,
               ROUND(s.price_eur, 2) AS price_eur,
               s.reviews, p.skroutz_link
        FROM products p
        JOIN price_snapshots s ON s.product_id = p.id
          AND s.date = (SELECT MAX(date) FROM price_snapshots)
        WHERE p.""" + queries.NEW_PRODUCTS_WHERE + """
        ORDER BY p.first_seen DESC LIMIT :lim
    """), {"lim": NEW_PRODUCTS_LIMIT, "days": 7}).fetchall()
    new_products = []
    for r in new_rows:
        new_products.append({
            "category":   r.category or "",
            "brand":      display_brand(r.brand),
            "model":      r.model or "",
            "name":       r.product_name or "",
            "first_seen": r.first_seen,
            "price":      float(r.price_eur) if r.price_eur else None,
            "reviews":    r.reviews,
            "url":        r.skroutz_link or "",
        })

    # Not seen for >30 days
    dis_rows = queries.disappeared(conn, days=30, limit=DISAPPEARED_LIMIT).itertuples()
    disappeared = []
    for r in dis_rows:
        disappeared.append({
            "category":  r.category or "",
            "brand":     display_brand(r.brand),
            "model":     r.model or r.product_name or "",
            "last_seen": str(r.last_seen),
            "days_gone": r.days_since_last_seen,
            "url":       r.skroutz_link or "",
        })

    # Price history for top 50 most-reviewed per category (200 total)
    history_rows = conn.execute(text("""
        WITH ranked AS (
            SELECT p.id,
                   ROW_NUMBER() OVER (
                       PARTITION BY p.category
                       ORDER BY (
                           SELECT MAX(reviews) FROM price_snapshots
                           WHERE product_id = p.id
                       ) DESC NULLS LAST
                   ) AS rn
            FROM products p
        )
        SELECT s.product_id, s.date, ROUND(s.price_eur, 2) AS price_eur
        FROM price_snapshots s
        JOIN ranked r ON r.id = s.product_id
        WHERE r.rn <= 50 AND s.price_eur IS NOT NULL
        ORDER BY s.product_id, s.date
    """)).fetchall()
    history = {}
    for r in history_rows:
        pid = r.product_id
        if pid not in history:
            history[pid] = []
        history[pid].append({"date": str(r.date), "price": float(r.price_eur)})

    # Hot deals — price drop + review surge between the two most recent scrapes
    hot_rows = queries.hot_deals(
        conn, limit=HOT_DEALS_LIMIT,
        min_drop_pct=HOT_DEAL_MIN_DROP_PCT,
        min_drop_eur=HOT_DEAL_MIN_DROP_EUR,
    ).itertuples()
    hot_deals = []
    for r in hot_rows:
        hot_deals.append({
            "category":   r.category or "",
            "brand":      display_brand(r.brand),
            "model":      r.model or r.product_name or "",
            "price_prev": round(float(r.price_prev), 2)   if pd.notna(r.price_prev)   and r.price_prev   else None,
            "price_now":  round(float(r.price_latest), 2) if pd.notna(r.price_latest) and r.price_latest else None,
            "chg_pct":    float(r.price_chg_pct) if pd.notna(r.price_chg_pct) and r.price_chg_pct else 0.0,
            "new_rev":    int(r.new_reviews) if pd.notna(r.new_reviews) else 0,
            "score":      float(r.hot_score) if pd.notna(r.hot_score) and r.hot_score else 0.0,
            "suspect":    is_suspect_drop(r.price_chg_pct if pd.notna(r.price_chg_pct) else None),
            "url":        r.skroutz_link or "",
            "from_date":  r.prev_date    or "",
            "to_date":    r.latest_date  or "",
        })

    # Brand median-price trend for comparison charts (top 8 brands/category, last 90 days)
    brand_trend = {}
    for cat in by_category:
        trend_df = queries.brand_trend(conn, cat, top_n=8, days=90)
        if trend_df.empty:
            continue
        brand_trend[cat] = {}
        for r in trend_df.itertuples():
            if not is_real_brand(r.brand):
                continue
            if r.brand not in brand_trend[cat]:
                brand_trend[cat][r.brand] = []
            brand_trend[cat][r.brand].append({"date": str(r.date), "price": round(float(r.median_price), 2)})

    # Buy or wait: today's price vs each product's own 90-day low/median/high
    try:
        conn.execute(text("SAVEPOINT sp_bow"))
        bow = buy_or_wait.build(buy_or_wait.fetch(conn))
        conn.execute(text("RELEASE SAVEPOINT sp_bow"))
    except Exception as e:  # noqa: BLE001 — optional section; query or build errors leave it empty instead of failing the dashboard
        conn.execute(text("ROLLBACK TO SAVEPOINT sp_bow"))
        logger.warning(f"buy-or-wait query failed, the Buy or Wait tab will be empty: {e}")
        bow = {}

    try:
        conn.execute(text("SAVEPOINT sp_market"))
        idx_rows = queries.basket_index(conn, days=90).itertuples()
        market_index: dict = {}
        for r in idx_rows:
            cat = r.category
            if cat not in market_index:
                market_index[cat] = []
            market_index[cat].append({"date": str(r.date), "idx": round(float(r.idx), 2), "n": int(r.products)})
        conn.execute(text("RELEASE SAVEPOINT sp_market"))
    except Exception as e:  # noqa: BLE001 — optional section; query or build errors leave it empty instead of failing the dashboard
        conn.execute(text("ROLLBACK TO SAVEPOINT sp_market"))
        logger.warning(f"basket index query failed, category price index chart will be empty: {e}")
        market_index = {}

    return {
        "generated":       str(today),
        "freshness":       payload.freshness(
            datetime.datetime.now().astimezone(), last_updated or today),
        "total_products":  total_products,
        "total_snapshots": total_snapshots,
        "last_updated":    str(last_updated) if last_updated else str(today),
        "window_start":    str(window_start) if window_start else str(today),
        "by_category":     by_category,
        "counts":          fetch_counts(conn),
        "limits": {
            "drops_today": DROPS_TODAY_LIMIT, "drops_week": DROPS_WEEK_LIMIT,
            "new": NEW_PRODUCTS_LIMIT, "disappeared": DISAPPEARED_LIMIT,
            "products": PRODUCTS_LIMIT, "hot_deals": HOT_DEALS_LIMIT,
        },
        "thresholds": {
            "suspect_pct": SUSPECT_DROP_PCT,
            "big_drop_label": BIG_DROP_LABEL_EN,
            "hot_min_pct": HOT_DEAL_MIN_DROP_PCT,
            "hot_min_eur": HOT_DEAL_MIN_DROP_EUR,
        },
        "drops":           drops,
        "weekly_drops":    weekly_drops,
        "hot_deals":       hot_deals,
        "brand_data":      brand_data,
        "brand_trend":     brand_trend,
        "products":        products,
        "new_products":    new_products,
        "disappeared":     disappeared,
        "history":          history,
        "buy_or_wait":   bow,
        "market_index":  market_index,
    }


def chart_url(name, version):
    """Relative URL of a chart PNG (published next to dashboard/ on Pages), or ''."""
    if not (CHARTS_DIR / name).exists():
        return ""
    return f"../charts/{name}?v={version}"


HTML_TEMPLATE_PATH = TEMPLATES_DIR / "dashboard.html"
HTML_TEMPLATE = HTML_TEMPLATE_PATH.read_text(encoding="utf-8")

# Chart.js is loaded from ../assets/ (published with the page) instead of inlined.
CHARTJS_SRC = "../assets/chart.umd.min.js"


def render_html(data):
    """Fill the HTML template from a fetch_data() dict. Pops data["history"]."""
    charts_json = {
        k: chart_url(k + ".png", data["generated"])
        for k in ("price_trend_phone", "price_trend_laptop",
                  "price_trend_smartwatch", "price_trend_tablet")
    }

    history = payload.compact_history(data.pop("history"))
    data = dict(data, products=payload.compact_products(data["products"]),
                brand_trend={cat: payload.compact_history(series)
                             for cat, series in data["brand_trend"].items()})
    # Public page: never publish the personal watchlist / target prices.
    data.pop("watchlist", None)

    window_start_date  = datetime.date.fromisoformat(data["window_start"])
    window_start_label = f"Since {window_start_date.day} {window_start_date.strftime('%b %Y')}"

    html = (HTML_TEMPLATE
        .replace("__CHARTJS_SRC__",        f"{CHARTJS_SRC}?v={data['generated']}")
        .replace("__GENERATED__",          data["generated"])
        .replace("__TOTAL_PRODUCTS__",     f"{data['total_products']:,}")
        .replace("__TOTAL_SNAPSHOTS__",    f"{data['total_snapshots']:,}")
        .replace("__WINDOW_START_LABEL__", window_start_label)
        .replace("__DATA_JSON__",        json.dumps(data,        ensure_ascii=False, separators=(",", ":")))
        .replace("__HISTORY_JSON__",     json.dumps(history,     ensure_ascii=False, separators=(",", ":")))
        .replace("__CHARTS_JSON__",      json.dumps(charts_json, ensure_ascii=False, separators=(",", ":")))
    )
    return html


def main():
    print("Generating dashboard…")
    try:
        engine = get_engine()
        with engine.connect() as conn:
            data = fetch_data(conn)
    except Exception as e:  # noqa: BLE001 — top-level guard; prints the error and exits 1
        print(f"Dashboard: DB connection failed — {e}", file=sys.stderr)
        sys.exit(1)

    html = render_html(data)

    out_path = OUT_DIR / f"dashboard_{data['generated']}.html"
    tmp_dated = out_path.parent / (out_path.name + ".tmp")
    tmp_dated.write_text(html, encoding="utf-8")
    os.replace(str(tmp_dated), str(out_path))
    print(f"Dashboard saved: {out_path}")

    latest = OUT_DIR / "dashboard_latest.html"
    tmp_latest = latest.parent / (latest.name + ".tmp")
    tmp_latest.write_text(html, encoding="utf-8")
    os.replace(str(tmp_latest), str(latest))
    print(f"Latest copy:     {latest}")


if __name__ == "__main__":
    main()
