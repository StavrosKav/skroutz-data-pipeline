-- =============================================================================
-- analytics.sql
-- =============
-- Analytical views for the Skroutz price-tracking database.
-- Run once in DBeaver or psql against the SkroutzPR database.
--
-- Views defined here:
--   vw_latest_prices       — each product with its most recent price snapshot
--   vw_price_history       — full daily price history with day-over-day change
--   vw_biggest_drops       — products with the largest single-day price drops
--   vw_brand_summary       — price stats per brand per category
--   vw_disappeared         — products that have not been seen in the last 7 days
--   vw_price_volatility    — 30-day coefficient of variation per product (deal quality signal)
--   vw_brand_price_trend   — daily avg price per brand/category (brand comparison over time)
--   vw_hot_deals           — products with price drop + review surge in the last 7 days
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
SELECT
    p.id            AS product_id,
    p.category,
    p.brand,
    p.model,
    p.product_name,
    s.date,
    s.price_eur,
    LAG(s.price_eur) OVER (PARTITION BY s.product_id ORDER BY s.date) AS prev_price,
    s.price_eur
        - LAG(s.price_eur) OVER (PARTITION BY s.product_id ORDER BY s.date)
                                                                       AS price_change,
    ROUND(
        100.0 * (
            s.price_eur
            - LAG(s.price_eur) OVER (PARTITION BY s.product_id ORDER BY s.date)
        )
        / NULLIF(
            LAG(s.price_eur) OVER (PARTITION BY s.product_id ORDER BY s.date),
            0
        ),
        2
    )                                                                  AS pct_change,
    s.rating,
    s.reviews,
    p.skroutz_link
FROM products p
JOIN price_snapshots s ON s.product_id = p.id;


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

CREATE INDEX IF NOT EXISTS idx_price_snapshots_product_date
    ON price_snapshots(product_id, date);
