-- =============================================================================
-- migrations_dedup_skroutz_id.sql
-- -------------------------------
-- One-off, idempotent: make products.skroutz_id (the number after /s/ in the
-- URL) the product key and merge rows that are the same Skroutz product under
-- a rewritten URL slug (audit 2026-10-28: 3,165 ids on 6,478 rows, never both
-- seen on the same day).
--
-- Merge rule per skroutz_id:
--   survivor = row with the latest last_seen (tie → highest id), so it already
--              carries the newest name / link / specs; NULL metadata is filled
--              from the most recent loser that has it.
--   first_seen = MIN, last_seen = MAX across the group.
--   price_snapshots repointed to the survivor; on a same-day clash the
--   survivor's snapshot is kept (expected 0).
--   losers deleted. Groups whose parsed ram_gb / storage_gb differ
--   are written to dedup_review for a manual look.
-- Run in one transaction:  psql -v ON_ERROR_STOP=1 -d <db> -f this_file
-- =============================================================================
BEGIN;

ALTER TABLE products ADD COLUMN IF NOT EXISTS skroutz_id BIGINT;
UPDATE products
SET    skroutz_id = substring(skroutz_link from '/s/(\d+)/')::bigint
WHERE  skroutz_id IS NULL AND skroutz_link ~ '/s/\d+/';

CREATE TEMP TABLE _grp ON COMMIT DROP AS
SELECT id, skroutz_id,
       first_value(id) OVER w AS survivor_id,
       row_number()    OVER w AS rn
FROM   products
WHERE  skroutz_id IN (SELECT skroutz_id FROM products
                      WHERE skroutz_id IS NOT NULL
                      GROUP BY 1 HAVING count(*) > 1)
WINDOW w AS (PARTITION BY skroutz_id ORDER BY last_seen DESC NULLS LAST, id DESC);

-- review list: groups whose parsed RAM or storage differ between rows
-- (a title correction by Skroutz, or possibly a different variant)
CREATE TABLE IF NOT EXISTS dedup_review (
    skroutz_id   BIGINT,
    product_id   INTEGER,
    survivor_id  INTEGER,
    product_name TEXT,
    skroutz_link TEXT,
    ram_gb       INTEGER,
    storage_gb   INTEGER,
    first_seen   DATE,
    last_seen    DATE,
    created_at   TIMESTAMPTZ DEFAULT now()
);
INSERT INTO dedup_review (skroutz_id, product_id, survivor_id, product_name, skroutz_link,
                          ram_gb, storage_gb, first_seen, last_seen)
SELECT g.skroutz_id, p.id, g.survivor_id, p.product_name, p.skroutz_link,
       p.ram_gb, p.storage_gb, p.first_seen, p.last_seen
FROM   _grp g JOIN products p ON p.id = g.id
WHERE  g.skroutz_id IN (
    SELECT g2.skroutz_id
    FROM   _grp g2 JOIN products p2 ON p2.id = g2.id
    GROUP  BY g2.skroutz_id
    HAVING count(DISTINCT p2.ram_gb) > 1 OR count(DISTINCT p2.storage_gb) > 1);

DO $$
DECLARE clashes int;
BEGIN
    SELECT count(*) INTO clashes
    FROM   price_snapshots l
    JOIN   _grp g ON g.id = l.product_id AND g.rn > 1
    JOIN   price_snapshots s ON s.product_id = g.survivor_id AND s.date = l.date;
    RAISE NOTICE 'same-day snapshot clashes (loser row dropped, survivor kept): %', clashes;
END $$;

DELETE FROM price_snapshots l
USING  _grp g, price_snapshots s
WHERE  g.id = l.product_id AND g.rn > 1
  AND  s.product_id = g.survivor_id AND s.date = l.date;

-- two losers of the same group on the same day: keep the newer-ranked one
DELETE FROM price_snapshots l
USING  _grp g, price_snapshots o, _grp go
WHERE  g.id = l.product_id AND g.rn > 1
  AND  o.date = l.date AND go.id = o.product_id
  AND  go.skroutz_id = g.skroutz_id AND go.rn > 1 AND go.rn < g.rn;

UPDATE price_snapshots ps
SET    product_id = g.survivor_id
FROM   _grp g
WHERE  g.id = ps.product_id AND g.rn > 1;

UPDATE products p
SET    first_seen     = a.first_seen,
       last_seen      = a.last_seen,
       brand          = COALESCE(p.brand,          a.brand),
       model          = COALESCE(p.model,          a.model),
       specs          = COALESCE(p.specs,          a.specs),
       ram_gb         = COALESCE(p.ram_gb,         a.ram_gb),
       storage_gb     = COALESCE(p.storage_gb,     a.storage_gb),
       num_cameras    = COALESCE(p.num_cameras,    a.num_cameras),
       camera_type    = COALESCE(p.camera_type,    a.camera_type),
       display_inches = COALESCE(p.display_inches, a.display_inches),
       battery_info   = COALESCE(p.battery_info,   a.battery_info),
       display_info   = COALESCE(p.display_info,   a.display_info),
       color          = COALESCE(p.color,          a.color)
FROM (
    SELECT g.survivor_id,
           min(x.first_seen) AS first_seen,
           max(x.last_seen)  AS last_seen,
           (array_agg(x.brand          ORDER BY g.rn) FILTER (WHERE x.brand          IS NOT NULL))[1] AS brand,
           (array_agg(x.model          ORDER BY g.rn) FILTER (WHERE x.model          IS NOT NULL))[1] AS model,
           (array_agg(x.specs          ORDER BY g.rn) FILTER (WHERE x.specs          IS NOT NULL))[1] AS specs,
           (array_agg(x.ram_gb         ORDER BY g.rn) FILTER (WHERE x.ram_gb         IS NOT NULL))[1] AS ram_gb,
           (array_agg(x.storage_gb     ORDER BY g.rn) FILTER (WHERE x.storage_gb     IS NOT NULL))[1] AS storage_gb,
           (array_agg(x.num_cameras    ORDER BY g.rn) FILTER (WHERE x.num_cameras    IS NOT NULL))[1] AS num_cameras,
           (array_agg(x.camera_type    ORDER BY g.rn) FILTER (WHERE x.camera_type    IS NOT NULL))[1] AS camera_type,
           (array_agg(x.display_inches ORDER BY g.rn) FILTER (WHERE x.display_inches IS NOT NULL))[1] AS display_inches,
           (array_agg(x.battery_info   ORDER BY g.rn) FILTER (WHERE x.battery_info   IS NOT NULL))[1] AS battery_info,
           (array_agg(x.display_info   ORDER BY g.rn) FILTER (WHERE x.display_info   IS NOT NULL))[1] AS display_info,
           (array_agg(x.color          ORDER BY g.rn) FILTER (WHERE x.color          IS NOT NULL))[1] AS color
    FROM   _grp g JOIN products x ON x.id = g.id
    GROUP  BY g.survivor_id
) a
WHERE p.id = a.survivor_id;

DELETE FROM products p USING _grp g WHERE g.id = p.id AND g.rn > 1;

CREATE UNIQUE INDEX IF NOT EXISTS products_skroutz_id_key ON products (skroutz_id);

REFRESH MATERIALIZED VIEW mv_latest_prices;
REFRESH MATERIALIZED VIEW mv_price_history;
REFRESH MATERIALIZED VIEW mv_price_floor;
REFRESH MATERIALIZED VIEW mv_price_volatility;
REFRESH MATERIALIZED VIEW mv_price_trend_direction;
REFRESH MATERIALIZED VIEW mv_brand_summary;
REFRESH MATERIALIZED VIEW mv_brand_price_trend;
REFRESH MATERIALIZED VIEW mv_daily_market_index;
REFRESH MATERIALIZED VIEW mv_restock_pricing;
REFRESH MATERIALIZED VIEW mv_review_velocity;

COMMIT;
