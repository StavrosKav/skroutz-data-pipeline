-- mv_drops_7d / vw_drops_7d: price drops measured against a 7-snapshot baseline.
-- Rule (mirrored in reporting/drops.py): reference price = LEAST(yesterday,
-- median of the previous 7 snapshots); a drop needs >= 3 earlier snapshots and
-- today below both. prev_price is the reference price, so drop_eur/drop_pct
-- keep the same meaning as in vw_biggest_drops (which stays day-over-day).
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_drops_7d AS
WITH w AS (
    SELECT s.product_id, s.date, s.price_eur,
           LAG(s.price_eur) OVER pw AS yesterday_price,
           array_agg(s.price_eur) FILTER (WHERE s.price_eur IS NOT NULL)
               OVER (pw ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING) AS hist
    FROM price_snapshots s
    WINDOW pw AS (PARTITION BY s.product_id ORDER BY s.date)
), m AS (
    SELECT w.*, COALESCE(cardinality(w.hist), 0) AS n_hist,
           (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY v) FROM unnest(w.hist) v)::numeric AS baseline
    FROM w
)
SELECT p.id AS product_id, p.category, p.brand, p.model, p.product_name,
       m.date                                        AS drop_date,
       ROUND(LEAST(m.yesterday_price, m.baseline), 2) AS prev_price,
       m.price_eur                                   AS new_price,
       ROUND(m.price_eur - LEAST(m.yesterday_price, m.baseline), 2) AS drop_eur,
       ROUND(100.0 * (m.price_eur - LEAST(m.yesterday_price, m.baseline))
             / NULLIF(LEAST(m.yesterday_price, m.baseline), 0), 2)  AS drop_pct,
       p.skroutz_link,
       m.yesterday_price,
       ROUND(m.baseline, 2)                          AS baseline_7d,
       m.n_hist
FROM m JOIN products p ON p.id = m.product_id
WHERE m.n_hist >= 3
  AND m.price_eur < m.yesterday_price
  AND m.price_eur < m.baseline
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_drops_7d_pid_date ON mv_drops_7d (product_id, drop_date);
CREATE INDEX IF NOT EXISTS idx_mv_drops_7d_date ON mv_drops_7d (drop_date);

CREATE OR REPLACE VIEW vw_drops_7d AS
SELECT * FROM mv_drops_7d
ORDER BY drop_eur ASC;
