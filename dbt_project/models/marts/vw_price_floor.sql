SELECT
    product_id,
    MIN(price_eur)  AS all_time_low,
    MAX(price_eur)  AS all_time_high,
    COUNT(*)        AS total_snapshots
FROM {{ ref('stg_price_snapshots') }}
WHERE price_eur IS NOT NULL
GROUP BY product_id
