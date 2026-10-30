"""
tests/test_latest_scrape_date.py
2026-10-30: "today" in these views and bot commands means the latest scrape
day (MAX(date) FROM price_snapshots), not the DB clock. On a day the scrape has
not run yet (or failed) CURRENT_DATE made /drops and /stats show 0 and shifted
the 30-day "disappeared" and 7/30-day trend windows. No network or DB.
"""

import inspect
import re
from pathlib import Path

from alerts import telegram_bot as bot

ROOT = Path(__file__).resolve().parent.parent
LATEST = "(SELECT MAX(date) FROM price_snapshots)"


def _block(sql: str, header: str) -> str:
    start = sql.index(header)
    end = sql.find(";", start)
    return sql[start:end]


def test_vw_disappeared_file_uses_latest_scrape_date():
    ddl = (ROOT / "sql" / "vw_disappeared.sql").read_text(encoding="utf-8")
    body = _block(ddl, "CREATE OR REPLACE VIEW vw_disappeared")
    assert "CURRENT_DATE" not in body
    assert LATEST in body


def test_analytics_views_use_latest_scrape_date():
    sql = (ROOT / "sql" / "analytics.sql").read_text(encoding="utf-8")
    for header in ("CREATE OR REPLACE VIEW vw_disappeared",
                   "CREATE OR REPLACE VIEW vw_price_trend_direction AS\nSELECT\n",
                   "CREATE MATERIALIZED VIEW IF NOT EXISTS mv_price_trend_direction"):
        body = _block(sql, header)
        assert "CURRENT_DATE" not in body, header
        assert LATEST in body, header


def test_bot_commands_use_latest_scrape_date():
    for fn in (bot._cmd_drops, bot._cmd_stats, bot._cmd_restock):
        src = inspect.getsource(fn)
        assert "CURRENT_DATE" not in src, fn.__name__
        assert re.search(r"MAX\(date\) FROM price_snapshots", src), fn.__name__
