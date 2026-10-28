"""Prompt variants for the model/prompt comparison.

v0 = schema prompt without the hints added on 2026-10-28 (NULLS LAST,
     search product_name, median != AVG).
v1 = the production prompt (agents.ask.agent.SQL_SYSTEM).
v2 = v1 + 3 few-shot examples. The examples are deliberately NOT eval
     questions (different categories, tables and shapes) to avoid leakage.
"""

from __future__ import annotations

from agents.ask import agent as _agent
from agents.ask.schema import SCHEMA_CONTEXT

V1_HINT_LINES = (
    "Search for a product/model name in product_name",
    "When sorting by price use NULLS LAST",
    "When the question asks for the median",
)


def _schema_without_hints() -> str:
    lines = [ln for ln in SCHEMA_CONTEXT.splitlines()
             if not any(ln.startswith(h) for h in V1_HINT_LINES)]
    return "\n".join(lines) + "\n"


FEW_SHOT = """\
Examples (style only; adapt to the question):
Q: Ποια 5 Lenovo laptops που πωλούνται σήμερα απέχουν περισσότερο από το ιστορικό χαμηλό τους;
```sql
SELECT l.product_name, l.price_eur, f.all_time_low, l.price_eur - f.all_time_low AS above_low
FROM mv_latest_prices l JOIN mv_price_floor f ON f.product_id = l.id
WHERE l.category = 'laptop' AND l.brand ILIKE 'lenovo'
  AND l.last_price_date = (SELECT MAX(date) FROM price_snapshots)
ORDER BY above_low DESC NULLS LAST LIMIT 5
```
Q: Ποια είναι η διάμεση τιμή των laptops με 32GB RAM σήμερα;
```sql
SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY l.price_eur) AS median_price
FROM mv_latest_prices l JOIN products p ON p.id = l.id
WHERE l.category = 'laptop' AND p.ram_gb = 32
  AND l.last_price_date = (SELECT MAX(date) FROM price_snapshots) LIMIT 1
```
Q: How many tablets with "Lenovo Tab" in the title are no longer listed?
```sql
SELECT COUNT(*) AS n FROM products
WHERE category = 'tablet' AND product_name ILIKE '%lenovo tab%'
  AND last_seen < (SELECT MAX(date) FROM price_snapshots) LIMIT 1
```
"""


def sql_system(variant: str) -> str:
    v1 = _agent.SQL_SYSTEM
    if variant == "v1":
        return v1
    if variant == "v0":
        return v1.replace(SCHEMA_CONTEXT, _schema_without_hints())
    if variant == "v2":
        return v1 + "\n" + FEW_SHOT
    raise ValueError(f"unknown prompt variant {variant!r} (v0, v1, v2)")


VARIANTS = ("v0", "v1", "v2")
