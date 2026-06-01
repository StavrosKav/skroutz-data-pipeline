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
FROM {{ ref('stg_products') }} p
JOIN {{ ref('stg_price_snapshots') }} s ON s.product_id = p.id
ORDER BY p.id, s.date DESC
