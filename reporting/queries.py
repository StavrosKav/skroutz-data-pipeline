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
    """Per-category product count + min/median/avg/max price, latest snapshot date."""
    query = text("""
        SELECT p.category,
               COUNT(DISTINCT p.id) AS product_count,
               AVG(s.price_eur)     AS avg_price,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY s.price_eur) AS median_price,
               MIN(s.price_eur)     AS min_price,
               MAX(s.price_eur)     AS max_price
        FROM products p
        JOIN price_snapshots s ON s.product_id = p.id
        WHERE s.date = (SELECT MAX(date) FROM price_snapshots)
        GROUP BY p.category ORDER BY p.category
    """)
    return pd.read_sql(query, conn)


def biggest_drops(conn, days_back, categories=None, limit=100):
    """Price drops (7-snapshot baseline, see reporting/drops.py) with drop_date >= MAX(date) - days_back, most negative drop_eur first."""
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
        FROM vw_drops_7d
        WHERE drop_date >= (SELECT MAX(date) FROM price_snapshots) - :days
        {cat_clause}
        ORDER BY drop_eur ASC LIMIT :limit
    """)
    return pd.read_sql(query, conn, params=params)


def brand_trend(conn, category, top_n=6, days=90):
    """Median daily price for the top-N brands (by product count) in one category.

    Computed from price_snapshots (cheapest-shop "from" price), one value per
    brand per recorded day; the median is not moved by a few very expensive models."""
    query = text("""
        WITH top_brands AS (
            SELECT brand
            FROM vw_brand_summary
            WHERE category = :cat AND brand IS NOT NULL
            ORDER BY product_count DESC
            LIMIT :n
        )
        SELECT p.brand, s.date,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY s.price_eur) AS median_price
        FROM price_snapshots s
        JOIN products p    ON p.id = s.product_id
        JOIN top_brands tb ON tb.brand = p.brand
        WHERE p.category = :cat
          AND s.price_eur IS NOT NULL
          AND s.date >= (SELECT MAX(date) FROM price_snapshots) - :days
        GROUP BY p.brand, s.date
        ORDER BY p.brand, s.date
    """)
    return pd.read_sql(query, conn, params={"cat": category, "n": top_n, "days": days})


def hot_deals(conn, limit=20, min_drop_pct=None, min_drop_eur=None):
    """Latest-day price drop (same definition as the dashboard: mv_drops_7d,
    vs LEAST(yesterday, 7-snapshot median)) + review surge vs the previous snapshot.

    Big drops (>= BIG_DROP_PCT) are kept; the caller flags them.
    Optional filters (None = unfiltered):
      min_drop_pct — keep only drops of at least this many percent (e.g. 3.0)
      min_drop_eur — keep only drops of at least this many euros
    """
    clauses, params = [], {"limit": limit}
    if min_drop_pct is not None:
        clauses.append("price_chg_pct <= -:min_pct")
        params["min_pct"] = min_drop_pct
    if min_drop_eur is not None:
        clauses.append("(price_prev - price_latest) >= :min_eur")
        params["min_eur"] = min_drop_eur
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    query = text(f"""
        WITH d AS (
            SELECT d.*, t.reviews AS reviews_latest, pr.reviews AS reviews_prev, pr.date AS prev_date
            FROM mv_drops_7d d
            JOIN price_snapshots t ON t.product_id = d.product_id AND t.date = d.drop_date
            LEFT JOIN LATERAL (
                SELECT ps.reviews, ps.date FROM price_snapshots ps
                WHERE ps.product_id = d.product_id AND ps.date < d.drop_date
                ORDER BY ps.date DESC LIMIT 1
            ) pr ON TRUE
            WHERE d.drop_date = (SELECT MAX(date) FROM price_snapshots)
              AND COALESCE(t.reviews - pr.reviews, 0) > 0   -- review surge required, as before
        ), h AS (
            SELECT category, brand, model, product_name,
                   prev_price AS price_prev, new_price AS price_latest,
                   drop_pct AS price_chg_pct,
                   COALESCE(reviews_latest - reviews_prev, 0) AS new_reviews,
                   ROUND((-0.5 * drop_pct + LEAST(COALESCE(reviews_latest - reviews_prev, 0), 50))::numeric, 1) AS hot_score,
                   skroutz_link, prev_date, drop_date AS latest_date
            FROM d
        )
        SELECT category, brand, model, product_name,
               price_prev, price_latest, price_chg_pct, new_reviews, hot_score,
               skroutz_link,
               prev_date::text   AS prev_date,
               latest_date::text AS latest_date
        FROM h
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


# Fixed basket: a product enters the index if it has a price on the first
# recorded day of the window (base day) and on at least this share of the
# window's recorded days.
BASKET_COVERAGE = 0.8


def basket_index(conn, days=90):
    """Daily fixed-basket price index per category (base day = 100).

    For the same set of products every day, each product's price is divided by
    its base-day price; the index is the geometric mean of those ratios x 100
    (Jevons, as in consumer price indices: every product counts equally). A median
    of ratios would sit at exactly 100 because most prices do not move on a given
    day. Products that come and go do not move it, unlike a plain daily average."""
    query = text("""
        WITH win AS (
            SELECT DISTINCT date FROM price_snapshots
            WHERE date > (SELECT MAX(date) FROM price_snapshots) - :days
        ),
        base_day AS (SELECT MIN(date) AS d, COUNT(*) AS n_days FROM win),
        daily AS (
            SELECT s.product_id, s.date, MIN(s.price_eur) AS price
            FROM price_snapshots s JOIN win ON win.date = s.date
            WHERE s.price_eur > 0
            GROUP BY s.product_id, s.date
        ),
        basket AS (
            SELECT d.product_id, MAX(d.price) FILTER (WHERE d.date = b.d) AS base_price
            FROM daily d CROSS JOIN base_day b
            GROUP BY d.product_id, b.d, b.n_days
            HAVING COUNT(*) FILTER (WHERE d.date = b.d) = 1
               AND COUNT(*) >= :coverage * b.n_days
        )
        SELECT p.category, d.date,
               EXP(AVG(LN(d.price / k.base_price))) * 100 AS idx,
               COUNT(*) AS products
        FROM daily d
        JOIN basket k   ON k.product_id = d.product_id
        JOIN products p ON p.id = d.product_id
        GROUP BY p.category, d.date
        ORDER BY p.category, d.date
    """)
    return pd.read_sql(query, conn, params={"days": days, "coverage": BASKET_COVERAGE})


def count_drops(conn, days_back):
    """Number of price drops (vw_drops_7d, big ones included) with drop_date >= MAX(date) - days_back."""
    query = text("""
        SELECT COUNT(*) FROM vw_drops_7d
        WHERE drop_date >= (SELECT MAX(date) FROM price_snapshots) - :days
    """)
    return int(conn.execute(query, {"days": days_back}).scalar() or 0)


def count_suspect_drops(conn, days_back, suspect_pct):
    """Number of drops of suspect_pct percent or more (shown, flagged "check")."""
    query = text("""
        SELECT COUNT(*) FROM vw_drops_7d
        WHERE drop_date >= (SELECT MAX(date) FROM price_snapshots) - :days
          AND drop_pct <= -:suspect
    """)
    return int(conn.execute(query, {"days": days_back, "suspect": suspect_pct}).scalar() or 0)


# The one "new product" rule (dashboard, Telegram, ask bot, eval): first seen in
# the `days` most recent days including the latest scrape date, and listed in it.
NEW_PRODUCTS_WHERE = "first_seen > (SELECT MAX(date) FROM price_snapshots) - :days"


def count_new_products(conn, days=7):
    """Products first seen in the last `days` days that are in the latest scrape
    (same filter as the New Arrivals list)."""
    query = text("""
        SELECT COUNT(*)
        FROM products p
        JOIN price_snapshots s ON s.product_id = p.id
          AND s.date = (SELECT MAX(date) FROM price_snapshots)
        WHERE p.""" + NEW_PRODUCTS_WHERE + """
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
