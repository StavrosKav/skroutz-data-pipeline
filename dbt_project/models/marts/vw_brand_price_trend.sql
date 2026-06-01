SELECT
    p.category,
    p.brand,
    s.date,
    ROUND(AVG(s.price_eur)::NUMERIC, 2) AS avg_price,
    COUNT(DISTINCT s.product_id)         AS product_count
FROM {{ ref('stg_price_snapshots') }} s
JOIN {{ ref('stg_products') }} p ON p.id = s.product_id
WHERE p.brand IS NOT NULL
GROUP BY p.category, p.brand, s.date
