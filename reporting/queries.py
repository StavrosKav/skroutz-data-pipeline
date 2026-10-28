"""
queries.py
----------
Shared read queries for the consumer layer (generate_dashboard.py,
charts_from_db.py). Each function takes a live connection
and returns a pandas DataFrame with raw (unrounded) numeric columns — callers
round/rename/limit to their own display needs.

Only genuinely duplicated query shapes live here. Queries that only look
similar but differ in joins or filtering semantics (e.g. product search,
watchlist lookups) are intentionally left in their consumer to avoid
changing behavior.
"""

import pandas as pd
from sqlalchemy import text


def category_snapshot(conn):
    """Per-category product count + min/avg/max price, latest snapshot date."""
    query = text("""
        SELECT p.category,
               COUNT(DISTINCT p.id) AS product_count,
               AVG(s.price_eur)     AS avg_price,
               MIN(s.price_eur)     AS min_price,
               MAX(s.price_eur)     AS max_price
        FROM products p
        JOIN price_snapshots s ON s.product_id = p.id
        WHERE s.date = (SELECT MAX(date) FROM price_snapshots)
        GROUP BY p.category ORDER BY p.category
    """)
    return pd.read_sql(query, conn)


def biggest_drops(conn, days_back, categories=None, limit=100):
    """Price drops with drop_date >= CURRENT_DATE - days_back, most negative drop_eur first."""
    params = {"days": days_back, "limit": limit}
    cat_clause = ""
    if categories:
        placeholders = ", ".join(f":cat{i}" for i in range(len(categories)))
        cat_clause = f"AND category IN ({placeholders}) "
        for i, c in enumerate(categories):
            params[f"cat{i}"] = c
    query = text(f"""
        SELECT brand, model, category,
               prev_price, new_price, drop_eur, drop_pct,
               drop_date, skroutz_link
        FROM vw_biggest_drops
        WHERE drop_date >= CURRENT_DATE - :days
        {cat_clause}
        ORDER BY drop_eur ASC LIMIT :limit
    """)
    return pd.read_sql(query, conn, params=params)


def brand_trend(conn, category, top_n=6, days=90):
    """Avg daily price for the top-N brands (by product count) in one category."""
    query = text("""
        WITH top_brands AS (
            SELECT brand
            FROM vw_brand_summary
            WHERE category = :cat AND brand IS NOT NULL
            ORDER BY product_count DESC
            LIMIT :n
        )
        SELECT bt.brand, bt.date, bt.avg_price
        FROM vw_brand_price_trend bt
        JOIN top_brands tb ON tb.brand = bt.brand
        WHERE bt.category = :cat
          AND bt.date >= CURRENT_DATE - :days
        ORDER BY bt.brand, bt.date
    """)
    return pd.read_sql(query, conn, params={"cat": category, "n": top_n, "days": days})


def hot_deals(conn, limit=20, min_drop_pct=None, min_drop_eur=None, max_drop_pct=None):
    """Price drop + review surge between the two most recent scrapes.

    Optional filters (all None = unfiltered, the original behaviour):
      min_drop_pct — keep only drops of at least this many percent (e.g. 3.0)
      min_drop_eur — keep only drops of at least this many euros
      max_drop_pct — drop rows whose fall is at or beyond this percent
                     (suspect scrapes / variant mix-ups, e.g. 40.0)
    """
    clauses, params = [], {"limit": limit}
    if min_drop_pct is not None:
        clauses.append("price_chg_pct <= -:min_pct")
        params["min_pct"] = min_drop_pct
    if min_drop_eur is not None:
        clauses.append("(price_prev - price_latest) >= :min_eur")
        params["min_eur"] = min_drop_eur
    if max_drop_pct is not None:
        clauses.append("price_chg_pct > -:max_pct")
        params["max_pct"] = max_drop_pct
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    query = text(f"""
        SELECT category, brand, model, product_name,
               price_prev, price_latest, price_chg_pct, new_reviews, hot_score,
               skroutz_link,
               prev_date::text   AS prev_date,
               latest_date::text AS latest_date
        FROM vw_hot_deals
        {where}
        ORDER BY hot_score DESC
        LIMIT :limit
    """)
    return pd.read_sql(query, conn, params=params)


def disappeared(conn, days=30, limit=50):
    """Products not seen for MORE than `days` days, most recently seen first.

    The daily scrape sees only part of the catalog, so a product missing for
    a few days is usually just "not seen", not gone. Filtering here (not only
    in the view) keeps this correct even on a DB whose view is still the old
    7-day definition.

    ORDER BY includes skroutz_link as a tiebreaker: vw_disappeared has many
    rows sharing the same last_seen date, and without a secondary sort key
    Postgres's tie order depends on the query plan (observed to differ
    between a parameterized LIMIT and a literal one) — not stable.
    """
    query = text("""
        SELECT category, brand, model, product_name,
               last_seen, days_since_last_seen, skroutz_link
        FROM vw_disappeared
        WHERE days_since_last_seen > :days
        ORDER BY last_seen DESC, skroutz_link LIMIT :limit
    """)
    return pd.read_sql(query, conn, params={"days": days, "limit": limit})


def brand_discount_freq(conn, category=None, limit=None):
    """How often each brand has a price drop (last 90 days per analytics.sql)."""
    params = {}
    cat_clause = "WHERE brand IS NOT NULL "
    if category is not None:
        cat_clause = "WHERE category = :cat AND brand IS NOT NULL "
        params["cat"] = category
    limit_clause = ""
    if limit is not None:
        limit_clause = "LIMIT :limit"
        params["limit"] = limit
    query = text(f"""
        SELECT category, brand, discount_days, tracked_days, discount_freq_pct
        FROM vw_brand_discount_freq
        {cat_clause}
        ORDER BY category, discount_freq_pct DESC NULLS LAST
        {limit_clause}
    """)
    return pd.read_sql(query, conn, params=params)


def market_index(conn, days=90):
    """Daily avg price per category over the last `days` days."""
    query = text("""
        SELECT category, date, avg_price
        FROM vw_daily_market_index
        WHERE date >= CURRENT_DATE - :days
        ORDER BY category, date
    """)
    return pd.read_sql(query, conn, params={"days": days})


def count_drops(conn, days_back, exclude_suspect_pct=None):
    """Number of price drops with drop_date >= CURRENT_DATE - days_back.
    exclude_suspect_pct (e.g. 40.0) leaves out drops of that size or larger."""
    params = {"days": days_back}
    suspect_clause = ""
    if exclude_suspect_pct is not None:
        suspect_clause = "AND drop_pct > -:suspect"
        params["suspect"] = exclude_suspect_pct
    query = text(f"""
        SELECT COUNT(*) FROM vw_biggest_drops
        WHERE drop_date >= CURRENT_DATE - :days
        {suspect_clause}
    """)
    return int(conn.execute(query, params).scalar() or 0)


def count_suspect_drops(conn, days_back, suspect_pct):
    """Number of drops of suspect_pct percent or more (likely scrape errors)."""
    query = text("""
        SELECT COUNT(*) FROM vw_biggest_drops
        WHERE drop_date >= CURRENT_DATE - :days
          AND drop_pct <= -:suspect
    """)
    return int(conn.execute(query, {"days": days_back, "suspect": suspect_pct}).scalar() or 0)


def count_new_products(conn, days=7):
    """Products first seen in the last `days` days that are in the latest scrape
    (same filter as the New Arrivals list)."""
    query = text("""
        SELECT COUNT(*)
        FROM products p
        JOIN price_snapshots s ON s.product_id = p.id
          AND s.date = (SELECT MAX(date) FROM price_snapshots)
        WHERE p.first_seen >= CURRENT_DATE - :days
    """)
    return int(conn.execute(query, {"days": days}).scalar() or 0)


def count_disappeared(conn, days=30):
    """Products not seen for more than `days` days (same filter as disappeared())."""
    query = text("""
        SELECT COUNT(*) FROM vw_disappeared
        WHERE days_since_last_seen > :days
    """)
    return int(conn.execute(query, {"days": days}).scalar() or 0)


def count_latest_products(conn):
    """Number of products in vw_latest_prices (denominator for the search sample)."""
    return int(conn.execute(text("SELECT COUNT(*) FROM vw_latest_prices")).scalar() or 0)
