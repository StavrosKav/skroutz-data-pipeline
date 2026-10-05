-- =============================================================================
-- migrations/2026-10-05_phone_ram_backfill.sql
-- =============================================================================
-- One-off Should #3: fill products.ram_gb (and storage_gb when also NULL) for
-- phones where ram_gb IS NULL and product_name matches the shared parser
-- pattern in clean_common.parse_ram_storage().
--
-- NULL-only: never overwrites a non-NULL value.
-- Idempotent: re-run is a no-op once candidates are filled.
--
-- Prefer the companion Python runner (migrations/run_phone_ram_backfill.py)
-- which writes logs/ + an undo CSV before applying the UPDATE.
-- =============================================================================

\echo --- phone RAM backfill candidates (must match shared parser) ---
WITH parsed AS (
    SELECT id, product_name, ram_gb, storage_gb,
           regexp_match(product_name,
               '\(\s*(\d+)\s*(?:GB)?\s*/\s*(\d+(?:\.\d+)?)\s*(GB|TB)\s*[/)]', 'i') AS r
    FROM products
    WHERE category = 'phone'
      AND ram_gb IS NULL
), vals AS (
    SELECT id, product_name, ram_gb, storage_gb,
           r[1]::int AS ram,
           ROUND(CASE WHEN upper(r[3]) = 'TB' THEN r[2]::numeric * 1000
                      ELSE r[2]::numeric END)::int AS storage
    FROM parsed WHERE r IS NOT NULL
)
SELECT COUNT(*) AS will_fill
FROM vals
WHERE ram BETWEEN 1 AND 64 AND storage > ram;

BEGIN;
WITH parsed AS (
    SELECT id,
           regexp_match(product_name,
               '\(\s*(\d+)\s*(?:GB)?\s*/\s*(\d+(?:\.\d+)?)\s*(GB|TB)\s*[/)]', 'i') AS r
    FROM products
    WHERE category = 'phone'
      AND ram_gb IS NULL
), vals AS (
    SELECT id,
           r[1]::int AS ram,
           ROUND(CASE WHEN upper(r[3]) = 'TB' THEN r[2]::numeric * 1000
                      ELSE r[2]::numeric END)::int AS storage
    FROM parsed WHERE r IS NOT NULL
)
UPDATE products p
SET ram_gb     = COALESCE(p.ram_gb,     v.ram),
    storage_gb = COALESCE(p.storage_gb, v.storage)
FROM vals v
WHERE p.id = v.id
  AND p.ram_gb IS NULL
  AND v.ram BETWEEN 1 AND 64
  AND v.storage > v.ram;
COMMIT;
