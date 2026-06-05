"""
generate_dashboard.py
---------------------
Generates a self-contained dashboard.html from the PostgreSQL database.

Embeds all data as JSON so the file works offline with no server.
Run standalone:  python generate_dashboard.py
Auto-called by run_pipeline.py after each successful scrape.

Output: dashboard/dashboard_<YYYY-MM-DD>.html  +  dashboard/dashboard_latest.html
"""

import os
import sys
import json
import base64
import datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

from db import get_engine

load_dotenv()

BASE       = Path(__file__).parent
CHARTS_DIR = BASE / "charts"
OUT_DIR    = BASE / "dashboard"
OUT_DIR.mkdir(exist_ok=True)


def _coerce_drops(rows):
    out = [dict(r._mapping) for r in rows]
    for d in out:
        for k in ("prev_price", "new_price", "drop_eur", "drop_pct"):
            d[k] = float(d[k]) if d[k] is not None else None
    return out


def fetch_data(conn):
    today = datetime.date.today()

    total_products  = conn.execute(text("SELECT COUNT(*) FROM products")).scalar()
    total_snapshots = conn.execute(text("SELECT COUNT(*) FROM price_snapshots")).scalar()
    last_updated    = conn.execute(text("SELECT MAX(date) FROM price_snapshots")).scalar()

    # Per-category stats (latest day)
    cat_rows = conn.execute(text("""
        SELECT p.category,
               COUNT(DISTINCT p.id)       AS product_count,
               ROUND(AVG(s.price_eur), 2) AS avg_price,
               ROUND(MIN(s.price_eur), 2) AS min_price,
               ROUND(MAX(s.price_eur), 2) AS max_price
        FROM products p
        JOIN price_snapshots s ON s.product_id = p.id
        WHERE s.date = (SELECT MAX(date) FROM price_snapshots)
        GROUP BY p.category ORDER BY p.category
    """)).fetchall()
    by_category = {
        r.category: {
            "count":     r.product_count,
            "avg_price": float(r.avg_price or 0),
            "min_price": float(r.min_price or 0),
            "max_price": float(r.max_price or 0),
        }
        for r in cat_rows
    }

    # Today's drops
    drops = _coerce_drops(conn.execute(text("""
        SELECT brand, model, category,
               ROUND(prev_price, 2) AS prev_price,
               ROUND(new_price,  2) AS new_price,
               ROUND(drop_eur,   2) AS drop_eur,
               ROUND(drop_pct,   2) AS drop_pct,
               drop_date::text      AS drop_date,
               skroutz_link
        FROM vw_biggest_drops
        WHERE drop_date = CURRENT_DATE
        ORDER BY drop_eur ASC LIMIT 25
    """)).fetchall())

    # This week's drops
    weekly_drops = _coerce_drops(conn.execute(text("""
        SELECT brand, model, category,
               ROUND(prev_price, 2) AS prev_price,
               ROUND(new_price,  2) AS new_price,
               ROUND(drop_eur,   2) AS drop_eur,
               ROUND(drop_pct,   2) AS drop_pct,
               drop_date::text      AS drop_date,
               skroutz_link
        FROM vw_biggest_drops
        WHERE drop_date >= CURRENT_DATE - 7
        ORDER BY drop_eur ASC LIMIT 30
    """)).fetchall())

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
        if len(brand_data[cat]) < 10:
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
    """)).fetchall()

    try:
        conn.execute(text("SAVEPOINT sp_floor"))
        floor_rows = conn.execute(text(
            "SELECT product_id, all_time_low FROM vw_price_floor"
        )).fetchall()
        floor_map = {r.product_id: float(r.all_time_low)
                     for r in floor_rows if r.all_time_low}
        conn.execute(text("RELEASE SAVEPOINT sp_floor"))
    except Exception:
        conn.execute(text("ROLLBACK TO SAVEPOINT sp_floor"))
        floor_map = {}

    try:
        conn.execute(text("SAVEPOINT sp_trend"))
        trend_rows = conn.execute(text(
            "SELECT product_id, trend FROM vw_price_trend_direction"
        )).fetchall()
        trend_map = {r.product_id: r.trend for r in trend_rows}
        conn.execute(text("RELEASE SAVEPOINT sp_trend"))
    except Exception:
        conn.execute(text("ROLLBACK TO SAVEPOINT sp_trend"))
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
            "brand":     r.brand or "",
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

    # New arrivals (first seen in last 7 days)
    new_rows = conn.execute(text("""
        SELECT p.id, p.category, p.brand, p.model, p.product_name,
               p.first_seen::text    AS first_seen,
               ROUND(s.price_eur, 2) AS price_eur,
               s.reviews, p.skroutz_link
        FROM products p
        JOIN price_snapshots s ON s.product_id = p.id
          AND s.date = (SELECT MAX(date) FROM price_snapshots)
        WHERE p.first_seen >= CURRENT_DATE - 7
        ORDER BY p.first_seen DESC LIMIT 50
    """)).fetchall()
    new_products = []
    for r in new_rows:
        new_products.append({
            "category":   r.category or "",
            "brand":      r.brand or "",
            "model":      r.model or "",
            "name":       r.product_name or "",
            "first_seen": r.first_seen,
            "price":      float(r.price_eur) if r.price_eur else None,
            "reviews":    r.reviews,
            "url":        r.skroutz_link or "",
        })

    # Disappeared (last 30 days)
    dis_rows = conn.execute(text("""
        SELECT category, brand, model, product_name,
               last_seen::text    AS last_seen,
               days_since_last_seen, skroutz_link
        FROM vw_disappeared
        WHERE days_since_last_seen <= 30
        ORDER BY last_seen DESC LIMIT 50
    """)).fetchall()
    disappeared = []
    for r in dis_rows:
        disappeared.append({
            "category":  r.category or "",
            "brand":     r.brand or "",
            "model":     r.model or r.product_name or "",
            "last_seen": r.last_seen,
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

    watchlist = _load_watchlist(conn)

    # Hot deals — price drop + review surge between the two most recent scrapes
    hot_rows = conn.execute(text("""
        SELECT category, brand, model, product_name,
               ROUND(price_prev,    2) AS price_prev,
               ROUND(price_latest,  2) AS price_latest,
               price_chg_pct, new_reviews, hot_score, skroutz_link,
               prev_date::text   AS prev_date,
               latest_date::text AS latest_date
        FROM vw_hot_deals
        LIMIT 20
    """)).fetchall()
    hot_deals = []
    for r in hot_rows:
        hot_deals.append({
            "category":   r.category or "",
            "brand":      r.brand or "",
            "model":      r.model or r.product_name or "",
            "price_prev": float(r.price_prev)    if r.price_prev    else None,
            "price_now":  float(r.price_latest)  if r.price_latest  else None,
            "chg_pct":    float(r.price_chg_pct) if r.price_chg_pct else 0.0,
            "new_rev":    int(r.new_reviews or 0),
            "score":      float(r.hot_score) if r.hot_score else 0.0,
            "url":        r.skroutz_link or "",
            "from_date":  r.prev_date    or "",
            "to_date":    r.latest_date  or "",
        })

    # Brand avg-price trend for comparison charts (top 8 brands/category, last 90 days)
    trend_rows = conn.execute(text("""
        WITH ranked AS (
            SELECT category, brand,
                   ROW_NUMBER() OVER (PARTITION BY category ORDER BY product_count DESC) AS rn
            FROM vw_brand_summary
            WHERE brand IS NOT NULL
        )
        SELECT bt.category, bt.brand, bt.date::text AS date,
               ROUND(bt.avg_price, 2) AS avg_price
        FROM vw_brand_price_trend bt
        JOIN ranked r ON r.category = bt.category AND r.brand = bt.brand
        WHERE r.rn <= 8
          AND bt.date >= CURRENT_DATE - 90
        ORDER BY bt.category, bt.brand, bt.date
    """)).fetchall()
    brand_trend = {}
    for r in trend_rows:
        cat = r.category
        if cat not in brand_trend:
            brand_trend[cat] = {}
        if r.brand not in brand_trend[cat]:
            brand_trend[cat][r.brand] = []
        brand_trend[cat][r.brand].append({"date": r.date, "price": float(r.avg_price)})

    # Brand discount frequency — gracefully absent until analytics.sql v2 is applied
    try:
        conn.execute(text("SAVEPOINT sp_disc"))
        disc_rows = conn.execute(text("""
            SELECT category, brand, discount_days, tracked_days, discount_freq_pct
            FROM vw_brand_discount_freq
            WHERE brand IS NOT NULL
            ORDER BY category, discount_freq_pct DESC NULLS LAST
        """)).fetchall()
        discount_data = {}
        for r in disc_rows:
            cat = r.category
            if cat not in discount_data:
                discount_data[cat] = []
            if len(discount_data[cat]) < 12:
                discount_data[cat].append({
                    "brand":     r.brand,
                    "disc_days": int(r.discount_days or 0),
                    "freq_pct":  float(r.discount_freq_pct or 0),
                })
        conn.execute(text("RELEASE SAVEPOINT sp_disc"))
    except Exception:
        conn.execute(text("ROLLBACK TO SAVEPOINT sp_disc"))
        discount_data = {}

    try:
        conn.execute(text("SAVEPOINT sp_market"))
        idx_rows = conn.execute(text("""
            SELECT category, date::text AS date, avg_price
            FROM vw_daily_market_index
            WHERE date >= CURRENT_DATE - 90
            ORDER BY category, date
        """)).fetchall()
        market_index: dict = {}
        for r in idx_rows:
            cat = r.category
            if cat not in market_index:
                market_index[cat] = []
            market_index[cat].append({"date": r.date, "avg": float(r.avg_price)})
        conn.execute(text("RELEASE SAVEPOINT sp_market"))
    except Exception:
        conn.execute(text("ROLLBACK TO SAVEPOINT sp_market"))
        market_index = {}

    return {
        "generated":       str(today),
        "total_products":  total_products,
        "total_snapshots": total_snapshots,
        "last_updated":    str(last_updated) if last_updated else str(today),
        "by_category":     by_category,
        "drops":           drops,
        "weekly_drops":    weekly_drops,
        "hot_deals":       hot_deals,
        "brand_data":      brand_data,
        "brand_trend":     brand_trend,
        "products":        products,
        "new_products":    new_products,
        "disappeared":     disappeared,
        "watchlist":        watchlist,
        "history":          history,
        "discount_data": discount_data,
        "market_index":  market_index,
    }


def _load_watchlist(conn):
    path = BASE / "watchlist.json"
    if not path.exists():
        return []
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    result = []
    for item in items:
        url       = item.get("url", "").strip()
        label     = item.get("label", url)
        threshold = float(item.get("threshold_eur", 0))
        if not url:
            continue
        try:
            row = conn.execute(text(
                "SELECT brand, model, category, ROUND(price_eur, 2) AS price_eur "
                "FROM vw_latest_prices "
                "WHERE skroutz_link = :url OR skroutz_link LIKE :url_prefix"
            ), {"url": url, "url_prefix": url.split("?")[0] + "%"}).fetchone()
        except Exception:
            row = None
        price = float(row.price_eur) if row and row.price_eur else None
        result.append({
            "label":     label,
            "threshold": threshold,
            "url":       url,
            "brand":     (row.brand or "") if row else "",
            "model":     (row.model or "") if row else "",
            "category":  row.category      if row else "",
            "price":     price,
            "hit":       (price <= threshold) if price is not None else False,
        })
    return result


def encode_chart(name):
    p = CHARTS_DIR / name
    if not p.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()


# ── HTML template — uses __TOKEN__ placeholders so JS braces need no escaping ──
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Skroutz Price Tracker — __GENERATED__</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.2/dist/chart.umd.min.js"></script>
<style>
:root {
  --bg:        #0f1117;
  --surface:   #1a1d27;
  --surface2:  #22263a;
  --border:    #2a2d3a;
  --accent:    #4f8ef7;
  --text:      #e2e8f0;
  --muted:     #64748b;
  --drop:      #ef4444;
  --rise:      #22c55e;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: system-ui, sans-serif; font-size: 14px; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.container { max-width: 1400px; margin: 0 auto; padding: 24px 16px; }

/* Header */
header {
  display: flex; align-items: center; justify-content: space-between;
  border-bottom: 1px solid var(--border); padding-bottom: 16px; margin-bottom: 20px;
}
header h1 { font-size: 22px; font-weight: 700; color: var(--accent); }
.header-meta { color: var(--muted); font-size: 12px; text-align: right; line-height: 2; }

/* Stat cards */
.stats-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(148px, 1fr));
  gap: 10px; margin-bottom: 20px;
}
.stat-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 14px;
}
.stat-card .val { font-size: 22px; font-weight: 700; color: var(--accent); }
.stat-card .lbl { font-size: 10px; color: var(--muted); margin-top: 4px; text-transform: uppercase; letter-spacing: .06em; }
.stat-card.highlight .val { color: var(--drop); }
.stat-card.success .val   { color: var(--rise); }

/* Tabs */
.tab-nav {
  display: flex; gap: 2px; margin-bottom: 20px;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 4px; width: fit-content;
  position: sticky; top: 8px; z-index: 10;
}
.tab-btn {
  background: none; border: none; color: var(--muted);
  padding: 7px 16px; border-radius: 7px; cursor: pointer;
  font-size: 13px; font-weight: 500; transition: all .15s; white-space: nowrap;
}
.tab-btn:hover { color: var(--text); background: var(--surface2); }
.tab-btn.active { background: var(--accent); color: #fff; }
.tab-content { display: none; }
.tab-content.active { display: block; }

/* Sections */
.section { margin-bottom: 30px; }
.section-title {
  font-size: 14px; font-weight: 600; margin-bottom: 12px;
  border-left: 3px solid var(--accent); padding-left: 10px;
  display: flex; align-items: center; gap: 8px;
}
.count-badge {
  background: var(--surface2); color: var(--muted);
  font-size: 11px; padding: 1px 7px; border-radius: 20px; font-weight: 400;
}

/* Tables */
.table-wrap { overflow-x: auto; border-radius: 10px; border: 1px solid var(--border); }
table { width: 100%; border-collapse: collapse; }
th {
  background: var(--surface); color: var(--muted); font-size: 11px;
  text-transform: uppercase; letter-spacing: .05em;
  padding: 10px 14px; text-align: left; border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
th.sortable { cursor: pointer; user-select: none; }
th.sortable:hover { color: var(--text); }
th.sorted { color: var(--accent); }
td { padding: 8px 14px; border-bottom: 1px solid var(--border); vertical-align: middle; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: #ffffff08; }
.no-data { color: var(--muted); text-align: center; padding: 28px; font-size: 13px; }

/* Category badges */
.cat-badge { display: inline-block; padding: 2px 8px; border-radius: 20px; font-size: 11px; font-weight: 600; white-space: nowrap; }
.cat-phone      { background: #3b82f620; color: #3b82f6; }
.cat-laptop     { background: #a78bfa20; color: #a78bfa; }
.cat-smartwatch { background: #22c55e20; color: #22c55e; }
.cat-tablet     { background: #f59e0b20; color: #f59e0b; }

/* Value styles */
.drop-eur { color: var(--drop); font-weight: 600; }
.drop-pct { color: #f87171; }
.price    { font-weight: 600; }
.muted    { color: var(--muted); }

/* Drop bar */
.drop-bar { display: flex; align-items: center; gap: 6px; }
.drop-bar-fill { height: 4px; border-radius: 2px; background: var(--drop); min-width: 3px; flex-shrink: 0; }

/* Sub-tabs */
.sub-tabs { display: flex; gap: 8px; margin-bottom: 12px; }
.sub-tab-btn {
  background: var(--surface); border: 1px solid var(--border);
  color: var(--muted); padding: 5px 14px; border-radius: 6px;
  cursor: pointer; font-size: 12px; font-weight: 500;
}
.sub-tab-btn.active { border-color: var(--accent); color: var(--accent); background: #4f8ef714; }

/* Trend chart images */
.charts-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(540px, 1fr)); gap: 14px; }
.chart-card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
.chart-card img { width: 100%; display: block; }
.chart-card .chart-lbl { padding: 8px 14px; font-size: 12px; color: var(--muted); text-align: center; }

/* Brand charts */
.brand-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 14px; }
.brand-card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }
.brand-card h4 { font-size: 12px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); margin-bottom: 12px; }

/* Metric toggle */
.metric-toggle { display: flex; gap: 6px; margin-bottom: 16px; flex-wrap: wrap; }
.metric-btn {
  background: var(--surface); border: 1px solid var(--border);
  color: var(--muted); padding: 5px 12px; border-radius: 6px;
  cursor: pointer; font-size: 12px;
}
.metric-btn.active { border-color: var(--accent); color: var(--accent); background: #4f8ef714; }

/* Search bar */
.search-bar { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; align-items: center; }
.search-bar input, .search-bar select {
  background: var(--surface); border: 1px solid var(--border);
  color: var(--text); border-radius: 8px; padding: 7px 12px; font-size: 13px;
}
.search-bar input { flex: 1; min-width: 160px; }
.search-bar input:focus, .search-bar select:focus { outline: none; border-color: var(--accent); }
.price-range { display: flex; gap: 6px; align-items: center; }
.price-range input { width: 80px; flex: none !important; }
.search-count { color: var(--muted); font-size: 12px; }

/* Watchlist */
.wl-list { display: flex; flex-direction: column; gap: 8px; }
.wl-item {
  background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
  padding: 12px 16px; display: flex; justify-content: space-between; align-items: center; gap: 16px;
}
.wl-item.hit { border-color: var(--rise); }
.wl-name { font-weight: 500; font-size: 13px; }
.wl-meta { font-size: 11px; color: var(--muted); margin-top: 3px; }
.wl-prices { text-align: right; white-space: nowrap; }
.wl-current { font-size: 18px; font-weight: 700; }
.wl-current.hit   { color: var(--rise); }
.wl-current.above { color: var(--text); }
.wl-target { font-size: 11px; color: var(--muted); margin-top: 1px; }
.wl-hit-badge {
  background: #22c55e18; color: var(--rise); border: 1px solid #22c55e60;
  border-radius: 20px; padding: 2px 10px; font-size: 11px; font-weight: 600;
}

/* History modal */
.modal-overlay {
  display: none; position: fixed; inset: 0; background: #000000bb;
  z-index: 100; align-items: center; justify-content: center;
}
.modal-overlay.open { display: flex; }
.modal {
  background: var(--surface); border: 1px solid var(--border); border-radius: 14px;
  padding: 24px; width: min(740px, 95vw); max-height: 85vh; overflow-y: auto;
}
.modal-header {
  display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 14px;
}
.modal-header h3 { font-size: 15px; font-weight: 600; max-width: 88%; }
.modal-close {
  background: none; border: none; color: var(--muted);
  font-size: 20px; cursor: pointer; padding: 0 4px; line-height: 1;
}
.modal-close:hover { color: var(--drop); }

/* History stats strip */
.hist-stats {
  display: flex; gap: 0; margin-bottom: 16px; flex-wrap: wrap;
  background: var(--surface2); border-radius: 8px; overflow: hidden;
}
.hist-stat {
  padding: 10px 16px; text-align: center; flex: 1; min-width: 90px;
  border-right: 1px solid var(--border);
}
.hist-stat:last-child { border-right: none; }
.hs-val { font-size: 15px; font-weight: 700; color: var(--accent); }
.hs-val.drop { color: var(--drop); }
.hs-val.rise { color: var(--rise); }
.hs-lbl { font-size: 10px; color: var(--muted); margin-top: 3px; text-transform: uppercase; letter-spacing: .04em; }

/* Volatility badges */
.vol-badge { display:inline-block; padding:2px 7px; border-radius:20px; font-size:10px; font-weight:600; white-space:nowrap; }
.vol-stable   { background:#22c55e18; color:#22c55e; }
.vol-volatile { background:#ef444418; color:#ef4444; }

/* Color badge */
.color-tag { display:inline-block; padding:2px 7px; border-radius:20px; font-size:10px;
             background:var(--surface2); color:var(--muted); white-space:nowrap; }

/* Hot deals score bar */
.score-bar-wrap { display:flex; align-items:center; gap:6px; }
.score-bar { height:6px; border-radius:3px; background:linear-gradient(90deg,#4f8ef7,#22c55e); min-width:3px; }

/* Market share donuts */
.donut-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:14px; }
.donut-card { background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:16px; }
.donut-card h4 { font-size:12px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); margin-bottom:10px; }

/* Brand compare */
.compare-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(340px,1fr)); gap:14px; }
.compare-card { background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:16px; }
.compare-card h4 { font-size:12px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); margin-bottom:10px; }
.brand-selector { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:10px; }
.brand-chk-btn {
  background:var(--surface); border:1px solid var(--border); color:var(--muted);
  padding:3px 10px; border-radius:20px; cursor:pointer; font-size:11px; user-select:none;
}
.brand-chk-btn.sel { border-color:var(--accent); color:var(--accent); background:#4f8ef714; }

/* Intelligence tab */
.intel-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(360px,1fr)); gap:14px; }
.intel-card { background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:16px; }
.intel-card h4 { font-size:12px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); margin-bottom:10px; }

/* ATL proximity bar */
.atl-bar-bg { flex:1; height:6px; border-radius:3px; background:var(--surface2); }
.atl-bar-fill { height:100%; border-radius:3px; }
.atl-bar-wrap { display:flex; align-items:center; gap:8px; min-width:80px; }

/* Price vs Rating scatter legend */
.scatter-legend { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:8px; }
.scatter-legend-item { display:flex; align-items:center; gap:5px; font-size:11px; color:var(--muted); }
.scatter-dot { width:10px; height:10px; border-radius:50%; flex-shrink:0; }

/* Tab count badges */
.tab-count {
  display:inline-block; font-size:10px; font-weight:600;
  padding:1px 6px; border-radius:10px; margin-left:5px;
  background:var(--surface2); color:var(--muted);
}
.tab-btn.active .tab-count { background:#ffffff25; color:#ffffffcc; }

/* ATL badge */
.atl-badge {
  display:inline-block; padding:1px 6px; border-radius:10px;
  font-size:10px; font-weight:700; margin-left:5px;
  background:#22c55e18; color:#22c55e; white-space:nowrap;
}

/* Responsive */
@media (max-width: 700px) {
  .charts-grid { grid-template-columns: 1fr; }
  .tab-nav { overflow-x: auto; width: 100%; }
  header { flex-direction: column; gap: 8px; align-items: flex-start; }
  .hist-stats { flex-wrap: wrap; }
  .hist-stat { min-width: 45%; }
}
</style>
</head>
<body>
<div class="container">

<header>
  <h1>&#128202; Skroutz Price Tracker</h1>
  <div class="header-meta">
    Generated __GENERATED__<br>
    __TOTAL_PRODUCTS__ products &nbsp;&middot;&nbsp; __TOTAL_SNAPSHOTS__ snapshots
  </div>
</header>

<div class="stats-grid" id="stat-cards"></div>

<nav class="tab-nav">
  <button class="tab-btn active"  data-tab="overview"  onclick="showTab(this)">Overview</button>
  <button class="tab-btn"         data-tab="drops"     onclick="showTab(this)">Price Drops<span class="tab-count" id="tc-drops"></span></button>
  <button class="tab-btn"         data-tab="products"  onclick="showTab(this)">Products<span class="tab-count" id="tc-products"></span></button>
  <button class="tab-btn"         data-tab="new-gone"  onclick="showTab(this)">New &amp; Gone<span class="tab-count" id="tc-new-gone"></span></button>
  <button class="tab-btn"         data-tab="insights"     onclick="showTab(this)">Insights</button>
  <button class="tab-btn"         data-tab="intelligence" onclick="showTab(this)">Intelligence<span class="tab-count" id="tc-intel"></span></button>
</nav>

<!-- ── Overview ─────────────────────────────────────────────────────────────── -->
<div id="tab-overview" class="tab-content active">
  <div class="section">
    <div class="section-title">&#128200; Price Trends &mdash; Top 6 by Reviews</div>
    <div class="charts-grid" id="trend-charts"></div>
  </div>
  <div class="section">
    <div class="section-title">&#128202; Category Price Index &mdash; Last 90 Days</div>
    <div style="max-height:280px;position:relative"><canvas id="market-index-chart"></canvas></div>
  </div>
</div>

<!-- ── Price Drops ───────────────────────────────────────────────────────────── -->
<div id="tab-drops" class="tab-content">
  <div class="section" id="hot-deals-section" style="display:none">
    <div class="section-title">&#128293; Hot Deals &mdash; Price Drop + Review Surge
      <span class="count-badge" id="hot-badge"></span>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Brand</th><th>Model</th><th>Category</th>
          <th>Was &euro;</th><th>Now &euro;</th><th>Change</th>
          <th>New Reviews</th><th>Score</th>
        </tr></thead>
        <tbody id="hot-body"></tbody>
      </table>
    </div>
  </div>

  <div class="section">
    <div class="section-title" id="drops-title">&#128315; Price Drops</div>
    <div class="sub-tabs">
      <button class="sub-tab-btn active" onclick="showDropsView('today', this)">Today</button>
      <button class="sub-tab-btn"        onclick="showDropsView('week',  this)">This Week</button>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr id="drops-thead"></tr></thead>
        <tbody id="drops-body"></tbody>
      </table>
    </div>
  </div>
</div>

<!-- ── Products ──────────────────────────────────────────────────────────────── -->
<div id="tab-products" class="tab-content">
  <div class="section">
    <div class="section-title">&#128269; Product Search</div>
    <div class="search-bar">
      <input id="q" type="text" placeholder="Brand, model&hellip;" oninput="filterProducts()"/>
      <select id="cat-filter" onchange="filterProducts()">
        <option value="">All categories</option>
        <option value="phone">Phones</option>
        <option value="laptop">Laptops</option>
        <option value="smartwatch">Smartwatches</option>
        <option value="tablet">Tablets</option>
      </select>
      <select id="color-filter" onchange="filterProducts()">
        <option value="">All colors</option>
      </select>
      <select id="trend-filter" onchange="filterProducts()">
        <option value="">All trends</option>
        <option value="falling">&#8595; Falling</option>
        <option value="stable">&#8594; Stable</option>
        <option value="rising">&#8593; Rising</option>
      </select>
      <div class="price-range">
        <span style="color:var(--muted);font-size:12px">&euro;</span>
        <input id="price-min" type="number" placeholder="Min" min="0" oninput="filterProducts()"/>
        <span style="color:var(--muted);font-size:12px">&ndash;</span>
        <input id="price-max" type="number" placeholder="Max" min="0" oninput="filterProducts()"/>
      </div>
      <label style="display:flex;align-items:center;gap:5px;font-size:12px;color:var(--muted);cursor:pointer">
        <input type="checkbox" id="chk-financing" onchange="filterProducts()"> Financing
      </label>
      <label style="display:flex;align-items:center;gap:5px;font-size:12px;color:var(--muted);cursor:pointer">
        <input type="checkbox" id="chk-stable" onchange="filterProducts()"> Stable price
      </label>
      <span class="search-count" id="search-count"></span>
      <button onclick="exportCSV()" style="background:var(--surface);border:1px solid var(--border);color:var(--muted);border-radius:8px;padding:7px 12px;font-size:12px;cursor:pointer">&#11015; CSV</button>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th class="sortable" onclick="sortProducts('brand')">Brand <span id="sarr-brand"></span></th>
            <th>Model</th>
            <th>Category</th>
            <th class="sortable" onclick="sortProducts('price')">Price &euro; <span id="sarr-price"></span></th>
            <th class="sortable" onclick="sortProducts('rating')">Rating <span id="sarr-rating"></span></th>
            <th class="sortable" onclick="sortProducts('reviews')">Reviews <span id="sarr-reviews"></span></th>
            <th>Monthly</th>
            <th>Color</th>
            <th class="sortable" onclick="sortProducts('cv')">Stability <span id="sarr-cv"></span></th>
            <th>History</th>
          </tr>
        </thead>
        <tbody id="products-body"></tbody>
      </table>
    </div>
  </div>
</div>

<!-- ── New & Gone ────────────────────────────────────────────────────────────── -->
<div id="tab-new-gone" class="tab-content">
  <div class="section">
    <div class="section-title">
      &#127381; New Arrivals This Week
      <span class="count-badge" id="new-badge"></span>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Brand</th><th>Model</th><th>Category</th>
          <th>Price &euro;</th><th>First Seen</th><th>Reviews</th>
        </tr></thead>
        <tbody id="new-body"></tbody>
      </table>
    </div>
  </div>

  <div class="section">
    <div class="section-title">
      &#128683; Disappeared (last 30 days)
      <span class="count-badge" id="gone-badge"></span>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Brand</th><th>Model</th><th>Category</th>
          <th>Last Seen</th><th>Days Gone</th>
        </tr></thead>
        <tbody id="gone-body"></tbody>
      </table>
    </div>
  </div>

  <div class="section" id="watchlist-section" style="display:none">
    <div class="section-title">
      &#127919; Watchlist
      <span class="count-badge" id="wl-badge"></span>
    </div>
    <div class="wl-list" id="wl-list"></div>
  </div>
</div>

<!-- ── Insights ──────────────────────────────────────────────────────────────── -->
<div id="tab-insights" class="tab-content">
  <div class="section">
    <div class="section-title">&#127991; Brand Analysis</div>
    <div class="metric-toggle">
      <button class="metric-btn active" data-metric="avg"    onclick="setBrandMetric(this)">Avg Price &euro;</button>
      <button class="metric-btn"        data-metric="median" onclick="setBrandMetric(this)">Median Price &euro;</button>
      <button class="metric-btn"        data-metric="count"  onclick="setBrandMetric(this)">Product Count</button>
      <button class="metric-btn"        data-metric="range"  onclick="setBrandMetric(this)">Price Range</button>
    </div>
    <div class="brand-grid" id="brand-charts"></div>
  </div>

  <div class="section">
    <div class="section-title">&#127381; Market Share &mdash; Products by Brand</div>
    <div class="donut-grid" id="donut-charts"></div>
  </div>

  <div class="section" id="compare-section" style="display:none">
    <div class="section-title">&#128202; Brand Price Trends &mdash; Compare Over Time</div>
    <div id="compare-selectors"></div>
    <div class="compare-grid" id="compare-charts"></div>
  </div>
</div>

<!-- ── Intelligence ──────────────────────────────────────────────────────────── -->
<div id="tab-intelligence" class="tab-content">

  <div class="section">
    <div class="section-title">&#127775; Near All-Time Low &mdash; Within 10%
      <span class="count-badge" id="atl-badge"></span>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Brand</th><th>Model</th><th>Category</th>
          <th>Current &euro;</th><th>ATL &euro;</th><th>Above ATL</th><th>Proximity</th>
        </tr></thead>
        <tbody id="atl-body"></tbody>
      </table>
    </div>
  </div>

  <div class="section">
    <div class="section-title">&#127380; Brand Discount Frequency &mdash; Last 90 Days</div>
    <div class="intel-grid" id="discount-freq-charts"></div>
  </div>

  <div class="section">
    <div class="section-title">&#128203; Price Tier Distribution</div>
    <div class="intel-grid" id="tier-dist-charts"></div>
  </div>

  <div class="section">
    <div class="section-title">&#11088; Price vs Rating</div>
    <div class="scatter-legend" id="scatter-legend"></div>
    <div style="max-height:400px;position:relative"><canvas id="price-rating-chart"></canvas></div>
  </div>


</div>

</div><!-- /container -->

<!-- History modal -->
<div class="modal-overlay" id="modal" onclick="if(event.target===this)closeModal()">
  <div class="modal">
    <div class="modal-header">
      <h3 id="modal-title"></h3>
      <button class="modal-close" onclick="closeModal()">&#x2715;</button>
    </div>
    <div class="hist-stats" id="hist-stats"></div>
    <canvas id="history-chart" height="120"></canvas>
    <div class="no-data" id="no-history" style="display:none">No price history available for this product.</div>
  </div>
</div>

<script>
// ── Embedded data ──────────────────────────────────────────────────────────────
const DATA    = __DATA_JSON__;
const HISTORY = __HISTORY_JSON__;
const CHARTS  = __CHARTS_JSON__;

// ── Helpers ────────────────────────────────────────────────────────────────────
const PALETTE   = ['#4f8ef7','#22c55e','#f59e0b','#ef4444','#a78bfa','#38bdf8','#fb923c','#e879f9','#34d399','#f472b6'];
const CAT_LABEL = {phone:'Phones',laptop:'Laptops',smartwatch:'Smartwatches',tablet:'Tablets'};

function catBadge(cat) {
  const cls = { phone:'cat-phone', laptop:'cat-laptop', smartwatch:'cat-smartwatch', tablet:'cat-tablet' }[cat] || '';
  return `<span class="cat-badge ${cls}">${cat || '—'}</span>`;
}

// ── Tab switching ──────────────────────────────────────────────────────────────
function showTab(btn) {
  document.querySelectorAll('.tab-content').forEach(e => e.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(e => e.classList.remove('active'));
  document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
  btn.classList.add('active');
  history.replaceState(null, '', '#' + btn.dataset.tab);
}

// ── Stat cards ─────────────────────────────────────────────────────────────────
function buildStats() {
  const bigDrop = DATA.drops.length ? DATA.drops[0] : null;
  const cards = [
    { val: DATA.total_products.toLocaleString(), lbl: 'Products Tracked' },
    { val: DATA.total_snapshots.toLocaleString(), lbl: 'Price Snapshots' },
    { val: DATA.last_updated, lbl: 'Last Updated' },
    { val: DATA.drops.length, lbl: 'Drops Today', cls: DATA.drops.length ? 'highlight' : '' },
    { val: DATA.new_products.length, lbl: 'New This Week', cls: DATA.new_products.length ? 'success' : '' },
  ];
  for (const [cat, info] of Object.entries(DATA.by_category)) {
    const label = (CAT_LABEL[cat]||cat);
    cards.push({ val: info.count.toLocaleString(), lbl: label + ' Today' });
    cards.push({ val: '€' + info.avg_price.toFixed(0), lbl: 'Avg ' + label + ' Price' });
  }
  if (bigDrop) {
    cards.push({
      val: `-€${Math.abs(bigDrop.drop_eur).toFixed(0)}`,
      lbl: 'Biggest Drop · ' + (bigDrop.brand || ''),
      cls: 'highlight',
    });
  }
  document.getElementById('stat-cards').innerHTML = cards.map(c =>
    `<div class="stat-card ${c.cls||''}"><div class="val">${c.val}</div><div class="lbl">${c.lbl}</div></div>`
  ).join('');
}

// ── Trend chart images ─────────────────────────────────────────────────────────
function buildTrendCharts() {
  const labels = {
    price_trend_phone:'Phones', price_trend_laptop:'Laptops',
    price_trend_smartwatch:'Smartwatches', price_trend_tablet:'Tablets',
  };
  document.getElementById('trend-charts').innerHTML = Object.entries(CHARTS)
    .filter(([,src]) => src)
    .map(([k, src]) => `<div class="chart-card">
      <img src="${src}" alt="${labels[k]||k}" loading="lazy"/>
      <div class="chart-lbl">${labels[k]||k}</div>
    </div>`).join('');
}

// ── Drops table ────────────────────────────────────────────────────────────────
let dropsView = 'today';
let dropsSortCol = 'drop_eur', dropsSortDir = 1;

function showDropsView(view, btn) {
  dropsView = view;
  document.querySelectorAll('.sub-tab-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  buildDropsTable();
}

function sortDropsBy(col) {
  dropsSortDir = (dropsSortCol === col) ? -dropsSortDir : 1;
  dropsSortCol = col;
  buildDropsTable();
}

function buildDropsTable() {
  const data   = dropsView === 'today' ? DATA.drops : DATA.weekly_drops;
  const showDate = dropsView === 'week';
  const label  = dropsView === 'today' ? "🔻 Today's Price Drops" : "🗓️ This Week's Best Deals";
  document.getElementById('drops-title').innerHTML = `${label} <span class="count-badge">${data.length}</span>`;

  const cols = [
    { key:'brand',      lbl:'Brand',    sort:true },
    { key:'model',      lbl:'Model',    sort:false },
    { key:'category',   lbl:'Category', sort:true },
    { key:'prev_price', lbl:'Was €',  sort:true },
    { key:'new_price',  lbl:'Now €',  sort:true },
    { key:'drop_eur',   lbl:'Drop €', sort:true },
    { key:'drop_pct',   lbl:'Drop %',   sort:true },
    ...(showDate ? [{ key:'drop_date', lbl:'Date', sort:true }] : []),
  ];
  document.getElementById('drops-thead').innerHTML = cols.map(c => {
    const sorted = c.key === dropsSortCol;
    const arrow  = sorted ? (dropsSortDir === 1 ? ' ↑' : ' ↓') : '';
    return `<th class="${c.sort ? 'sortable' : ''}${sorted ? ' sorted' : ''}"
               ${c.sort ? `onclick="sortDropsBy('${c.key}')"` : ''}>${c.lbl}${arrow}</th>`;
  }).join('');

  const sorted = [...data].sort((a, b) => {
    const av = a[dropsSortCol], bv = b[dropsSortCol];
    if (av == null) return 1; if (bv == null) return -1;
    return (av < bv ? -1 : av > bv ? 1 : 0) * dropsSortDir;
  });

  const maxDrop = sorted.length ? Math.abs(sorted[0].drop_eur || 0) : 1;
  const tb = document.getElementById('drops-body');
  if (!sorted.length) {
    tb.innerHTML = `<tr><td colspan="${cols.length}" class="no-data">No drops recorded.</td></tr>`;
    return;
  }
  tb.innerHTML = sorted.map(d => {
    const barW = Math.round(Math.min(56, Math.abs(d.drop_eur || 0) / maxDrop * 56));
    return `<tr>
      <td>${d.brand||''}</td>
      <td><a href="${d.skroutz_link}" target="_blank">${(d.model||d.brand||'').slice(0,44)}</a></td>
      <td>${catBadge(d.category)}</td>
      <td class="price muted">€${(d.prev_price||0).toFixed(2)}</td>
      <td class="price">€${(d.new_price||0).toFixed(2)}</td>
      <td class="drop-eur"><div class="drop-bar"><div class="drop-bar-fill" style="width:${barW}px"></div>-€${Math.abs(d.drop_eur||0).toFixed(2)}</div></td>
      <td class="drop-pct">-${Math.abs(d.drop_pct||0).toFixed(1)}%</td>
      ${showDate ? `<td class="muted">${d.drop_date||''}</td>` : ''}
    </tr>`;
  }).join('');
}

// ── Product search ─────────────────────────────────────────────────────────────
let filteredProds = DATA.products.slice(0, 100);
let prodSortCol = 'reviews', prodSortDir = -1;

// Populate color dropdown once on load
(function populateColors() {
  const colors = [...new Set(DATA.products.map(p => p.color).filter(Boolean))].sort();
  const sel = document.getElementById('color-filter');
  colors.forEach(c => {
    const o = document.createElement('option');
    o.value = c; o.textContent = c;
    sel.appendChild(o);
  });
})();

function sortProducts(col) {
  document.querySelectorAll('[id^="sarr-"]').forEach(e => e.textContent = '');
  document.querySelectorAll('th.sorted').forEach(e => e.classList.remove('sorted'));
  prodSortDir = (prodSortCol === col) ? -prodSortDir : -1;
  prodSortCol = col;
  const el = document.getElementById('sarr-' + col);
  if (el) {
    el.textContent = prodSortDir === -1 ? ' ↓' : ' ↑';
    el.closest('th').classList.add('sorted');
  }
  filterProducts();
}

function filterProducts() {
  const q         = document.getElementById('q').value.toLowerCase().trim();
  const cat       = document.getElementById('cat-filter').value;
  const color     = document.getElementById('color-filter').value;
  const mn        = parseFloat(document.getElementById('price-min').value) || 0;
  const mx        = parseFloat(document.getElementById('price-max').value) || Infinity;
  const financing = document.getElementById('chk-financing').checked;
  const stable    = document.getElementById('chk-stable').checked;
  const trend     = document.getElementById('trend-filter').value;

  let result = DATA.products.filter(p => {
    if (cat && p.category !== cat) return false;
    if (color && p.color !== color) return false;
    if (trend && p.trend !== trend) return false;
    if (p.price != null && p.price < mn) return false;
    if (p.price != null && p.price > mx) return false;
    if (financing && !p.monthly) return false;
    if (stable && p.cv >= 5) return false;
    if (q) return (p.brand + ' ' + p.model + ' ' + p.name).toLowerCase().includes(q);
    return true;
  });
  result.sort((a, b) => {
    const av = a[prodSortCol], bv = b[prodSortCol];
    if (av == null) return 1; if (bv == null) return -1;
    return (av < bv ? -1 : av > bv ? 1 : 0) * prodSortDir;
  });
  filteredProds = result.slice(0, 300);
  renderProductTable();
}

function volBadge(cv) {
  if (cv < 5)  return `<span class="vol-badge vol-stable">STABLE</span>`;
  if (cv >= 15) return `<span class="vol-badge vol-volatile">VOLATILE</span>`;
  return '';
}

function renderProductTable() {
  document.getElementById('search-count').textContent = filteredProds.length + ' results';
  const tb = document.getElementById('products-body');
  if (!filteredProds.length) {
    tb.innerHTML = '<tr><td colspan="10" class="no-data">No products found.</td></tr>';
    return;
  }
  tb.innerHTML = filteredProds.map(p => {
    const name    = (p.model || p.name).slice(0, 48);
    const rating  = p.rating  != null ? '★ ' + p.rating.toFixed(1) : '—';
    const reviews = p.reviews != null ? p.reviews.toLocaleString()  : '—';
    const monthly = p.monthly ? `€${p.monthly.toFixed(0)}/mo` : '—';
    const color   = p.color ? `<span class="color-tag">${p.color}</span>` : '—';
    let priceHtml = p.price != null ? '€' + p.price.toFixed(2) : '—';
    const hist = HISTORY[p.id];
    if (hist && hist.length > 1 && p.price != null) {
      const atl = Math.min(...hist.map(h => h.price));
      if (p.price <= atl * 1.03) priceHtml += '<span class="atl-badge">ATL</span>';
    }
    return `<tr>
      <td>${p.brand}</td>
      <td><a href="${p.url}" target="_blank">${name}</a></td>
      <td>${catBadge(p.category)}</td>
      <td class="price">${priceHtml}</td>
      <td>${rating}</td>
      <td>${reviews}</td>
      <td class="muted">${monthly}</td>
      <td>${color}</td>
      <td>${volBadge(p.cv)}</td>
      <td><button onclick="showHistory(${p.id})"
            style="background:var(--accent);color:#fff;border:none;border-radius:6px;
                   padding:3px 10px;cursor:pointer;font-size:11px">Chart</button></td>
    </tr>`;
  }).join('');
}

// ── New & Gone ─────────────────────────────────────────────────────────────────
function buildNewGone() {
  // New arrivals
  document.getElementById('new-badge').textContent = DATA.new_products.length;
  const nb = document.getElementById('new-body');
  if (!DATA.new_products.length) {
    nb.innerHTML = '<tr><td colspan="6" class="no-data">No new products this week.</td></tr>';
  } else {
    nb.innerHTML = DATA.new_products.map(p => `<tr>
      <td>${p.brand}</td>
      <td><a href="${p.url}" target="_blank">${(p.model||p.name).slice(0,48)}</a></td>
      <td>${catBadge(p.category)}</td>
      <td class="price">${p.price != null ? '€'+p.price.toFixed(2) : '—'}</td>
      <td class="muted">${p.first_seen}</td>
      <td class="muted">${p.reviews != null ? p.reviews.toLocaleString() : '—'}</td>
    </tr>`).join('');
  }

  // Disappeared
  document.getElementById('gone-badge').textContent = DATA.disappeared.length;
  const gb = document.getElementById('gone-body');
  if (!DATA.disappeared.length) {
    gb.innerHTML = '<tr><td colspan="5" class="no-data">No products disappeared recently.</td></tr>';
  } else {
    gb.innerHTML = DATA.disappeared.map(p => `<tr>
      <td>${p.brand}</td>
      <td><a href="${p.url}" target="_blank">${p.model.slice(0,48)}</a></td>
      <td>${catBadge(p.category)}</td>
      <td class="muted">${p.last_seen}</td>
      <td class="muted">${p.days_gone}d</td>
    </tr>`).join('');
  }

  // Watchlist
  const wl = DATA.watchlist;
  if (wl && wl.length) {
    document.getElementById('watchlist-section').style.display = 'block';
    const hits = wl.filter(w => w.hit).length;
    document.getElementById('wl-badge').textContent = hits ? `${wl.length} · ${hits} hit` : wl.length;
    document.getElementById('wl-list').innerHTML = wl.map(w => {
      const priceTxt = w.price != null ? `€${w.price.toFixed(2)}` : '—';
      const badge    = w.hit ? '<span class="wl-hit-badge">TARGET REACHED</span>' : '';
      return `<div class="wl-item ${w.hit ? 'hit' : ''}">
        <div>
          <div class="wl-name">${w.label}</div>
          <div class="wl-meta">${catBadge(w.category)} &nbsp;<a href="${w.url}" target="_blank">View on Skroutz ↗</a></div>
        </div>
        <div style="display:flex;align-items:center;gap:12px">
          ${badge}
          <div class="wl-prices">
            <div class="wl-current ${w.hit ? 'hit' : 'above'}">${priceTxt}</div>
            <div class="wl-target">Target: €${w.threshold.toFixed(2)}</div>
          </div>
        </div>
      </div>`;
    }).join('');
  }
}

// ── Brand charts (Insights) ────────────────────────────────────────────────────
let brandMetric = 'avg';
let brandInst   = [];

function setBrandMetric(btn) {
  brandMetric = btn.dataset.metric;
  document.querySelectorAll('.metric-btn').forEach(b => b.classList.toggle('active', b === btn));
  buildBrandCharts();
}

function buildBrandCharts() {
  const el = document.getElementById('brand-charts');
  brandInst.forEach(c => c.destroy());
  brandInst = [];

  // Build all canvases first so DOM is ready
  el.innerHTML = Object.entries(DATA.brand_data).filter(([,b]) => b.length).map(([cat, brands]) =>
    `<div class="brand-card"><h4>${CAT_LABEL[cat]||cat}</h4><canvas id="bc-${cat}"></canvas></div>`
  ).join('');

  for (const [cat, brands] of Object.entries(DATA.brand_data)) {
    if (!brands.length) continue;
    const ctx = document.getElementById('bc-' + cat)?.getContext('2d');
    if (!ctx) continue;

    let datasets, yFmt, ttFmt;

    if (brandMetric === 'count') {
      datasets = [{ label:'Products', data:brands.map(b=>b.product_count),
                    backgroundColor:PALETTE, borderRadius:4 }];
      yFmt  = v => v;
      ttFmt = c => ' ' + c.raw + ' products';
    } else if (brandMetric === 'range') {
      datasets = [{ label:'Price Range', data:brands.map(b=>[b.min_price, b.max_price]),
                    backgroundColor:PALETTE.map(c=>c+'99'), borderColor:PALETTE,
                    borderWidth:2, borderRadius:4 }];
      yFmt  = v => '€' + v;
      ttFmt = c => ` €${c.raw[0]}–€${c.raw[1]}`;
    } else {
      const field = brandMetric === 'median' ? 'median_price' : 'avg_price';
      const lbl   = brandMetric === 'median' ? 'Median €' : 'Avg €';
      datasets = [{ label:lbl, data:brands.map(b=>b[field]),
                    backgroundColor:PALETTE, borderRadius:4 }];
      yFmt  = v => '€' + v;
      ttFmt = c => ' €' + c.raw.toFixed(0);
    }

    brandInst.push(new Chart(ctx, {
      type: 'bar',
      data: { labels: brands.map(b => b.brand), datasets },
      options: {
        responsive: true,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: ttFmt } },
        },
        scales: {
          x: { ticks:{ color:'#64748b', font:{size:10} }, grid:{ color:'#2a2d3a' } },
          y: { ticks:{ color:'#64748b', callback:yFmt  }, grid:{ color:'#2a2d3a' } },
        },
      },
    }));
  }
}

// ── History modal ──────────────────────────────────────────────────────────────
let histInst = null;

function showHistory(id) {
  // Look up the product from the embedded data — avoids injecting arbitrary strings into onclick attributes
  const _p  = DATA.products.find(p => p.id === id) || {};
  const name = ((_p.brand || '') + ' ' + (_p.model || _p.name || '')).trim();
  const url  = _p.url || '';
  // Sanitize before inserting as innerHTML
  const safeName = name.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  document.getElementById('modal-title').innerHTML = `<a href="${url}" target="_blank">${safeName}</a>`;
  const pts = HISTORY[id] || [];

  if (!pts.length) {
    document.getElementById('hist-stats').innerHTML   = '';
    document.getElementById('no-history').style.display = 'block';
    document.getElementById('history-chart').style.display = 'none';
    document.getElementById('modal').classList.add('open');
    return;
  }
  document.getElementById('no-history').style.display    = 'none';
  document.getElementById('history-chart').style.display = 'block';

  const prices  = pts.map(p => p.price);
  const minP    = Math.min(...prices);
  const maxP    = Math.max(...prices);
  const avgP    = prices.reduce((a,b) => a+b, 0) / prices.length;
  const firstP  = prices[0];
  const currP   = prices[prices.length - 1];
  const chgPct  = (currP - firstP) / firstP * 100;
  const chgCls  = chgPct < -0.05 ? 'drop' : chgPct > 0.05 ? 'rise' : '';
  const chgSign = chgPct > 0 ? '+' : '';

  document.getElementById('hist-stats').innerHTML = [
    { v:`€${currP.toFixed(2)}`,  l:'Current' },
    { v:`€${minP.toFixed(2)}`,   l:'All-time Low',  cls:'drop' },
    { v:`€${maxP.toFixed(2)}`,   l:'All-time High' },
    { v:`€${avgP.toFixed(2)}`,   l:'Average' },
    { v:`${chgSign}${chgPct.toFixed(1)}%`, l:`vs First (€${firstP.toFixed(2)})`, cls:chgCls },
    { v:`${pts.length}d`,             l:'Data Points' },
  ].map(s => `<div class="hist-stat">
    <div class="hs-val ${s.cls||''}">${s.v}</div>
    <div class="hs-lbl">${s.l}</div>
  </div>`).join('');

  if (histInst) histInst.destroy();
  histInst = new Chart(document.getElementById('history-chart').getContext('2d'), {
    type: 'line',
    data: {
      labels: pts.map(p => p.date),
      datasets: [{
        label:'Price €', data:pts.map(p => p.price),
        borderColor:'#4f8ef7', backgroundColor:'#4f8ef718',
        fill:true, tension:0.3,
        pointRadius: pts.length > 30 ? 0 : 3, pointHoverRadius:4,
      }, {
        label:'All-time Low', data:pts.map(() => minP),
        borderColor:'#ef444660', backgroundColor:'transparent',
        borderDash:[6,3], pointRadius:0, fill:false, tension:0,
      }],
    },
    options: {
      responsive: true,
      interaction: { mode:'index', intersect:false },
      plugins: {
        legend: { display:false },
        tooltip: { callbacks: { label:c => ' €'+c.raw.toFixed(2) } },
      },
      scales: {
        x: { ticks:{ color:'#64748b', maxTicksLimit:8, font:{size:11} }, grid:{ color:'#2a2d3a' } },
        y: { ticks:{ color:'#64748b', callback:v => '€'+v }, grid:{ color:'#2a2d3a' } },
      },
    },
  });
  document.getElementById('modal').classList.add('open');
}

function closeModal() { document.getElementById('modal').classList.remove('open'); }
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeModal();
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  const tabMap = {'1':'overview','2':'drops','3':'products','4':'new-gone','5':'insights','6':'intelligence'};
  if (tabMap[e.key]) { const btn = document.querySelector(`.tab-btn[data-tab="${tabMap[e.key]}"]`); if (btn) showTab(btn); }
});

// ── Hot Deals ──────────────────────────────────────────────────────────────────
function buildHotDeals() {
  const deals = DATA.hot_deals || [];
  if (!deals.length) return;
  document.getElementById('hot-deals-section').style.display = 'block';
  document.getElementById('hot-badge').textContent = deals.length;
  const maxScore = deals.reduce((m, d) => Math.max(m, d.score), 1);
  document.getElementById('hot-body').innerHTML = deals.map(d => {
    const barW = Math.round(d.score / maxScore * 60);
    return `<tr>
      <td>${d.brand}</td>
      <td><a href="${d.url}" target="_blank">${(d.model).slice(0,44)}</a></td>
      <td>${catBadge(d.category)}</td>
      <td class="price muted">€${(d.price_prev||0).toFixed(2)}</td>
      <td class="price">€${(d.price_now||0).toFixed(2)}</td>
      <td class="drop-pct">${d.chg_pct.toFixed(1)}%</td>
      <td style="color:var(--rise)">+${d.new_rev} &#9733;</td>
      <td><div class="score-bar-wrap"><div class="score-bar" style="width:${barW}px"></div>${d.score.toFixed(1)}</div></td>
    </tr>`;
  }).join('');
}

// ── Market Share donuts ────────────────────────────────────────────────────────
let donutInst = [];
function buildMarketShare() {
  const el = document.getElementById('donut-charts');
  donutInst.forEach(c => c.destroy());
  donutInst = [];

  const DONUT_PALETTE = ['#4f8ef7','#a78bfa','#22c55e','#f59e0b','#ef4444','#38bdf8','#fb923c'];

  el.innerHTML = Object.entries(DATA.brand_data)
    .filter(([,brands]) => brands.length)
    .map(([cat]) => `<div class="donut-card"><h4>${CAT_LABEL[cat]||cat}</h4><canvas id="dn-${cat}" height="180"></canvas></div>`)
    .join('');

  for (const [cat, brands] of Object.entries(DATA.brand_data)) {
    if (!brands.length) continue;
    const ctx = document.getElementById('dn-' + cat)?.getContext('2d');
    if (!ctx) continue;
    const top6    = brands.slice(0, 6);
    const otherCt = brands.slice(6).reduce((s, b) => s + b.product_count, 0);
    const labels  = [...top6.map(b => b.brand), ...(otherCt ? ['Other'] : [])];
    const counts  = [...top6.map(b => b.product_count), ...(otherCt ? [otherCt] : [])];
    donutInst.push(new Chart(ctx, {
      type: 'doughnut',
      data: { labels, datasets: [{ data: counts, backgroundColor: DONUT_PALETTE, borderWidth: 1, borderColor: '#1a1d27' }] },
      options: {
        cutout: '62%',
        plugins: {
          legend: { position:'bottom', labels:{ color:'#64748b', font:{size:10}, boxWidth:10, padding:8 } },
          tooltip: { callbacks: { label: c => ` ${c.label}: ${c.raw} products` } },
        },
      },
    }));
  }
}

// ── Brand Comparison line charts ───────────────────────────────────────────────
let compareInst = [];
const compareSelected = {};  // cat -> Set of selected brands

function buildBrandComparison() {
  const trend = DATA.brand_trend || {};
  if (!Object.keys(trend).length) return;
  document.getElementById('compare-section').style.display = 'block';

  // Init selection per category (default top 3)
  for (const [cat, brands] of Object.entries(trend)) {
    if (!compareSelected[cat]) {
      compareSelected[cat] = new Set(Object.keys(brands).slice(0, 3));
    }
  }

  renderCompareSelectors();
  renderCompareCharts();
}

function renderCompareSelectors() {
  const trend = DATA.brand_trend || {};
  document.getElementById('compare-selectors').innerHTML = Object.entries(trend).map(([cat, brands]) => {
    const title = (CAT_LABEL[cat]||cat);
    const btns  = Object.keys(brands).map(brand => {
      const sel = compareSelected[cat]?.has(brand) ? 'sel' : '';
      return `<button class="brand-chk-btn ${sel}" data-cat="${cat}" data-brand="${brand}"
                onclick="toggleCompareBrand(this)">${brand}</button>`;
    }).join('');
    return `<div style="margin-bottom:10px">
      <div style="font-size:11px;color:var(--muted);margin-bottom:6px">${title}</div>
      <div class="brand-selector">${btns}</div>
    </div>`;
  }).join('');
}

function toggleCompareBrand(btn) {
  const cat   = btn.dataset.cat;
  const brand = btn.dataset.brand;
  if (!compareSelected[cat]) compareSelected[cat] = new Set();
  if (compareSelected[cat].has(brand)) {
    if (compareSelected[cat].size <= 1) return;
    compareSelected[cat].delete(brand);
    btn.classList.remove('sel');
  } else {
    compareSelected[cat].add(brand);
    btn.classList.add('sel');
  }
  renderCompareCharts();
}

function renderCompareCharts() {
  const trend = DATA.brand_trend || {};
  const el    = document.getElementById('compare-charts');
  compareInst.forEach(c => c.destroy());
  compareInst = [];

  el.innerHTML = Object.entries(trend)
    .filter(([,brands]) => Object.keys(brands).length)
    .map(([cat]) => `<div class="compare-card"><h4>${CAT_LABEL[cat]||cat} &mdash; Avg Price Trend</h4><canvas id="cmp-${cat}" height="160"></canvas></div>`)
    .join('');

  for (const [cat, brands] of Object.entries(trend)) {
    const ctx = document.getElementById('cmp-' + cat)?.getContext('2d');
    if (!ctx) continue;
    const sel = compareSelected[cat] || new Set();
    const allDates = [...new Set(Object.values(brands).flat().map(p => p.date))].sort();

    const datasets = Object.entries(brands)
      .filter(([brand]) => sel.has(brand))
      .map(([brand, pts], i) => {
        const byDate = Object.fromEntries(pts.map(p => [p.date, p.price]));
        return {
          label: brand,
          data:  allDates.map(d => byDate[d] ?? null),
          borderColor: PALETTE[i % PALETTE.length],
          backgroundColor: 'transparent',
          tension: 0.3, pointRadius: allDates.length > 20 ? 0 : 3,
          spanGaps: true,
        };
      });

    compareInst.push(new Chart(ctx, {
      type: 'line',
      data: { labels: allDates, datasets },
      options: {
        responsive: true,
        interaction: { mode:'index', intersect:false },
        plugins: {
          legend: { labels:{ color:'#94a3b8', font:{size:10}, boxWidth:12 } },
          tooltip: { callbacks: { label: c => ` ${c.dataset.label}: €${c.raw?.toFixed(2) ?? '—'}` } },
        },
        scales: {
          x: { ticks:{ color:'#64748b', maxTicksLimit:6, font:{size:10} }, grid:{ color:'#2a2d3a' } },
          y: { ticks:{ color:'#64748b', callback:v => '€'+v }, grid:{ color:'#2a2d3a' } },
        },
      },
    }));
  }
}

// ── Intelligence tab ──────────────────────────────────────────────────────────
let discountFreqInst = [], tierDistInst = [], priceRatingInst = null;

function buildIntelligence() {
  buildNearATL();
  buildDiscountFreqCharts();
  buildPriceTierDist();
  buildPriceVsRating();
}

function buildNearATL() {
  const near = DATA.products
    .filter(p => p.floor_pct != null && p.floor_pct <= 10 && p.price != null)
    .sort((a, b) => a.floor_pct - b.floor_pct)
    .slice(0, 60);
  document.getElementById('atl-badge').textContent = near.length;
  const el = document.getElementById('tc-intel');
  if (el && near.length) el.textContent = near.length;
  const tb = document.getElementById('atl-body');
  if (!near.length) {
    tb.innerHTML = '<tr><td colspan="7" class="no-data">No products near ATL right now — run analytics.sql v2 to enable.</td></tr>';
    return;
  }
  tb.innerHTML = near.map(p => {
    const pct      = p.floor_pct.toFixed(1);
    const barPct   = Math.min(100, p.floor_pct / 10 * 100);
    const barColor = p.floor_pct <= 2 ? 'var(--rise)' : p.floor_pct <= 5 ? '#f59e0b' : 'var(--muted)';
    const name     = (p.model || p.name).slice(0, 44);
    return `<tr>
      <td>${p.brand}</td>
      <td><a href="${p.url}" target="_blank">${name}</a></td>
      <td>${catBadge(p.category)}</td>
      <td class="price">&euro;${p.price.toFixed(2)}</td>
      <td class="muted">&euro;${p.atl.toFixed(2)}</td>
      <td style="color:${barColor};font-weight:600">+${pct}%</td>
      <td><div class="atl-bar-wrap">
        <div class="atl-bar-bg"><div class="atl-bar-fill" style="width:${barPct}%;background:${barColor}"></div></div>
      </div></td>
    </tr>`;
  }).join('');
}

function buildDiscountFreqCharts() {
  const el = document.getElementById('discount-freq-charts');
  discountFreqInst.forEach(c => c.destroy());
  discountFreqInst = [];
  const data = DATA.discount_data || {};
  if (!Object.keys(data).length) {
    el.innerHTML = '<p class="no-data" style="padding:20px">No discount data yet — run analytics.sql v2 and accumulate 90 days of data.</p>';
    return;
  }
  el.innerHTML = Object.entries(data)
    .filter(([,brands]) => brands.length)
    .map(([cat]) =>
      `<div class="intel-card"><h4>${CAT_LABEL[cat]||cat} &mdash; % days with &ge;3% drop</h4><canvas id="dfc-${cat}"></canvas></div>`
    ).join('');
  for (const [cat, brands] of Object.entries(data)) {
    if (!brands.length) continue;
    const ctx = document.getElementById('dfc-' + cat)?.getContext('2d');
    if (!ctx) continue;
    const sorted = [...brands].sort((a, b) => b.freq_pct - a.freq_pct).slice(0, 10);
    discountFreqInst.push(new Chart(ctx, {
      type: 'bar',
      data: {
        labels: sorted.map(b => b.brand),
        datasets: [{ label:'Discount Freq %', data: sorted.map(b => b.freq_pct),
                     backgroundColor: sorted.map((_, i) => PALETTE[i % PALETTE.length]), borderRadius: 4 }],
      },
      options: {
        indexAxis: 'y', responsive: true,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: c => ` ${c.raw.toFixed(1)}% of days had a drop` } },
        },
        scales: {
          x: { max: 100, ticks:{ color:'#64748b', callback: v => v+'%' }, grid:{ color:'#2a2d3a' } },
          y: { ticks:{ color:'#64748b', font:{size:10} }, grid:{ display:false } },
        },
      },
    }));
  }
}

function buildPriceTierDist() {
  const el = document.getElementById('tier-dist-charts');
  tierDistInst.forEach(c => c.destroy());
  tierDistInst = [];
  const TIERS = [
    { label:'<€200',     min:0,    max:200 },
    { label:'€200–500',  min:200,  max:500 },
    { label:'€500–1k',   min:500,  max:1000 },
    { label:'€1k–2k',    min:1000, max:2000 },
    { label:'>€2000',    min:2000, max:Infinity },
  ];
  const TIER_COLORS = ['#22c55e','#4f8ef7','#f59e0b','#a78bfa','#ef4444'];
  const cats = ['phone','laptop','smartwatch','tablet'];
  el.innerHTML = cats.map(cat =>
    `<div class="intel-card"><h4>${CAT_LABEL[cat]||cat}</h4><canvas id="td-${cat}"></canvas></div>`
  ).join('');
  for (const cat of cats) {
    const ctx = document.getElementById('td-' + cat)?.getContext('2d');
    if (!ctx) continue;
    const counts = TIERS.map(t =>
      DATA.products.filter(p => p.category === cat && p.price != null && p.price >= t.min && p.price < t.max).length
    );
    tierDistInst.push(new Chart(ctx, {
      type: 'bar',
      data: {
        labels: TIERS.map(t => t.label),
        datasets: [{ label:'Products', data: counts, backgroundColor: TIER_COLORS, borderRadius: 4 }],
      },
      options: {
        responsive: true,
        plugins: {
          legend: { display:false },
          tooltip: { callbacks: { label: c => ` ${c.raw} products` } },
        },
        scales: {
          x: { ticks:{ color:'#64748b', font:{size:10} }, grid:{ color:'#2a2d3a' } },
          y: { ticks:{ color:'#64748b' }, grid:{ color:'#2a2d3a' } },
        },
      },
    }));
  }
}

function buildPriceVsRating() {
  if (priceRatingInst) { priceRatingInst.destroy(); priceRatingInst = null; }
  const catColors = { phone:'#3b82f6', laptop:'#a78bfa', smartwatch:'#22c55e', tablet:'#f59e0b' };
  const cats = ['phone','laptop','smartwatch','tablet'];
  document.getElementById('scatter-legend').innerHTML = cats.map(cat =>
    `<div class="scatter-legend-item">
       <div class="scatter-dot" style="background:${catColors[cat]}"></div>
       ${CAT_LABEL[cat]||cat}
     </div>`
  ).join('');
  const datasets = cats.map(cat => ({
    label: (CAT_LABEL[cat]||cat),
    data: DATA.products
      .filter(p => p.category === cat && p.price != null && p.rating != null && p.rating > 0)
      .map(p => ({ x: p.price, y: p.rating })),
    backgroundColor: catColors[cat] + '99',
    borderColor: catColors[cat],
    borderWidth: 1, pointRadius: 4, pointHoverRadius: 6,
  }));
  priceRatingInst = new Chart(
    document.getElementById('price-rating-chart').getContext('2d'), {
      type: 'scatter',
      data: { datasets },
      options: {
        responsive: true,
        plugins: {
          legend: { display:false },
          tooltip: { callbacks: { label: c => `€${c.parsed.x.toFixed(0)} · ★${c.parsed.y.toFixed(1)}` } },
        },
        scales: {
          x: { title:{ display:true, text:'Price (€)', color:'#64748b' }, ticks:{ color:'#64748b', callback:v=>'€'+v }, grid:{ color:'#2a2d3a' } },
          y: { title:{ display:true, text:'Rating', color:'#64748b' }, ticks:{ color:'#64748b' }, grid:{ color:'#2a2d3a' }, min:0, max:5 },
        },
      },
    }
  );
}

let marketIndexInst = null;
function buildMarketIndex() {
  if (marketIndexInst) { marketIndexInst.destroy(); marketIndexInst = null; }
  const idx = DATA.market_index || {};
  if (!Object.keys(idx).length) return;
  const allDates = [...new Set(Object.values(idx).flat().map(p => p.date))].sort();
  const CAT_COLORS = { phone:'#3b82f6', laptop:'#a78bfa', smartwatch:'#22c55e', tablet:'#f59e0b' };
  const datasets = Object.entries(idx).map(([cat, pts]) => {
    const byDate = Object.fromEntries(pts.map(p => [p.date, p.avg]));
    return {
      label: (CAT_LABEL[cat]||cat),
      data:  allDates.map(d => byDate[d] ?? null),
      borderColor:     CAT_COLORS[cat] || '#4f8ef7',
      backgroundColor: 'transparent',
      tension: 0.3,
      pointRadius: allDates.length > 30 ? 0 : 3,
      spanGaps: true,
    };
  });
  marketIndexInst = new Chart(
    document.getElementById('market-index-chart').getContext('2d'), {
      type: 'line',
      data: { labels: allDates, datasets },
      options: {
        responsive: true,
        interaction: { mode:'index', intersect:false },
        plugins: {
          legend: { labels:{ color:'#94a3b8', font:{size:11}, boxWidth:12 } },
          tooltip: { callbacks: { label: c => ` ${c.dataset.label}: €${c.raw?.toFixed(2) ?? '—'}` } },
        },
        scales: {
          x: { ticks:{ color:'#64748b', maxTicksLimit:8, font:{size:11} }, grid:{ color:'#2a2d3a' } },
          y: { ticks:{ color:'#64748b', callback: v => '€'+v }, grid:{ color:'#2a2d3a' } },
        },
      },
    }
  );
}

// ── Tab badge counts ───────────────────────────────────────────────────────────
function updateTabBadges() {
  const drops = DATA.drops.length + (DATA.hot_deals?.length || 0);
  const el = document.getElementById('tc-drops');
  if (el && drops) el.textContent = drops;
  const pel = document.getElementById('tc-products');
  if (pel) pel.textContent = DATA.products.length.toLocaleString();
  const nel = document.getElementById('tc-new-gone');
  const newGone = DATA.new_products.length + DATA.disappeared.length;
  if (nel && newGone) nel.textContent = newGone;
}

// ── CSV export ─────────────────────────────────────────────────────────────────
function exportCSV() {
  const cols   = ['brand','model','category','price','rating','reviews','cv','trend','url'];
  const header = ['Brand','Model','Category','Price (EUR)','Rating','Reviews','CV%','Trend','Link'];
  const rows   = filteredProds.map(p => cols.map(c => {
    const v = p[c] ?? '';
    return typeof v === 'string' && (v.includes(',') || v.includes('"'))
      ? '"' + v.replace(/"/g,'""') + '"' : v;
  }));
  const csv = [header, ...rows].map(r => r.join(',')).join('\\n');
  const a   = document.createElement('a');
  a.href    = URL.createObjectURL(new Blob([csv], {type:'text/csv'}));
  a.download = 'skroutz_products.csv';
  a.click();
  URL.revokeObjectURL(a.href);
}

// ── Init ───────────────────────────────────────────────────────────────────────
buildStats();
buildTrendCharts();
buildMarketIndex();
buildDropsTable();
buildHotDeals();
filterProducts();
buildNewGone();
buildBrandCharts();
buildMarketShare();
buildBrandComparison();
buildIntelligence();
updateTabBadges();
(function restoreTab() {
  const hash = location.hash.slice(1);
  if (hash) { const btn = document.querySelector(`.tab-btn[data-tab="${hash}"]`); if (btn) showTab(btn); }
})();
</script>
</body>
</html>"""


def main():
    print("Generating dashboard…")
    try:
        engine = get_engine()
        with engine.connect() as conn:
            data = fetch_data(conn)
    except Exception as e:
        print(f"Dashboard: DB connection failed — {e}", file=sys.stderr)
        sys.exit(1)

    charts_json = {
        "price_trend_phone":      encode_chart("price_trend_phone.png"),
        "price_trend_laptop":     encode_chart("price_trend_laptop.png"),
        "price_trend_smartwatch": encode_chart("price_trend_smartwatch.png"),
        "price_trend_tablet":     encode_chart("price_trend_tablet.png"),
    }

    history = data.pop("history")

    html = (HTML_TEMPLATE
        .replace("__GENERATED__",        data["generated"])
        .replace("__TOTAL_PRODUCTS__",   f"{data['total_products']:,}")
        .replace("__TOTAL_SNAPSHOTS__",  f"{data['total_snapshots']:,}")
        .replace("__DATA_JSON__",        json.dumps(data,        ensure_ascii=False, separators=(",", ":")))
        .replace("__HISTORY_JSON__",     json.dumps(history,     ensure_ascii=False, separators=(",", ":")))
        .replace("__CHARTS_JSON__",      json.dumps(charts_json, ensure_ascii=False, separators=(",", ":")))
    )

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
