"""The one snapshot-volume rule (warn only, never fails a run).

Baseline = median snapshot count over the recorded days in the LOOKBACK_DAYS
before the run date (needs MIN_PRIOR_DAYS of them). A category below THRESHOLD
of its baseline, or the total below TOTAL_THRESHOLD, is reported, so a scraper
that silently loses one category shows up even when the total looks normal.
Median, not average, so one odd day does not move the baseline.
"""

from sqlalchemy import text

THRESHOLD = 0.85        # per category
TOTAL_THRESHOLD = 0.80  # all categories together
LOOKBACK_DAYS = 7
MIN_PRIOR_DAYS = 3
TOTAL = "__total__"

VOLUME_SQL = """
    WITH daily AS (
        SELECT s.date, p.category::text AS category, COUNT(*) AS n
        FROM price_snapshots s JOIN products p ON p.id = s.product_id
        WHERE s.date BETWEEN CAST(:d AS date) - :lookback AND CAST(:d AS date)
        GROUP BY s.date, p.category
    ),
    allrows AS (
        SELECT date, category, n FROM daily
        UNION ALL SELECT date, '__total__', SUM(n) FROM daily GROUP BY date
    )
    SELECT category,
           COALESCE(MAX(n) FILTER (WHERE date = CAST(:d AS date)), 0) AS today_n,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY n)
               FILTER (WHERE date < CAST(:d AS date)) AS median_n,
           COUNT(*) FILTER (WHERE date < CAST(:d AS date)) AS prior_days
    FROM allrows
    GROUP BY category
    ORDER BY category
"""


def fetch(conn, run_date):
    """Return ({category: count on run_date}, {category: baseline median}).

    Both include TOTAL. Categories with fewer than MIN_PRIOR_DAYS recorded
    days get no baseline (not enough history to judge).
    """
    today, base = {}, {}
    rows = conn.execute(text(VOLUME_SQL), {"d": str(run_date), "lookback": LOOKBACK_DAYS}).fetchall()
    for cat, n, median, prior_days in rows:
        today[cat] = int(n or 0)
        if median and (prior_days or 0) >= MIN_PRIOR_DAYS:
            base[cat] = float(median)
    return today, base


def category_volume_alerts(today, base, threshold=THRESHOLD, total_threshold=TOTAL_THRESHOLD):
    """Entries below their threshold x baseline, sorted by name."""
    out = []
    for cat in sorted(base):
        b = base[cat]
        n = int(today.get(cat, 0))
        limit = total_threshold if cat == TOTAL else threshold
        if b > 0 and n < b * limit:
            out.append({"category": cat, "today": n, "base7": round(b, 1), "pct": round(n / b * 100, 1)})
    return out


def label(cat):
    return "TOTAL" if cat == TOTAL else cat
