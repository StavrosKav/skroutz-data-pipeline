"""What counts as a price drop (single source of truth for Python callers).

A product's drop today is measured against its *reference price*: the lower of
yesterday's price and the median of its previous 7 snapshots. The median
ignores a one-day spike (e.g. the cheap shop briefly out of stock), and the
"lower of" rule stops a product that already fell from being counted again
every day for a week. At least MIN_HISTORY earlier snapshots are required.

sql/drops_7d.sql implements the same rule for mv_drops_7d / vw_drops_7d.
"""
from statistics import median

BASELINE_SNAPSHOTS = 7
MIN_HISTORY = 3
# Drops this big (percent) are shown but flagged for a manual look.
BIG_DROP_PCT = 40.0
BIG_DROP_LABEL = "μεγάλη πτώση, έλεγξε"
# The dashboard is English-only (tests enforce no Greek), so it uses this twin.
BIG_DROP_LABEL_EN = "big drop, check"


def drop_vs_baseline(history, today):
    """history: earlier prices, oldest first. Returns None when today is not a
    drop, else {"ref_price", "baseline", "drop_eur", "drop_pct"}."""
    hist = [float(p) for p in history if p is not None][-BASELINE_SNAPSHOTS:]
    if len(hist) < MIN_HISTORY or today is None:
        return None
    yesterday, base = hist[-1], median(hist)
    today = float(today)
    if not (today < yesterday and today < base):
        return None
    ref = min(yesterday, base)
    return {
        "ref_price": round(ref, 2),
        "baseline": round(base, 2),
        "drop_eur": round(today - ref, 2),
        "drop_pct": round(100.0 * (today - ref) / ref, 2),
    }


def is_big_drop(drop_pct, threshold=BIG_DROP_PCT):
    """True when a drop is threshold percent or larger (drop_pct is negative)."""
    return drop_pct is not None and abs(float(drop_pct)) >= threshold
