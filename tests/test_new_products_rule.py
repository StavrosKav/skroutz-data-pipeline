"""
Audit item B: one "new product" rule everywhere.
New = first_seen within the 7 most recent days (first_seen > latest - 7)
AND listed in the latest scrape. Dashboard KPI, New Arrivals list,
Telegram weekly line, ask-bot schema and eval reference all use it.
"""
import json
import re
from pathlib import Path

from reporting import queries

ROOT = Path(__file__).resolve().parents[1]
RULE = "first_seen > (SELECT MAX(date) FROM price_snapshots) - :days"


def _norm(s):
    return re.sub(r"\s+", " ", s)


def test_rule_constant():
    assert _norm(queries.NEW_PRODUCTS_WHERE) == RULE


def test_dashboard_list_uses_rule():
    src = _norm((ROOT / "reporting/generate_dashboard.py").read_text(encoding="utf-8"))
    assert "first_seen >= (SELECT MAX(date)" not in src
    assert "queries.NEW_PRODUCTS_WHERE" in src


def test_eval_reference_uses_rule():
    data = json.loads((ROOT / "evals/ask_eval.yaml").read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else next(v for v in data.values() if isinstance(v, list))
    sql = _norm(next(i for i in items if i["id"] == "new_products_week")["reference_sql"])
    assert "first_seen > (SELECT MAX(date) FROM price_snapshots) - 7" in sql
    assert "s.date = (SELECT MAX(date) FROM price_snapshots)" in sql


def test_ask_schema_states_rule():
    from agents.ask import schema
    text = " ".join(str(v) for k, v in vars(schema).items() if isinstance(v, str))
    assert '"New this week" = first_seen > latest date - 7' in text
    assert "first_seen >= latest date - 7" not in text


def test_telegram_shows_week_count():
    from alerts import notifications as n
    sent = {}
    n.tg_send, orig = (lambda m: sent.setdefault("m", m) or True), n.tg_send
    try:
        n.tg_success(10, 2, 3, "0:01:00", new_week=232)
    finally:
        n.tg_send = orig
    assert "232" in sent["m"] and "7 days" in sent["m"]
