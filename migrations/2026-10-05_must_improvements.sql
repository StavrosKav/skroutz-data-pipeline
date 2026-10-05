-- =============================================================================
-- migrations/2026-10-05_must_improvements.sql
-- =============================================================================
-- One-off, idempotent migration applied to the live SkroutzPR DB on 2026-10-05.
-- Safe to re-run: every statement is IF NOT EXISTS / guarded / NULL-only.
--
--   1. pipeline_runs table (run log written by run_pipeline.py)
--   2. Backfill products.ram_gb / storage_gb from product_name for tablets and
--      phones — only where the target column IS NULL (never overwrites).
--   3. CHECK constraints (added NOT VALID, then VALIDATEd, so a violation
--      fails loudly here instead of during a pipeline load).
--
-- create_new_schema.sql carries the same objects for fresh installs.
-- Run:  psql -v ON_ERROR_STOP=1 -f migrations/2026-10-05_must_improvements.sql
-- =============================================================================

\echo '--- pre-flight: CHECK violations (all must be 0) ---'
SELECT
    (SELECT COUNT(*) FROM products
      WHERE category NOT IN ('phone', 'laptop', 'smartwatch', 'tablet'))      AS bad_category,
    (SELECT COUNT(*) FROM price_snapshots
      WHERE price_eur IS NOT NULL AND price_eur <= 0)                         AS bad_price,
    (SELECT COUNT(*) FROM price_snapshots
      WHERE rating IS NOT NULL AND (rating < 0 OR rating > 5))                AS bad_rating;


-- ── 1. pipeline_runs ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id               SERIAL PRIMARY KEY,
    run_date         DATE        NOT NULL,                 -- PIPELINE_DATE of the run
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ,
    status           VARCHAR(20) NOT NULL DEFAULT 'running',
    failed_stage     TEXT,
    exit_code        INTEGER,
    error            TEXT,
    snapshots_loaded INTEGER,                              -- price_snapshots rows for run_date at finish
    new_products     INTEGER,                              -- products first_seen = run_date at finish
    stage_notes      JSONB       NOT NULL DEFAULT '[]'::jsonb,  -- [{stage, status, elapsed_s, ...}]
    skip_scrape      BOOLEAN,
    host             TEXT,
    pid              INTEGER,
    CONSTRAINT chk_pipeline_runs_status
        CHECK (status IN ('running', 'success', 'failed', 'skipped'))
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_run_date
    ON pipeline_runs (run_date);


-- ── 2. RAM / storage backfill (NULL-only) ────────────────────────────────────
-- Regex + sanity guard mirror clean_common.parse_ram_storage().
\echo '--- backfill candidates ---'
WITH parsed AS (
    SELECT id, category, ram_gb, storage_gb,
           regexp_match(product_name,
               '\(\s*(\d+)\s*(?:GB)?\s*/\s*(\d+(?:\.\d+)?)\s*(GB|TB)\s*[/)]', 'i') AS r
    FROM products
    WHERE category IN ('tablet', 'phone')
      AND (ram_gb IS NULL OR storage_gb IS NULL)
), vals AS (
    SELECT id, category, ram_gb, storage_gb,
           r[1]::int AS ram,
           ROUND(CASE WHEN upper(r[3]) = 'TB' THEN r[2]::numeric * 1000
                      ELSE r[2]::numeric END)::int AS storage
    FROM parsed WHERE r IS NOT NULL
)
SELECT category, COUNT(*) AS will_fill
FROM vals
WHERE ram BETWEEN 1 AND 64 AND storage > ram
GROUP BY category ORDER BY category;

BEGIN;
WITH parsed AS (
    SELECT id,
           regexp_match(product_name,
               '\(\s*(\d+)\s*(?:GB)?\s*/\s*(\d+(?:\.\d+)?)\s*(GB|TB)\s*[/)]', 'i') AS r
    FROM products
    WHERE category IN ('tablet', 'phone')
      AND (ram_gb IS NULL OR storage_gb IS NULL)
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
  AND v.ram BETWEEN 1 AND 64
  AND v.storage > v.ram
  AND (p.ram_gb IS NULL OR p.storage_gb IS NULL);
COMMIT;


-- ── 3. CHECK constraints ─────────────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_products_category') THEN
        ALTER TABLE products ADD CONSTRAINT chk_products_category
            CHECK (category IN ('phone', 'laptop', 'smartwatch', 'tablet')) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_snapshots_price_positive') THEN
        ALTER TABLE price_snapshots ADD CONSTRAINT chk_snapshots_price_positive
            CHECK (price_eur IS NULL OR price_eur > 0) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_snapshots_rating_range') THEN
        ALTER TABLE price_snapshots ADD CONSTRAINT chk_snapshots_rating_range
            CHECK (rating IS NULL OR rating BETWEEN 0 AND 5) NOT VALID;
    END IF;
END $$;

ALTER TABLE products        VALIDATE CONSTRAINT chk_products_category;
ALTER TABLE price_snapshots VALIDATE CONSTRAINT chk_snapshots_price_positive;
ALTER TABLE price_snapshots VALIDATE CONSTRAINT chk_snapshots_rating_range;
