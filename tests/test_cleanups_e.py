"""
Audit item E: presentation cleanups. Fake brands parsed from titles
("Smartwatch", the Greek word for "smart") are hidden on the dashboard only
(DB untouched); fewer, clearer top cards with "vs yesterday"; the
"from" price note; vw_biggest_drops (no consumer) dropped with its grant.
"""
from pathlib import Path
from unittest.mock import MagicMock

from ops import create_ask_role
from reporting import generate_dashboard as gd
from reporting import queries

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates/dashboard.html").read_text(encoding="utf-8")


def test_fake_brands_hidden():
    assert gd.display_brand("Smartwatch") == ""
    assert gd.display_brand("\u0388\u03be\u03c5\u03c0\u03bd\u03bf") == ""
    assert gd.display_brand("Apple") == "Apple"
    assert gd.display_brand(None) == ""


def test_fake_brands_skipped_in_brand_lists():
    assert gd.is_real_brand("Samsung") and not gd.is_real_brand("Smartwatch") and not gd.is_real_brand(None)


def test_prev_day_counts_sql():
    conn = MagicMock()
    conn.execute.return_value.scalar.return_value = 7
    assert queries.count_listed(conn, prev=True) == 7
    sql = " ".join(str(conn.execute.call_args.args[0]).split())
    assert "date < (SELECT MAX(date) FROM price_snapshots)" in sql
    assert queries.count_drops_prev_day(conn) == 7
    sql = " ".join(str(conn.execute.call_args.args[0]).split())
    assert "FROM vw_drops_7d" in sql and "date < (SELECT MAX(date) FROM price_snapshots)" in sql


def test_fewer_cards_with_vs_yesterday():
    stats = HTML[HTML.index("function buildStats()"):HTML.index("document.getElementById('stat-cards')")]
    assert "Price Snapshots" not in stats and "Biggest Drop" not in stats
    assert "vsYesterday(" in stats
    assert "listed_prev" in stats and "drops_prev" in stats


def test_from_price_note():
    assert "cheapest shop&rsquo;s &ldquo;from&rdquo; price" in HTML or "cheapest shop's &ldquo;from&rdquo; price" in HTML


def test_biggest_drops_view_gone():
    assert "vw_biggest_drops" not in create_ask_role.BOT_VIEWS
    sql = (ROOT / "sql/analytics.sql").read_text(encoding="utf-8")
    assert "CREATE OR REPLACE VIEW vw_biggest_drops" not in sql
    assert "DROP VIEW IF EXISTS vw_biggest_drops" in sql
