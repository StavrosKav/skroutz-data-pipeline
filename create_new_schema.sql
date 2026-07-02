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
-- Static fields (brand, model, specs) are written on first insert and not updated.
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
