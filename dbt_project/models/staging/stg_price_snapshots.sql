-- Staging model for the raw price_snapshots table.

SELECT
    id,
    product_id,
    date,
    price_eur,
    installments_per_month,
    installments_in_total,
    rating,
    reviews
FROM {{ source('skroutz_raw', 'price_snapshots') }}
