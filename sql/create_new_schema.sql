-- =============================================================================
-- create_new_schema.sql
-- =====================
-- Defines the normalised PostgreSQL schema for the Skroutz price-tracking project.
--
-- Design rationale
-- ----------------
-- The original schema used one flat table per category (phones, laptops, etc.),
-- inserting a full duplicate row every day.  This new design separates concerns:
--
--   products        → static metadata about each unique product (scraped once)
--   price_snapshots → daily observations: price, rating, installments
--
-- Benefits:
--   • No metadata duplication across days
--   • Clean time-series queries ("how has this phone's price changed over 30 days?")
--   • A single JOIN retrieves the full picture for any product on any date
--
-- Run this script ONCE in DBeaver or psql against the SkroutzPR database.
-- The old tables are left intact as a backup until the migration is verified.
-- =============================================================================


-- ── products ──────────────────────────────────────────────────────────────────
-- One row per unique product, identified by its canonical skroutz URL.
-- Metadata (brand, model, specs, ram_gb, …) is written on first insert; later
-- loads only fill columns that are still NULL (COALESCE in 4csvsTOsql.py) and
-- never overwrite an existing value.
-- first_seen / last_seen track the date range during which the product was observed.

CREATE TABLE IF NOT EXISTS products (
    id             SERIAL PRIMARY KEY,
    category       VARCHAR(20)  NOT NULL,       -- 'phone' | 'laptop' | 'tablet' | 'smartwatch'
    skroutz_link   TEXT         UNIQUE NOT NULL, -- canonical URL; used as the natural key
    product_name   TEXT,                         -- full name as shown on skroutz
    brand          VARCHAR(100),
    model          TEXT,
    specs          TEXT,                         -- raw spec string scraped from the listing card

    -- Phone / tablet specific fields (NULL for laptops and smartwatches)
    ram_gb         INTEGER,
    storage_gb     INTEGER,
    num_cameras    INTEGER,                      -- main camera megapixels
    camera_type    VARCHAR(50),
    display_inches NUMERIC(4,1),
    battery_info   VARCHAR(50),
    display_info   TEXT,
    color          VARCHAR(100),

    first_seen     DATE,                         -- date this product was first scraped
    last_seen      DATE                          -- date of the most recent scrape
);


-- ── price_snapshots ───────────────────────────────────────────────────────────
-- One row per product per day.  All mutable fields (price, rating, reviews,
-- installment plan) live here so changes over time are fully preserved.

CREATE TABLE IF NOT EXISTS price_snapshots (
    id                     SERIAL PRIMARY KEY,
    product_id             INTEGER NOT NULL REFERENCES products(id),
    date                   DATE    NOT NULL,
    price_eur              NUMERIC(10,2),
    installments_per_month NUMERIC(8,2),         -- monthly payment amount in EUR
    installments_in_total  NUMERIC(8,2),          -- total number of installments
    rating                 NUMERIC(3,1),          -- skroutz user rating (0.0 – 5.0)
    reviews                INTEGER,               -- number of user reviews

    UNIQUE (product_id, date)                     -- one snapshot per product per day
);


-- ── Indexes ───────────────────────────────────────────────────────────────────
-- Optimise the two most common query patterns:
--   1. Time-series queries for a single product ("show me price history for product X")
--   2. Filtering / aggregation by brand or category

-- Pattern 1 needs no extra index: UNIQUE(product_id, date) already provides
-- a btree on exactly those columns.

CREATE INDEX IF NOT EXISTS idx_products_brand
    ON products (brand);

CREATE INDEX IF NOT EXISTS idx_products_category
    ON products (category);

-- Disappeared-product alerts / dashboard filter on last_seen ranges.
CREATE INDEX IF NOT EXISTS idx_products_last_seen
    ON products (last_seen);

-- Daily snapshot counts and per-date scans (README stats, coverage check).
CREATE INDEX IF NOT EXISTS idx_price_snapshots_date
    ON price_snapshots (date);


-- ── CHECK constraints ─────────────────────────────────────────────────────────
-- Guarded so this file stays re-runnable against an existing database.
-- 4csvsTOsql.py already maps price <= 0 and rating outside 0–5 to NULL, so a
-- bad scrape value cannot abort a load on these constraints.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_products_category') THEN
        ALTER TABLE products ADD CONSTRAINT chk_products_category
            CHECK (category IN ('phone', 'laptop', 'smartwatch', 'tablet'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_snapshots_price_positive') THEN
        ALTER TABLE price_snapshots ADD CONSTRAINT chk_snapshots_price_positive
            CHECK (price_eur IS NULL OR price_eur > 0);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_snapshots_rating_range') THEN
        ALTER TABLE price_snapshots ADD CONSTRAINT chk_snapshots_rating_range
            CHECK (rating IS NULL OR rating BETWEEN 0 AND 5);
    END IF;
END $$;


-- ── pipeline_runs ─────────────────────────────────────────────────────────────
-- One row per run_pipeline.py execution: start/end, final status, the fatal
-- stage (if any) and per-stage notes. Written best-effort — logging failures
-- never affect the pipeline.

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
