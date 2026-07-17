-- =============================================================================
-- analytics.sql
-- =============
-- Analytical views for the Skroutz price-tracking database.
-- Run once in DBeaver or psql against the SkroutzPR database.
--
-- Views defined here:
--   vw_latest_prices          — each product with its most recent price snapshot
--   vw_price_history          — full daily price history with day-over-day change
--   vw_biggest_drops          — products with the largest single-day price drops
--   vw_brand_summary          — price stats per brand per category
--   vw_disappeared            — products not seen for 7+ days
--   vw_price_volatility       — 30-day coefficient of variation per product
--   vw_brand_price_trend      — daily avg price per brand/category
--   vw_hot_deals              — products with price drop + review surge in the last 7 days
--   vw_price_floor            — all-time low / high per product
--   vw_brand_discount_freq    — % of days each brand had a ≥3% drop (last 90 days)
--   vw_near_atl               — products currently within a given % of their all-time low
--   vw_price_trend_direction  — 7-day vs 30-day avg price momentum (falling/stable/rising)
--   vw_daily_market_index     — daily avg category price (macro market trend)
--   vw_restock_pricing        — price before vs. after a 3+ day stock gap
--   vw_review_velocity        — products gaining the most new reviews in the last 14 days
--
-- v4 (see bottom of file): vw_latest_prices, vw_price_history, vw_biggest_drops,
--   vw_price_floor, vw_price_volatility, vw_price_trend_direction, vw_brand_summary,
--   vw_brand_price_trend, vw_daily_market_index, vw_restock_pricing, vw_review_velocity
--   are backed by MATERIALIZED VIEWs (mv_*), refreshed once/day by
--   run_pipeline.py's refresh_matviews(). Names and columns are unchanged.
-- =============================================================================


-- ── 1. Latest prices ──────────────────────────────────────────────────────────
-- One row per product showing its most recent price, rating, and snapshot date.
-- Foundation for "what does the market look like right now?" queries.

-- DISTINCT ON replaces the correlated subquery (O(N) per row) for a single sorted scan
CREATE OR REPLACE VIEW vw_latest_prices AS
SELECT DISTINCT ON (p.id)
    p.id,
    p.category,
    p.brand,
    p.model,
    p.product_name,
    p.specs,
    s.date         AS last_price_date,
    s.price_eur,
    s.rating,
    s.reviews,
    s.installments_per_month,
    s.installments_in_total,
    p.first_seen,
    p.last_seen,
    p.skroutz_link
FROM products p
JOIN price_snapshots s ON s.product_id = p.id
ORDER BY p.id, s.date DESC;


-- ── 2. Full price history with day-over-day change ────────────────────────────
-- Every snapshot with the previous day's price alongside, showing the absolute
-- and percentage change.  Uses LAG() so you can query "show all days a phone
-- dropped more than 5%" without a self-join.

CREATE OR REPLACE VIEW vw_price_history AS
WITH ph AS (
    SELECT
        s.product_id,
        s.date,
        s.price_eur,
        s.rating,
        s.reviews,
        LAG(s.price_eur) OVER (PARTITION BY s.product_id ORDER BY s.date) AS prev_price
    FROM price_snapshots s
)
SELECT
    p.id                                                               AS product_id,
    p.category,
    p.brand,
    p.model,
    p.product_name,
    ph.date,
    ph.price_eur,
    ph.prev_price,
    ph.price_eur - ph.prev_price                                       AS price_change,
    ROUND(
        100.0 * (ph.price_eur - ph.prev_price)
        / NULLIF(ph.prev_price, 0),
        2
    )                                                                  AS pct_change,
    ph.rating,
    ph.reviews,
    p.skroutz_link
FROM ph
JOIN products p ON p.id = ph.product_id;


-- ── 3. Biggest single-day price drops ────────────────────────────────────────
-- Top products by absolute price drop between any two consecutive scraped days.
-- Useful for "alert me to good deals" use-cases and pipeline demos.

CREATE OR REPLACE VIEW vw_biggest_drops AS
SELECT
    product_id,
    category,
    brand,
    model,
    product_name,
    date          AS drop_date,
    prev_price,
    price_eur     AS new_price,
    price_change  AS drop_eur,
    pct_change    AS drop_pct,
    skroutz_link
FROM vw_price_history
WHERE price_change < 0
ORDER BY price_change ASC;


-- ── 4. Brand price summary per category ──────────────────────────────────────
-- For each (category, brand) pair: how many products, what price range,
-- what is the average and median price across all snapshots.
-- Answers "which brands are budget vs premium in each category?"

CREATE OR REPLACE VIEW vw_brand_summary AS
SELECT
    p.category,
    p.brand,
    COUNT(DISTINCT p.id)                              AS product_count,
    ROUND(AVG(s.price_eur),    2)                     AS avg_price,
    ROUND(MIN(s.price_eur),    2)                     AS min_price,
    ROUND(MAX(s.price_eur),    2)                     AS max_price,
    ROUND(PERCENTILE_CONT(0.5)
          WITHIN GROUP (ORDER BY s.price_eur)::NUMERIC, 2)
                                                      AS median_price,
    COUNT(s.id)                                       AS total_snapshots
FROM products p
JOIN price_snapshots s ON s.product_id = p.id
WHERE s.price_eur IS NOT NULL
GROUP BY p.category, p.brand;


-- ── 5. Disappeared products ───────────────────────────────────────────────────
-- Products whose last_seen date is more than 7 days ago — they were once on
-- skroutz but have been removed or are out of stock.
-- Useful for "what did the market lose recently?" and data-quality checks.

CREATE OR REPLACE VIEW vw_disappeared AS
SELECT
    id,
    category,
    brand,
    model,
    product_name,
    first_seen,
    last_seen,
    (CURRENT_DATE - last_seen) AS days_since_last_seen,
    skroutz_link
FROM products
WHERE last_seen < CURRENT_DATE - INTERVAL '7 days'
ORDER BY last_seen DESC;


-- ── 6. Price volatility (30-day coefficient of variation) ────────────────────
-- Measures how "noisy" a product's price has been over the last 30 days.
-- cv_pct = (stddev / avg) * 100.  Low cv = stable price, high cv = volatile.
-- Useful for flagging genuine deals vs. normal price fluctuation.

CREATE OR REPLACE VIEW vw_price_volatility AS
SELECT
    product_id,
    ROUND(STDDEV(price_eur)::NUMERIC, 2)                                       AS stddev_price,
    ROUND((STDDEV(price_eur) / NULLIF(AVG(price_eur), 0) * 100)::NUMERIC, 1)  AS cv_pct,
    COUNT(*)                                                                    AS snap_count
FROM price_snapshots
WHERE date >= CURRENT_DATE - 30
GROUP BY product_id;


-- ── 7. Brand average price trend (daily) ─────────────────────────────────────
-- Daily average price per brand per category.  Use this to draw side-by-side
-- trend lines comparing e.g. Samsung vs. Apple over the last 90 days.

CREATE OR REPLACE VIEW vw_brand_price_trend AS
SELECT
    p.category,
    p.brand,
    ps.date,
    ROUND(AVG(ps.price_eur)::NUMERIC, 2) AS avg_price,
    COUNT(DISTINCT ps.product_id)         AS product_count
FROM price_snapshots ps
JOIN products p ON p.id = ps.product_id
WHERE p.brand IS NOT NULL
GROUP BY p.category, p.brand, ps.date;


-- ── 8. Hot deals (price drop + review surge vs. previous scrape) ─────────────
-- Products where the price fell AND new reviews appeared since the prior batch.
-- Compares the two most recent distinct scrape dates so the view always has
-- results regardless of how many days apart those dates are.
-- hot_score combines both signals: bigger drop + more reviews = higher score.

CREATE OR REPLACE VIEW vw_hot_deals AS
WITH latest_dates AS (
    SELECT DISTINCT date
    FROM price_snapshots
    ORDER BY date DESC
    LIMIT 2
),
date_pair AS (
    SELECT
        MAX(date) AS d_new,
        MIN(date) AS d_old
    FROM latest_dates
),
recent AS (
    SELECT
        ps.product_id,
        MAX(ps.price_eur)  FILTER (WHERE ps.date = dp.d_old) AS price_prev,
        MAX(ps.price_eur)  FILTER (WHERE ps.date = dp.d_new) AS price_latest,
        MAX(ps.reviews)    FILTER (WHERE ps.date = dp.d_old) AS reviews_prev,
        MAX(ps.reviews)    FILTER (WHERE ps.date = dp.d_new) AS reviews_latest
    FROM price_snapshots ps, date_pair dp
    WHERE ps.date IN (dp.d_old, dp.d_new)
    GROUP BY ps.product_id
)
SELECT
    p.id,
    p.category,
    p.brand,
    p.model,
    p.product_name,
    p.skroutz_link,
    dp.d_old                                                                               AS prev_date,
    dp.d_new                                                                               AS latest_date,
    r.price_prev,
    r.price_latest,
    ROUND(((r.price_latest - r.price_prev) / NULLIF(r.price_prev, 0) * 100)::NUMERIC, 1) AS price_chg_pct,
    COALESCE(r.reviews_latest - r.reviews_prev, 0)                                        AS new_reviews,
    ROUND((
        -1.0 * ((r.price_latest - r.price_prev) / NULLIF(r.price_prev, 0) * 50)
        + LEAST(COALESCE(r.reviews_latest - r.reviews_prev, 0), 50)
    )::NUMERIC, 1)                                                                         AS hot_score
FROM recent r
JOIN products p ON p.id = r.product_id
CROSS JOIN date_pair dp
WHERE r.price_latest IS NOT NULL
  AND r.price_prev   IS NOT NULL
  AND r.price_latest < r.price_prev
  AND COALESCE(r.reviews_latest - r.reviews_prev, 0) > 0
ORDER BY hot_score DESC;


-- =============================================================================
-- Sample queries using the views above
-- =============================================================================

-- Top 10 cheapest laptops right now:
-- SELECT brand, model, price_eur FROM vw_latest_prices
-- WHERE category = 'laptop' ORDER BY price_eur ASC LIMIT 10;

-- 30-day price history for a specific phone (replace the URL):
-- SELECT date, price_eur, price_change, pct_change
-- FROM vw_price_history
-- WHERE skroutz_link = 'https://www.skroutz.gr/s/...'
-- ORDER BY date;

-- Biggest price drops this week:
-- SELECT brand, model, drop_date, prev_price, new_price, drop_eur, drop_pct
-- FROM vw_biggest_drops
-- WHERE drop_date >= CURRENT_DATE - 7
-- LIMIT 20;

-- Average price per brand for phones (cheapest brands first):
-- SELECT brand, product_count, avg_price, median_price
-- FROM vw_brand_summary
-- WHERE category = 'phone'
-- ORDER BY median_price ASC;

-- Products that disappeared in the last 30 days:
-- SELECT category, brand, model, last_seen, days_since_last_seen
-- FROM vw_disappeared
-- WHERE days_since_last_seen <= 30
-- ORDER BY last_seen DESC;


-- =============================================================================
-- Indexes (run once; safe to re-run — all use IF NOT EXISTS)
-- =============================================================================

-- Speeds up vw_latest_prices (DISTINCT ON … ORDER BY p.id, s.date DESC)
-- and any query that filters or groups price snapshots by date.
CREATE INDEX IF NOT EXISTS idx_price_snapshots_date
    ON price_snapshots(date);

-- (product_id, date) lookups are covered by the UNIQUE(product_id, date)
-- constraint's index — no separate index needed.

-- Speeds up all views that filter or group by category or brand
-- (vw_brand_summary, vw_brand_price_trend, vw_brand_discount_freq, vw_near_atl, etc.)
CREATE INDEX IF NOT EXISTS idx_products_category
    ON products(category);

CREATE INDEX IF NOT EXISTS idx_products_brand
    ON products(brand);

-- Speeds up vw_disappeared and send_disappeared_alert() which filter on last_seen
CREATE INDEX IF NOT EXISTS idx_products_last_seen
    ON products(last_seen);


-- =============================================================================
-- Additional analytical views (v2)
-- =============================================================================

-- ── 9. All-time low per product ───────────────────────────────────────────────
-- Per-product floor price over all recorded snapshots.
-- Used to compute "% above ATL" for the Near-ATL intelligence section.

CREATE OR REPLACE VIEW vw_price_floor AS
SELECT
    product_id,
    ROUND(MIN(price_eur)::NUMERIC, 2) AS all_time_low,
    ROUND(MAX(price_eur)::NUMERIC, 2) AS all_time_high,
    COUNT(*)                           AS snapshot_count
FROM price_snapshots
GROUP BY product_id;


-- ── 10. Brand discount frequency (last 90 days) ───────────────────────────────
-- What fraction of tracked days did each brand actually have a price drop ≥3%?
-- Answers: "which brands discount often (Xiaomi) vs. almost never (Apple)?"

CREATE OR REPLACE VIEW vw_brand_discount_freq AS
WITH drops AS (
    SELECT p.category, p.brand,
           COUNT(DISTINCT ph.date) AS discount_days
    FROM vw_price_history ph
    JOIN products p ON p.id = ph.product_id
    WHERE ph.pct_change <= -3
      AND ph.date >= CURRENT_DATE - 90
      AND p.brand IS NOT NULL
    GROUP BY p.category, p.brand
),
totals AS (
    SELECT p.category, p.brand,
           COUNT(DISTINCT ps.date) AS tracked_days
    FROM price_snapshots ps
    JOIN products p ON p.id = ps.product_id
    WHERE ps.date >= CURRENT_DATE - 90 AND p.brand IS NOT NULL
    GROUP BY p.category, p.brand
)
SELECT
    t.category,
    t.brand,
    COALESCE(d.discount_days, 0)                                                       AS discount_days,
    t.tracked_days,
    ROUND(COALESCE(d.discount_days, 0)::NUMERIC / NULLIF(t.tracked_days, 0) * 100, 1) AS discount_freq_pct
FROM totals t
LEFT JOIN drops d ON d.category = t.category AND d.brand = t.brand
ORDER BY discount_freq_pct DESC NULLS LAST;


-- ── 11. Near all-time low ─────────────────────────────────────────────────────
-- Products currently within a given percentage of their all-time low.
-- Requires at least 10 snapshots and a meaningful price range (≥€20).
-- Used by /best Telegram command and the Intelligence dashboard tab.

CREATE OR REPLACE VIEW vw_near_atl AS
SELECT
    lp.id,
    lp.category,
    lp.brand,
    lp.model,
    lp.product_name,
    lp.skroutz_link,
    lp.price_eur AS current_price,
    pf.all_time_low,
    pf.all_time_high,
    pf.snapshot_count,
    ROUND(
        100.0 * (lp.price_eur - pf.all_time_low)
        / NULLIF(pf.all_time_low, 0),
        1
    ) AS pct_above_atl
FROM vw_latest_prices lp
JOIN vw_price_floor pf ON pf.product_id = lp.id
WHERE lp.price_eur > 50
  AND pf.all_time_low > 0
  AND pf.snapshot_count >= 10
  AND (pf.all_time_high - pf.all_time_low) >= 20
ORDER BY pct_above_atl ASC;


-- ── 12. Price trend direction (7-day vs 30-day average) ───────────────────────
-- Classifies each product's price momentum as "falling", "rising", or "stable"
-- by comparing its 7-day average price against its 30-day average.
-- Answers "should I buy now or wait?" — a falling product may drop further.

CREATE OR REPLACE VIEW vw_price_trend_direction AS
SELECT
    product_id,
    ROUND(AVG(price_eur) FILTER (WHERE date >= CURRENT_DATE - 7),  2) AS avg_7d,
    ROUND(AVG(price_eur) FILTER (WHERE date >= CURRENT_DATE - 30), 2) AS avg_30d,
    CASE
        WHEN AVG(price_eur) FILTER (WHERE date >= CURRENT_DATE - 7)
           < AVG(price_eur) FILTER (WHERE date >= CURRENT_DATE - 30) * 0.97
        THEN 'falling'
        WHEN AVG(price_eur) FILTER (WHERE date >= CURRENT_DATE - 7)
           > AVG(price_eur) FILTER (WHERE date >= CURRENT_DATE - 30) * 1.03
        THEN 'rising'
        ELSE 'stable'
    END AS trend
FROM price_snapshots
WHERE date >= CURRENT_DATE - 30
GROUP BY product_id;


-- ── 13. Daily market index (category-level average price) ─────────────────────
-- Daily average, min, and max price per category across all tracked products.
-- Answers "are phones getting cheaper overall?" — a macro market trend view
-- analogous to a stock index for each product category.

CREATE OR REPLACE VIEW vw_daily_market_index AS
SELECT
    p.category,
    ps.date,
    ROUND(AVG(ps.price_eur)::NUMERIC,  2) AS avg_price,
    ROUND(MIN(ps.price_eur)::NUMERIC,  2) AS min_price,
    ROUND(MAX(ps.price_eur)::NUMERIC,  2) AS max_price,
    COUNT(DISTINCT ps.product_id)          AS products_tracked
FROM price_snapshots ps
JOIN products p ON p.id = ps.product_id
GROUP BY p.category, ps.date
ORDER BY p.category, ps.date;


-- =============================================================================
-- Additional views (v3)
-- =============================================================================

-- ── 14. Restock pricing ───────────────────────────────────────────────────────
-- Products that disappeared then reappeared after a 3+ day gap, showing the
-- price before and after the gap.  Useful for spotting items that come back
-- cheaper (or more expensive) after going out of stock.

CREATE OR REPLACE VIEW vw_restock_pricing AS
WITH consecutive AS (
    SELECT
        product_id,
        date                                                               AS before_gap,
        LEAD(date)      OVER (PARTITION BY product_id ORDER BY date)      AS after_gap,
        price_eur                                                          AS price_before,
        LEAD(price_eur) OVER (PARTITION BY product_id ORDER BY date)      AS price_after
    FROM price_snapshots
)
SELECT
    p.id,
    p.category,
    p.brand,
    p.model,
    p.product_name,
    p.skroutz_link,
    c.before_gap,
    c.after_gap,
    (c.after_gap - c.before_gap)                                         AS gap_days,
    c.price_before,
    c.price_after,
    ROUND(
        ((c.price_after - c.price_before) / NULLIF(c.price_before, 0) * 100)::NUMERIC, 1
    )                                                                     AS price_chg_pct
FROM consecutive c
JOIN products p ON p.id = c.product_id
WHERE (c.after_gap - c.before_gap) >= 3
  AND c.price_before IS NOT NULL
  AND c.price_after  IS NOT NULL
ORDER BY c.after_gap DESC, ABS(c.price_after - c.price_before) DESC;


-- ── 15. Review velocity ────────────────────────────────────────────────────────
-- Products gaining the most new reviews in the last 14 days.
-- High velocity = actively purchased / trending right now.

CREATE OR REPLACE VIEW vw_review_velocity AS
WITH bounds AS (
    SELECT MAX(date) AS latest_date, MAX(date) - 14 AS cutoff_date
    FROM price_snapshots
),
agg AS (
    SELECT
        ps.product_id,
        MAX(ps.reviews) FILTER (WHERE ps.date  = b.latest_date) AS rev_now,
        MAX(ps.reviews) FILTER (WHERE ps.date <= b.cutoff_date) AS rev_14d
    FROM price_snapshots ps, bounds b
    GROUP BY ps.product_id
)
SELECT
    p.id            AS product_id,
    p.category,
    p.brand,
    p.model,
    p.product_name,
    p.skroutz_link,
    a.rev_now,
    a.rev_14d,
    COALESCE(a.rev_now - a.rev_14d, 0)                              AS new_reviews_14d,
    ROUND(COALESCE(a.rev_now - a.rev_14d, 0)::NUMERIC / 14.0, 2)   AS reviews_per_day
FROM agg a
JOIN products p ON p.id = a.product_id
WHERE a.rev_now IS NOT NULL
  AND a.rev_14d IS NOT NULL
  AND a.rev_now > a.rev_14d
ORDER BY COALESCE(a.rev_now - a.rev_14d, 0) DESC;


-- =============================================================================
-- Performance pass (v4) — materialized aggregate views + trigram search indexes
-- =============================================================================
--
-- Data changes ONCE per day (the 08:00 pipeline). Every view above that GROUPs
-- or windows (LAG/LEAD/FILTER) over the full price_snapshots table was paying
-- that cost on every single read — including the hottest path in the whole
-- project (vw_biggest_drops, hit by the HTML dashboard, Streamlit, the
-- Telegram drop digest, and /status on every call).
--
-- The 10 heaviest full-table aggregates below are now MATERIALIZED VIEWs,
-- refreshed once by run_pipeline.py's refresh_matviews() step right after
-- Load SQL. The original view names are preserved as thin
-- `SELECT * FROM mv_...` wrappers — CREATE OR REPLACE VIEW here further
-- redefines the CREATE OR REPLACE VIEW statements earlier in this same file,
-- so a fresh install run top-to-bottom ends in the matview-backed state and
-- no consumer's SQL (view name, columns, column order) changes.
--
-- Left as plain (non-materialized) views, deliberately:
--   vw_disappeared          — filters the small `products` table only
--   vw_hot_deals            — WHERE date IN (last 2 dates), index-bound already
--   vw_brand_discount_freq  — now reads the matview-backed vw_price_history;
--                              its own GROUP BY is bounded by the date index (90d)
--   vw_near_atl             — joins two now-cheap views, no aggregation of its own
--
-- Every materialized view gets a UNIQUE index so REFRESH ... CONCURRENTLY can
-- run without locking the view against readers.

-- ── mv_latest_prices — backs vw_latest_prices ──────────────────────────────────
-- Initially assessed as "already cheap" (DISTINCT ON drives an index scan, not
-- a seq scan) and left as a plain view — EXPLAIN ANALYZE on the /find query
-- proved that wrong: DISTINCT ON still visits every one of the ~450k snapshot
-- rows to find each product's latest one, and vw_latest_prices sits underneath
-- both /find and /best. Materializing it was the actual fix for /find's
-- latency, not the trigram indexes below.
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_latest_prices AS
SELECT DISTINCT ON (p.id)
    p.id,
    p.category,
    p.brand,
    p.model,
    p.product_name,
    p.specs,
    s.date         AS last_price_date,
    s.price_eur,
    s.rating,
    s.reviews,
    s.installments_per_month,
    s.installments_in_total,
    p.first_seen,
    p.last_seen,
    p.skroutz_link
FROM products p
JOIN price_snapshots s ON s.product_id = p.id
ORDER BY p.id, s.date DESC
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_latest_prices_id
    ON mv_latest_prices (id);

CREATE OR REPLACE VIEW vw_latest_prices AS
SELECT * FROM mv_latest_prices;


-- ── mv_price_history — backs vw_price_history AND vw_biggest_drops ────────────
-- The single most expensive query in the app: LAG() over all ~450k snapshot
-- rows, previously recomputed on every dashboard/bot/digest call.
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_price_history AS
WITH ph AS (
    SELECT
        s.product_id,
        s.date,
        s.price_eur,
        s.rating,
        s.reviews,
        LAG(s.price_eur) OVER (PARTITION BY s.product_id ORDER BY s.date) AS prev_price
    FROM price_snapshots s
)
SELECT
    p.id                                                               AS product_id,
    p.category,
    p.brand,
    p.model,
    p.product_name,
    ph.date,
    ph.price_eur,
    ph.prev_price,
    ph.price_eur - ph.prev_price                                       AS price_change,
    ROUND(
        100.0 * (ph.price_eur - ph.prev_price)
        / NULLIF(ph.prev_price, 0),
        2
    )                                                                  AS pct_change,
    ph.rating,
    ph.reviews,
    p.skroutz_link
FROM ph
JOIN products p ON p.id = ph.product_id
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_price_history_pid_date
    ON mv_price_history (product_id, date);
CREATE INDEX IF NOT EXISTS idx_mv_price_history_date
    ON mv_price_history (date);

CREATE OR REPLACE VIEW vw_price_history AS
SELECT * FROM mv_price_history;

CREATE OR REPLACE VIEW vw_biggest_drops AS
SELECT
    product_id, category, brand, model, product_name,
    date          AS drop_date,
    prev_price,
    price_eur     AS new_price,
    price_change  AS drop_eur,
    pct_change    AS drop_pct,
    skroutz_link
FROM mv_price_history
WHERE price_change < 0
ORDER BY price_change ASC;


-- ── mv_price_floor — all-time low/high per product ─────────────────────────────
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_price_floor AS
SELECT
    product_id,
    ROUND(MIN(price_eur)::NUMERIC, 2) AS all_time_low,
    ROUND(MAX(price_eur)::NUMERIC, 2) AS all_time_high,
    COUNT(*)                           AS snapshot_count
FROM price_snapshots
GROUP BY product_id
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_price_floor_pid
    ON mv_price_floor (product_id);

CREATE OR REPLACE VIEW vw_price_floor AS
SELECT * FROM mv_price_floor;


-- ── mv_price_volatility — 30-day coefficient of variation per product ─────────
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_price_volatility AS
SELECT
    product_id,
    ROUND(STDDEV(price_eur)::NUMERIC, 2)                                       AS stddev_price,
    ROUND((STDDEV(price_eur) / NULLIF(AVG(price_eur), 0) * 100)::NUMERIC, 1)  AS cv_pct,
    COUNT(*)                                                                    AS snap_count
FROM price_snapshots
WHERE date >= CURRENT_DATE - 30
GROUP BY product_id
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_price_volatility_pid
    ON mv_price_volatility (product_id);

CREATE OR REPLACE VIEW vw_price_volatility AS
SELECT * FROM mv_price_volatility;


-- ── mv_price_trend_direction — 7-day vs 30-day momentum per product ───────────
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_price_trend_direction AS
SELECT
    product_id,
    ROUND(AVG(price_eur) FILTER (WHERE date >= CURRENT_DATE - 7),  2) AS avg_7d,
    ROUND(AVG(price_eur) FILTER (WHERE date >= CURRENT_DATE - 30), 2) AS avg_30d,
    CASE
        WHEN AVG(price_eur) FILTER (WHERE date >= CURRENT_DATE - 7)
           < AVG(price_eur) FILTER (WHERE date >= CURRENT_DATE - 30) * 0.97
        THEN 'falling'
        WHEN AVG(price_eur) FILTER (WHERE date >= CURRENT_DATE - 7)
           > AVG(price_eur) FILTER (WHERE date >= CURRENT_DATE - 30) * 1.03
        THEN 'rising'
        ELSE 'stable'
    END AS trend
FROM price_snapshots
WHERE date >= CURRENT_DATE - 30
GROUP BY product_id
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_price_trend_direction_pid
    ON mv_price_trend_direction (product_id);

CREATE OR REPLACE VIEW vw_price_trend_direction AS
SELECT * FROM mv_price_trend_direction;


-- ── mv_brand_summary — price stats per brand per category ─────────────────────
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_brand_summary AS
SELECT
    p.category,
    p.brand,
    COUNT(DISTINCT p.id)                              AS product_count,
    ROUND(AVG(s.price_eur),    2)                     AS avg_price,
    ROUND(MIN(s.price_eur),    2)                     AS min_price,
    ROUND(MAX(s.price_eur),    2)                     AS max_price,
    ROUND(PERCENTILE_CONT(0.5)
          WITHIN GROUP (ORDER BY s.price_eur)::NUMERIC, 2)
                                                      AS median_price,
    COUNT(s.id)                                       AS total_snapshots
FROM products p
JOIN price_snapshots s ON s.product_id = p.id
WHERE s.price_eur IS NOT NULL
GROUP BY p.category, p.brand
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_brand_summary_cat_brand
    ON mv_brand_summary (category, brand);

CREATE OR REPLACE VIEW vw_brand_summary AS
SELECT * FROM mv_brand_summary;


-- ── mv_brand_price_trend — daily avg price per brand/category ─────────────────
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_brand_price_trend AS
SELECT
    p.category,
    p.brand,
    ps.date,
    ROUND(AVG(ps.price_eur)::NUMERIC, 2) AS avg_price,
    COUNT(DISTINCT ps.product_id)         AS product_count
FROM price_snapshots ps
JOIN products p ON p.id = ps.product_id
WHERE p.brand IS NOT NULL
GROUP BY p.category, p.brand, ps.date
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_brand_price_trend_cat_brand_date
    ON mv_brand_price_trend (category, brand, date);

CREATE OR REPLACE VIEW vw_brand_price_trend AS
SELECT * FROM mv_brand_price_trend;


-- ── mv_daily_market_index — daily avg/min/max price per category ──────────────
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_daily_market_index AS
SELECT
    p.category,
    ps.date,
    ROUND(AVG(ps.price_eur)::NUMERIC,  2) AS avg_price,
    ROUND(MIN(ps.price_eur)::NUMERIC,  2) AS min_price,
    ROUND(MAX(ps.price_eur)::NUMERIC,  2) AS max_price,
    COUNT(DISTINCT ps.product_id)          AS products_tracked
FROM price_snapshots ps
JOIN products p ON p.id = ps.product_id
GROUP BY p.category, ps.date
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_daily_market_index_cat_date
    ON mv_daily_market_index (category, date);

CREATE OR REPLACE VIEW vw_daily_market_index AS
SELECT * FROM mv_daily_market_index
ORDER BY category, date;


-- ── mv_restock_pricing — price before/after a 3+ day stock gap ────────────────
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_restock_pricing AS
WITH consecutive AS (
    SELECT
        product_id,
        date                                                               AS before_gap,
        LEAD(date)      OVER (PARTITION BY product_id ORDER BY date)      AS after_gap,
        price_eur                                                          AS price_before,
        LEAD(price_eur) OVER (PARTITION BY product_id ORDER BY date)      AS price_after
    FROM price_snapshots
)
SELECT
    p.id,
    p.category,
    p.brand,
    p.model,
    p.product_name,
    p.skroutz_link,
    c.before_gap,
    c.after_gap,
    (c.after_gap - c.before_gap)                                         AS gap_days,
    c.price_before,
    c.price_after,
    ROUND(
        ((c.price_after - c.price_before) / NULLIF(c.price_before, 0) * 100)::NUMERIC, 1
    )                                                                     AS price_chg_pct
FROM consecutive c
JOIN products p ON p.id = c.product_id
WHERE (c.after_gap - c.before_gap) >= 3
  AND c.price_before IS NOT NULL
  AND c.price_after  IS NOT NULL
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_restock_pricing_id_gap
    ON mv_restock_pricing (id, before_gap);

CREATE OR REPLACE VIEW vw_restock_pricing AS
SELECT * FROM mv_restock_pricing
ORDER BY after_gap DESC, ABS(price_after - price_before) DESC;


-- ── mv_review_velocity — products gaining the most reviews in 14 days ─────────
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_review_velocity AS
WITH bounds AS (
    SELECT MAX(date) AS latest_date, MAX(date) - 14 AS cutoff_date
    FROM price_snapshots
),
agg AS (
    SELECT
        ps.product_id,
        MAX(ps.reviews) FILTER (WHERE ps.date  = b.latest_date) AS rev_now,
        MAX(ps.reviews) FILTER (WHERE ps.date <= b.cutoff_date) AS rev_14d
    FROM price_snapshots ps, bounds b
    GROUP BY ps.product_id
)
SELECT
    p.id            AS product_id,
    p.category,
    p.brand,
    p.model,
    p.product_name,
    p.skroutz_link,
    a.rev_now,
    a.rev_14d,
    COALESCE(a.rev_now - a.rev_14d, 0)                              AS new_reviews_14d,
    ROUND(COALESCE(a.rev_now - a.rev_14d, 0)::NUMERIC / 14.0, 2)   AS reviews_per_day
FROM agg a
JOIN products p ON p.id = a.product_id
WHERE a.rev_now IS NOT NULL
  AND a.rev_14d IS NOT NULL
  AND a.rev_now > a.rev_14d
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_review_velocity_pid
    ON mv_review_velocity (product_id);

CREATE OR REPLACE VIEW vw_review_velocity AS
SELECT * FROM mv_review_velocity
ORDER BY new_reviews_14d DESC;


-- ── Trigram search indexes ──────────────────────────────────────────────────
-- telegram_bot.py's /find and /history do leading-wildcard ILIKE '%term%',
-- which a btree index cannot use at all (previously: sequential scan on every
-- interactive command). pg_trgm's GIN indexes support arbitrary substring
-- ILIKE. The fourth index of each set covers the concatenated "brand model"
-- clause both commands also query, so a query spanning the brand/model
-- boundary (e.g. "galaxy s25") is index-backed too.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_products_brand_trgm
    ON products USING GIN (brand gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_products_model_trgm
    ON products USING GIN (model gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_products_product_name_trgm
    ON products USING GIN (product_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_products_brand_model_trgm
    ON products USING GIN ((brand || ' ' || model) gin_trgm_ops);

-- /find actually queries vw_latest_prices, which — as of this same v4 section —
-- is materialized (mv_latest_prices), so the ILIKE runs against that table, not
-- `products` directly. Mirror the same trigram indexes there or the ones above
-- go unused on /find's hot path (confirmed by EXPLAIN ANALYZE below).
CREATE INDEX IF NOT EXISTS idx_mv_latest_prices_brand_trgm
    ON mv_latest_prices USING GIN (brand gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_mv_latest_prices_model_trgm
    ON mv_latest_prices USING GIN (model gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_mv_latest_prices_brand_model_trgm
    ON mv_latest_prices USING GIN ((brand || ' ' || model) gin_trgm_ops);
