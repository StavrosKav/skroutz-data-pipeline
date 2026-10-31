"""Ask bot, eval set and Hot Deals use the same 7-snapshot drop definition."""
import inspect
import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from agents.ask import schema
from ops import create_ask_role
from reporting import queries

ROOT = Path(__file__).resolve().parents[1]


def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8-sig")


def test_ask_bot_allows_and_describes_only_the_7d_view():
    assert "vw_drops_7d" in schema.ALLOWED_TABLES
    assert "vw_biggest_drops" not in schema.ALLOWED_TABLES
    assert "vw_drops_7d(" in schema.SCHEMA_CONTEXT
    assert "vw_biggest_drops" not in schema.SCHEMA_CONTEXT
    assert "vw_biggest_drops" not in _src("agents/ask/analyze.py")


def test_eval_references_use_7d_view():
    items = json.loads(_src("evals/ask_eval.yaml"))["items"]
    sqls = [i.get("reference_sql") or "" for i in items]
    assert not any("vw_biggest_drops" in s for s in sqls)
    assert sum("vw_drops_7d" in s for s in sqls) == 3


def test_role_can_read_7d_view():
    assert "vw_drops_7d" in create_ask_role.BOT_VIEWS


def test_hot_deals_uses_7d_drops_and_keeps_big_drops():
    assert "max_drop_pct" not in inspect.signature(queries.hot_deals).parameters
    with patch.object(queries.pd, "read_sql", return_value=pd.DataFrame()) as rs:
        queries.hot_deals("conn", limit=5, min_drop_pct=3.0, min_drop_eur=5.0)
    sql = str(rs.call_args[0][0])
    assert "mv_drops_7d" in sql
    assert "vw_hot_deals" not in sql
    assert "max_pct" not in sql
    assert "pr.reviews, 0) > 0" in sql  # review surge still required
    assert "max_drop_pct" not in _src("reporting/generate_dashboard.py")
    assert "excluded" not in _src("templates/dashboard.html")
