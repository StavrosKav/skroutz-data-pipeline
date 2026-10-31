"""Per-category volume alarm (warn only, never fails a run).

A category whose snapshot count on the run date is below THRESHOLD of its
average over the recorded days in the 7 days before is reported, so a scraper
that silently loses one category shows up even when the total looks normal.
"""

from sqlalchemy import text

THRESHOLD = 0.85

VOLUME_SQL = """
    WITH per_day AS (
        SELECT p.category, s.date, COUNT(*) AS n
        FROM price_snapshots s JOIN products p ON p.id = s.product_id
        WHERE s.date >= CAST(:d AS date) - 7 AND s.date <= CAST(:d AS date)
        GROUP BY p.category, s.date
    )
    SELECT category,
           COALESCE(SUM(n) FILTER (WHERE date = CAST(:d AS date)), 0) AS today,
           AVG(n) FILTER (WHERE date < CAST(:d AS date))              AS avg7
    FROM per_day
    GROUP BY category
"""


def fetch(conn, run_date):
    """Return ({category: count on run_date}, {category: 7-day average})."""
    today, avg7 = {}, {}
    for cat, n, avg in conn.execute(text(VOLUME_SQL), {"d": str(run_date)}).fetchall():
        today[cat] = int(n or 0)
        if avg is not None:
            avg7[cat] = float(avg)
    return today, avg7


def category_volume_alerts(today, avg7, threshold=THRESHOLD):
    """Categories with today < threshold x their 7-day average, sorted by name."""
    out = []
    for cat in sorted(avg7):
        avg = avg7[cat]
        n = int(today.get(cat, 0))
        if avg > 0 and n < avg * threshold:
            out.append({"category": cat, "today": n, "avg7": round(avg, 1), "pct": round(n / avg * 100, 1)})
    return out
