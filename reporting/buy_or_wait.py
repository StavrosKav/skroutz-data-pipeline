"""
buy_or_wait.py
--------------
"Buy or wait" signal for the dashboard's Intelligence tab.

For every product listed in the latest scrape, compare today's price with the
product's own low / median / high over the last WINDOW_DAYS days. Only the days
we actually recorded count (the scraper missed some days), and a product needs
at least MIN_DAYS recorded days, so a few points never pass for a pattern.

    near 90-day low  today <= low * (1 + NEAR_LOW_PCT/100) and below the median
    above usual      today >= median * (1 + ABOVE_PCT/100)
    typical          everything else (also a price that never moved)

"How often it drops" = share of day-to-day steps (between recorded days) where
the price fell by DROP_STEP_PCT percent or more.
Prices are the cheapest shop's "from" price on Skroutz, as everywhere else.
A gap to the median of BIG_DROP_PCT percent or more is flagged "check" (often a
shop change or a different variant), like big drops on the Price Drops tab.
"""
from sqlalchemy import text

from reporting.drops import BIG_DROP_PCT

WINDOW_DAYS = 90
MIN_DAYS = 20
NEAR_LOW_PCT = 2.0
ABOVE_PCT = 5.0
DROP_STEP_PCT = 3.0
ROW_LIMIT = 150

NEAR_LOW, TYPICAL, ABOVE = "near_low", "typical", "above"

SQL = """
WITH mx AS (SELECT MAX(date) AS d FROM price_snapshots),
daily AS (
    SELECT s.product_id, s.date, MIN(s.price_eur) AS price
    FROM price_snapshots s, mx
    WHERE s.date > mx.d - :days AND s.price_eur IS NOT NULL
    GROUP BY s.product_id, s.date
),
steps AS (
    SELECT product_id, price,
           LAG(price) OVER (PARTITION BY product_id ORDER BY date) AS prev
    FROM daily
),
stats AS (
    SELECT product_id,
           COUNT(*)                                          AS n_days,
           MIN(price)                                        AS lo,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price) AS med,
           MAX(price)                                        AS hi,
           COUNT(*) FILTER (WHERE prev IS NOT NULL
                              AND price <= prev * (1 - :step / 100.0)) AS n_drops
    FROM steps
    GROUP BY product_id
)
SELECT p.id, p.skroutz_id, p.category, p.brand, p.model, p.product_name, p.skroutz_link,
       cur.price AS current, st.lo, st.med, st.hi, st.n_days, st.n_drops
FROM stats st
JOIN products p ON p.id = st.product_id
JOIN daily cur ON cur.product_id = st.product_id
              AND cur.date = (SELECT d FROM mx)
WHERE st.n_days >= :min_days
"""


def signal(current, lo, med, hi):
    """near_low / typical / above for one product (see module docstring)."""
    if hi - lo < 0.005 * med:          # price never really moved
        return TYPICAL
    if current <= lo * (1 + NEAR_LOW_PCT / 100) and current < med:
        return NEAR_LOW
    if current >= med * (1 + ABOVE_PCT / 100):
        return ABOVE
    return TYPICAL


def fetch(conn):
    return conn.execute(text(SQL), {"days": WINDOW_DAYS, "min_days": MIN_DAYS,
                                    "step": DROP_STEP_PCT}).fetchall()


def build(rows, limit=ROW_LIMIT):
    """Rows -> {"counts": {cat: {...}}, "near_low": [...], "above": [...], params}."""
    name_count = {}
    for r in rows:
        name_count[r.product_name] = name_count.get(r.product_name, 0) + 1
    counts, buckets = {}, {NEAR_LOW: [], ABOVE: []}
    for r in rows:
        cur, lo, med, hi = float(r.current), float(r.lo), float(r.med), float(r.hi)
        sig = signal(cur, lo, med, hi)
        c = counts.setdefault(r.category or "", {NEAR_LOW: 0, TYPICAL: 0, ABOVE: 0, "total": 0})
        c[sig] += 1
        c["total"] += 1
        if sig not in buckets:
            continue
        name = r.product_name or f"{r.brand or ''} {r.model or ''}".strip()
        if name_count.get(r.product_name, 0) > 1:
            name = f"{name} (#{r.skroutz_id})"
        steps = max(int(r.n_days) - 1, 1)
        buckets[sig].append({
            "id": r.id, "category": r.category or "", "name": name,
            "price": round(cur, 2), "lo": round(lo, 2), "med": round(med, 2), "hi": round(hi, 2),
            "days": int(r.n_days), "vs_median_pct": round((cur - med) / med * 100, 1),
            "drop_rate_pct": round(int(r.n_drops) / steps * 100), "url": r.skroutz_link or "",
            "suspect": (cur - med) / med * 100 <= -BIG_DROP_PCT,
        })
    near = sorted(buckets[NEAR_LOW], key=lambda x: x["vs_median_pct"])[:limit]
    above = sorted(buckets[ABOVE], key=lambda x: -x["vs_median_pct"])[:limit]
    return {"counts": counts, "near_low": near, "above": above,
            "params": {"window_days": WINDOW_DAYS, "min_days": MIN_DAYS,
                       "near_low_pct": NEAR_LOW_PCT, "above_pct": ABOVE_PCT,
                       "drop_step_pct": DROP_STEP_PCT}}
