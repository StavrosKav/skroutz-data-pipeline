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
        LIMIT 3000
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
    items = [i for i in items if i.get("url", "").strip()]
    if not items:
        return []

    urls = [i["url"].strip() for i in items]
    try:
        db_rows = conn.execute(text(
            "SELECT brand, model, category, ROUND(price_eur, 2) AS price_eur, skroutz_link "
            "FROM vw_latest_prices WHERE skroutz_link = ANY(:urls)"
        ), {"urls": urls}).fetchall()
    except Exception:
        db_rows = []
    price_map = {r.skroutz_link: r for r in db_rows}

    result = []
    for item in items:
        url       = item["url"].strip()
        label     = item.get("label", url)
        threshold = float(item.get("threshold_eur", 0))
        row       = price_map.get(url)
        price     = float(row.price_eur) if row and row.price_eur else None
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


HTML_TEMPLATE_PATH = BASE / "templates" / "dashboard.html"
HTML_TEMPLATE = HTML_TEMPLATE_PATH.read_text(encoding="utf-8")


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
