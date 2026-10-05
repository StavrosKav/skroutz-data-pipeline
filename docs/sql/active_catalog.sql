-- =============================================================================
-- docs/sql/active_catalog.sql
-- =============================================================================
-- Active catalog helper: products observed on the current calendar day
-- (last_seen = CURRENT_DATE). The canonical view lives in analytics.sql as
-- vw_active_products; this file is a standalone copy for ad-hoc use in
-- DBeaver / psql plus a few example queries.
--
-- Plain VIEW — no REFRESH required.
-- Safe / additive: does not alter tables or dashboard HTML.
-- =============================================================================

CREATE OR REPLACE VIEW vw_active_products AS
SELECT
    id,
    category,
    brand,
    model,
    product_name,
    specs,
    ram_gb,
    storage_gb,
    first_seen,
    last_seen,
    skroutz_link
FROM products
WHERE last_seen = CURRENT_DATE
ORDER BY category, brand, model;


-- Example 1: active product counts by category
-- SELECT category, COUNT(*) AS n
-- FROM vw_active_products
-- GROUP BY category
-- ORDER BY category;

-- Example 2: active products joined to latest price
-- SELECT a.category, a.brand, a.model, a.product_name,
--        lp.price_eur, lp.rating, lp.reviews, lp.last_price_date
-- FROM vw_active_products a
-- JOIN vw_latest_prices lp ON lp.id = a.id
-- ORDER BY a.category, lp.price_eur NULLS LAST
-- LIMIT 50;

-- Example 3: active phones still missing RAM (data-quality check)
-- SELECT COUNT(*) AS active_phones_missing_ram
-- FROM vw_active_products
-- WHERE category = 'phone' AND ram_gb IS NULL;
