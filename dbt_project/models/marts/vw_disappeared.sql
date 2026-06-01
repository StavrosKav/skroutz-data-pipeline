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
FROM {{ ref('stg_products') }}
WHERE last_seen < CURRENT_DATE - INTERVAL '7 days'
ORDER BY last_seen DESC
