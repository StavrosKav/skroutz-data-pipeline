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
FROM {{ ref('vw_price_history') }}
WHERE price_change < 0
ORDER BY price_change ASC
