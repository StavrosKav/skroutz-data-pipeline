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
FROM {{ ref('stg_products') }} p
JOIN {{ ref('stg_price_snapshots') }} s ON s.product_id = p.id
