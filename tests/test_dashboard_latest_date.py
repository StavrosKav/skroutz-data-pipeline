"""
tests/test_dashboard_latest_date.py
2026-10-31 dashboard audit.
1. The "Last Updated" card showed 20,261,030: countUp() parsed the date
   2026-10-30 as a number. Dates must be left as text.
2. The dashboard queries and five views used CURRENT_DATE. On 2026-10-31,
   before the 10:00 scrape, vw_active_products returned 0 rows (latest scrape
   2026-10-30). "Today" must mean the latest scrape day. No network or DB.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LATEST = "(SELECT MAX(date) FROM price_snapshots)"


def _func(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    return src[start:src.index("\n}\n", start)]


def test_countup_skips_dates():
    tpl = (ROOT / "templates" / "dashboard.html").read_text(encoding="utf-8")
    body = _func(tpl, "countUp")
    assert re.search(r"\\d\{4\}-\\d\{2\}-\\d\{2\}", body), "countUp must skip YYYY-MM-DD values"


def test_dashboard_python_has_no_current_date():
    for rel in ("reporting/queries.py", "reporting/generate_dashboard.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "CURRENT_DATE" not in src, rel


def _block(sql: str, header: str) -> str:
    start = sql.index(header)
    return sql[start:sql.find(";", start)]


def test_remaining_views_use_latest_scrape_date():
    sql = (ROOT / "sql" / "analytics.sql").read_text(encoding="utf-8")
    for header in ("CREATE OR REPLACE VIEW vw_active_products",
                   "CREATE OR REPLACE VIEW vw_price_volatility AS\nSELECT\n",
                   "CREATE OR REPLACE VIEW vw_brand_discount_freq",
                   "CREATE MATERIALIZED VIEW IF NOT EXISTS mv_price_volatility"):
        body = _block(sql, header)
        assert "CURRENT_DATE" not in body, header
        assert LATEST in body, header
