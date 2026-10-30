"""Eval references must follow the conventions the agent is told (agents/ask/schema.py).

A reference that silently answers a different question than the documented
convention measures nothing: the 2026-10-30 run failed three items because the
reference, not the agent, broke these rules.
"""
import json
from pathlib import Path

from agents.ask.schema import SCHEMA_CONTEXT

ROOT = Path(__file__).resolve().parents[1]
ITEMS = {i["id"]: i for i in json.loads(
    (ROOT / "evals" / "ask_eval.yaml").read_text(encoding="utf-8"))["items"]}


def _is_current(sql: str) -> bool:
    return ("last_seen" in sql or "last_price_date" in sql) and "MAX(date)" in sql


def test_trend_count_counts_currently_listed_products():
    # "πόσα προϊόντα ..." = currently listed (COUNTS rule); the trend view
    # also holds products delisted within its 30-day window.
    assert _is_current(ITEMS["falling_trend_count"]["reference_sql"])


def test_median_is_over_current_prices_not_all_history():
    # mv_brand_summary aggregates every snapshot ever, weighted by days listed.
    ref = ITEMS["apple_median_tablet"]["reference_sql"]
    assert "mv_brand_summary" not in ref and _is_current(ref)


def test_atl_question_names_what_the_reference_computes():
    # The reference is the minimum over every S24 variant ever listed (FE,
    # Enterprise...), so the question must say so instead of "the cheapest
    # S24", which by convention means one currently listed product.
    q = ITEMS["atl_galaxy_s24"]["question"]
    assert "φθηνότερο" not in q and "ποτέ" in q


def test_schema_says_brand_summary_and_trend_include_history():
    assert "ALL snapshots ever" in SCHEMA_CONTEXT
    assert "includes delisted" in SCHEMA_CONTEXT
