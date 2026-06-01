SELECT
    product_id,
    ROUND(STDDEV(price_eur)::NUMERIC, 2)                                       AS stddev_price,
    ROUND((STDDEV(price_eur) / NULLIF(AVG(price_eur), 0) * 100)::NUMERIC, 1)  AS cv_pct,
    COUNT(*)                                                                    AS snap_count
FROM {{ ref('stg_price_snapshots') }}
WHERE date >= CURRENT_DATE - 30
GROUP BY product_id
