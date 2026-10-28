"""Grounded /analyze: fixed read-only queries -> facts -> LLM summary of those facts only.

Pure core: `execute(sql) -> (columns, rows)` and `llm(messages, max_tokens) -> str`
are injected (same shapes as agents.ask.runtime), so tests use fakes.
The category is resolved from a fixed allowlist before it reaches SQL.
"""

from __future__ import annotations

import html
from typing import Any, Callable, Sequence

CATEGORIES = ("phone", "laptop", "tablet", "smartwatch")

Executor = Callable[[str], "tuple[Sequence[str], Sequence[Sequence[Any]]]"]
LLM = Callable[[list, int], str]

_LATEST = "(SELECT MAX(date) FROM price_snapshots)"


def _queries(cat: str) -> dict[str, str]:
    if cat not in CATEGORIES:
        raise ValueError(f"unknown category {cat!r}")
    c = f"'{cat}'"
    return {
        "latest_date": "SELECT MAX(date) FROM price_snapshots",
        "overview": (
            "SELECT COUNT(*), ROUND(AVG(price_eur)::numeric, 2), "
            "ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price_eur)::numeric, 2), "
            "ROUND(MIN(price_eur)::numeric, 2), ROUND(MAX(price_eur)::numeric, 2) "
            f"FROM mv_latest_prices WHERE category = {c} AND last_price_date = {_LATEST}"
        ),
        "index_30d": (
            "SELECT date, ROUND(avg_price::numeric, 2) FROM mv_daily_market_index "
            f"WHERE category = {c} AND date IN ({_LATEST}, "
            f"(SELECT MAX(date) FROM mv_daily_market_index WHERE category = {c} "
            f"AND date <= {_LATEST} - 30)) ORDER BY date"
        ),
        "top_brands": (
            "SELECT brand, product_count, ROUND(avg_price::numeric, 2) FROM mv_brand_summary "
            f"WHERE category = {c} AND brand IS NOT NULL AND brand <> '' "
            "AND LOWER(brand) NOT IN ('none', 'smartwatch', 'unknown') "
            "ORDER BY product_count DESC LIMIT 5"
        ),
        "drops_7d": (
            "SELECT COUNT(*), ROUND(AVG(drop_pct)::numeric, 1) FROM vw_biggest_drops "
            f"WHERE category = {c} AND drop_date > {_LATEST} - 7"
        ),
        "top_drops": (
            "SELECT brand, model, ROUND(prev_price::numeric, 2), ROUND(new_price::numeric, 2), "
            "ROUND(drop_pct::numeric, 1), drop_date FROM vw_biggest_drops "
            f"WHERE category = {c} AND drop_date > {_LATEST} - 7 ORDER BY drop_pct ASC LIMIT 3"
        ),
        "trend_mix": (
            "SELECT t.trend, COUNT(*) FROM mv_price_trend_direction t "
            "JOIN products p ON p.id = t.product_id "
            f"WHERE p.category = {c} AND p.last_seen = {_LATEST} GROUP BY t.trend ORDER BY t.trend"
        ),
    }


def _n(v: Any) -> str:
    return "—" if v is None else str(v)


def build_facts(execute: Executor, category: str) -> list[str]:
    """Run the fixed queries; return plain-text fact lines (the only LLM input)."""
    from agents.ask.guard import validate_sql
    r = {}
    for k, sql in _queries(category).items():
        validate_sql(sql)  # same read-only/allowlist guard as /ask
        r[k] = list(execute(sql)[1])
    facts: list[str] = []
    latest = r["latest_date"][0][0] if r["latest_date"] else None
    facts.append(f"Category: {category}. Latest scrape date: {_n(latest)}.")
    if r["overview"]:
        cnt, avg, med, lo, hi = r["overview"][0]
        facts.append(f"Products priced on latest date: {_n(cnt)}; avg €{_n(avg)}, "
                     f"median €{_n(med)}, min €{_n(lo)}, max €{_n(hi)}.")
    idx = r["index_30d"]
    if len(idx) == 2 and idx[0][1] and idx[1][1]:
        (d0, a0), (d1, a1) = idx
        pct = (float(a1) - float(a0)) / float(a0) * 100
        facts.append(f"Category average price {d0}: €{a0} -> {d1}: €{a1} ({pct:+.1f}%).")
    else:
        facts.append("30-day category price change: not enough history.")
    if r["top_brands"]:
        facts.append("Top brands by product count: " + "; ".join(
            f"{b} {n} products avg €{a}" for b, n, a in r["top_brands"]) + ".")
    if r["drops_7d"]:
        n, avg = r["drops_7d"][0]
        facts.append(f"Price drops in last 7 days: {_n(n)} (avg {_n(avg)}%).")
    for b, m, p0, p1, pct, d in r["top_drops"]:
        facts.append(f"Big drop {d}: {b} {m} €{p0} -> €{p1} ({pct}%).")
    if r["trend_mix"]:
        facts.append("7d-vs-30d price trend of listed products: " + ", ".join(
            f"{t} {n}" for t, n in r["trend_mix"]) + ".")
    return facts


SYSTEM_PROMPT = (
    "You summarize Greek electronics price data from skroutz.gr. Use ONLY the facts given. "
    "Do not add brands, products, prices, percentages or market knowledge that are not in the facts. "
    "If something is not in the facts, do not mention it. Write 3-5 short sentences in Greek: "
    "price level, 30-day direction, most active brands, notable drops. "
    "The median is διάμεση τιμή and the average is μέση τιμή: never call the median an average."
)


def analyze(execute: Executor, llm: LLM | None, category: str, max_tokens: int = 400) -> str:
    """Telegram HTML: LLM summary (grounded) + the facts it was given."""
    facts = build_facts(execute, category)
    fact_block = "\n".join(f"• {html.escape(f)}" for f in facts)
    summary = ""
    if llm is not None:
        try:
            summary = llm([
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "Facts:\n" + "\n".join(facts)},
            ], max_tokens).strip()
        except Exception:
            summary = ""
    head = f"📊 <b>{category}</b>\n"
    if summary:
        return f"{head}{html.escape(summary)}\n\n<b>Δεδομένα</b> (από τη βάση):\n{fact_block}"
    return f"{head}<b>Δεδομένα</b> (από τη βάση):\n{fact_block}"
