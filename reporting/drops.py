"""What counts as a price drop (single source of truth for Python callers).

A product's drop today is measured against its *reference price*: the lower of
yesterday's price and the median of its previous 7 snapshots. The median
ignores a one-day spike (e.g. the cheap shop briefly out of stock), and the
"lower of" rule stops a product that already fell from being counted again
every day for a week. At least MIN_HISTORY earlier snapshots are required.

sql/drops_7d.sql implements the same rule for mv_drops_7d / vw_drops_7d.
"""
from collections import defaultdict
from statistics import median

BASELINE_SNAPSHOTS = 7
MIN_HISTORY = 3
# Drops this big (percent) are shown but flagged for a manual look.
BIG_DROP_PCT = 40.0
BIG_DROP_LABEL = "μεγάλη πτώση, έλεγξε"
# The dashboard is English-only (tests enforce no Greek), so it uses this twin.
BIG_DROP_LABEL_EN = "big drop, check"

# A drop is shown and counted only when it is at least MIN_DROP_PCT percent AND
# at least MIN_DROP_EUR euros. On 2 Nov 2026, 370 of 402 raw drops were under 5%
# (mostly one -3.5%..-3.8% repricing of laptops and tablets) and 283 were under
# 5 euros; "OR 20 euros" would have let the 3.7% cuts on 3,000 euro laptops in.
# vw_drops_7d stays unfiltered (the ask bot uses it); consumers add this filter.
MIN_DROP_PCT = 5.0
MIN_DROP_EUR = 5.0
MEANINGFUL_DROP_SQL = f"(drop_pct <= -{MIN_DROP_PCT} AND drop_eur <= -{MIN_DROP_EUR})"

# BULK_MIN_PRODUCTS or more drops on the same day, in the same category, with
# the same size (drop_pct rounded to BULK_STEP_PCT) are one shop-wide repricing:
# shown as one line, still counted per product.
BULK_MIN_PRODUCTS = 5
BULK_STEP_PCT = 0.5


def is_meaningful_drop(drop_pct, drop_eur):
    """True when a drop passes both minimums (both values are negative)."""
    if drop_pct is None or drop_eur is None:
        return False
    return float(drop_pct) <= -MIN_DROP_PCT and float(drop_eur) <= -MIN_DROP_EUR


def group_bulk(drops):
    """Collapse same-day, same-category, same-size drops into one bulk line.

    drops: dicts with category, drop_date, drop_pct, drop_eur, brand.
    Groups smaller than BULK_MIN_PRODUCTS are returned unchanged, in order."""
    groups = defaultdict(list)
    for d in drops:
        key = (str(d.get("drop_date")), d.get("category"), round(float(d["drop_pct"]) / BULK_STEP_PCT))
        groups[key].append(d)
    out, done = [], set()
    for d in drops:
        key = (str(d.get("drop_date")), d.get("category"), round(float(d["drop_pct"]) / BULK_STEP_PCT))
        members = groups[key]
        if len(members) < BULK_MIN_PRODUCTS:
            out.append(d)
            continue
        if key in done:
            continue
        done.add(key)
        brands = sorted({m.get("brand") or "" for m in members} - {""},
                        key=lambda b: -sum(1 for m in members if m.get("brand") == b))
        shown = ", ".join(brands[:3]) + (f" +{len(brands) - 3}" if len(brands) > 3 else "")
        out.append({
            "bulk": True, "count": len(members), "brands": brands,
            "category": d.get("category"), "drop_date": d.get("drop_date"),
            "brand": f"{len(members)} products",
            "model": f"bulk repricing: {shown}",
            "product_name": f"bulk repricing of {len(members)} products",
            "prev_price": round(median(float(m["prev_price"]) for m in members), 2),
            "new_price": round(median(float(m["new_price"]) for m in members), 2),
            "drop_eur": min(float(m["drop_eur"]) for m in members),
            "drop_pct": round(median(float(m["drop_pct"]) for m in members), 1),
            "skroutz_link": "", "suspect": any(m.get("suspect") for m in members),
        })
    return out


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
