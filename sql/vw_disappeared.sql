-- Applied by run_pipeline.refresh_matviews() on every run (idempotent, no migration).
-- "Not seen for more than 30 days": the daily scrape sees only part of the
-- catalog, so shorter gaps mostly mean "not seen", not "gone".
CREATE OR REPLACE VIEW vw_disappeared AS
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
FROM products
WHERE last_seen < CURRENT_DATE - INTERVAL '30 days'
ORDER BY last_seen DESC;
