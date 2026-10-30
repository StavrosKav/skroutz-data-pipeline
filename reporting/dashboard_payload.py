"""
dashboard_payload.py
--------------------
Shrinks the data embedded in the dashboard and adds freshness info.

- compact_products / expand_products: list of dicts <-> columns + rows, with the
  common Skroutz URL prefix stripped. `name` is only sent when `model` is
  empty (the page falls back to it then); otherwise it is rebuilt as brand + model. The page expands it back in JS
  (expandProducts in templates/dashboard.html must stay in step with this).
- compact_history / expand_history: one shared date axis + aligned price arrays.
- downsample: a few evenly spaced points for the per-product sparkline.
- freshness: when the page was built, how old its data is, and when to warn.
"""
import datetime

URL_PREFIX = "https://www.skroutz.gr/s/"
STALE_AFTER_HOURS = 30   # daily run + 6 h grace
STALE_LAG_DAYS = 2       # data this many days older than the run = stale at once


def compact_products(products):
    cols = list(products[0].keys()) if products else []
    rows = []
    for p in products:
        row = []
        for c in cols:
            v = p.get(c)
            if c == "url" and isinstance(v, str) and v.startswith(URL_PREFIX):
                v = v[len(URL_PREFIX):]
            elif c == "name" and p.get("model"):
                v = None   # the page only shows/searches name when model is empty
            row.append(v)
        rows.append(row)
    return {"cols": cols, "rows": rows, "url_prefix": URL_PREFIX}


def expand_products(packed):
    cols, prefix = packed["cols"], packed["url_prefix"]
    out = []
    for row in packed["rows"]:
        p = dict(zip(cols, row))
        if p.get("name") is None and "name" in p:
            p["name"] = f"{p.get('brand') or ''} {p.get('model') or ''}".strip()
        u = p.get("url")
        if isinstance(u, str) and u and not u.startswith("http"):
            p["url"] = prefix + u
        out.append(p)
    return out


def compact_history(history):
    dates = sorted({pt["date"] for pts in history.values() for pt in pts})
    idx = {d: i for i, d in enumerate(dates)}
    series = {}
    for pid, pts in history.items():
        arr = [None] * len(dates)
        for pt in pts:
            arr[idx[pt["date"]]] = pt["price"]
        series[str(pid)] = arr
    return {"dates": dates, "series": series}


def expand_history(packed):
    dates = packed["dates"]
    return {pid: [{"date": dates[i], "price": v} for i, v in enumerate(arr) if v is not None]
            for pid, arr in packed["series"].items()}


def downsample(values, n=8):
    """n evenly spaced points, always keeping the first and last value."""
    values = list(values)
    if len(values) <= n:
        return values
    step = (len(values) - 1) / (n - 1)
    return [values[round(i * step)] for i in range(n)]


def freshness(generated_at, last_updated):
    """generated_at: aware datetime of the run; last_updated: date of newest snapshot."""
    lag = (generated_at.date() - last_updated).days
    return {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "stale_at": (generated_at + datetime.timedelta(hours=STALE_AFTER_HOURS)).isoformat(timespec="seconds"),
        "lag_days": lag,
        "stale_now": lag >= STALE_LAG_DAYS,
    }
