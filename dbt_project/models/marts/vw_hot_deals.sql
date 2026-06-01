WITH latest_dates AS (
    SELECT DISTINCT date
    FROM {{ ref('stg_price_snapshots') }}
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
    FROM {{ ref('stg_price_snapshots') }} ps, date_pair dp
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
JOIN {{ ref('stg_products') }} p ON p.id = r.product_id
CROSS JOIN date_pair dp
WHERE r.price_latest IS NOT NULL
  AND r.price_prev   IS NOT NULL
  AND r.price_latest < r.price_prev
  AND COALESCE(r.reviews_latest - r.reviews_prev, 0) > 0
ORDER BY hot_score DESC
