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
FROM {{ ref('stg_products') }} p
JOIN {{ ref('stg_price_snapshots') }} s ON s.product_id = p.id
WHERE s.price_eur IS NOT NULL
GROUP BY p.category, p.brand
