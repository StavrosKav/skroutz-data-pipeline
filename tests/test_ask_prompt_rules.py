"""General /ask prompt rules found by the 2026-10-30 eval reruns (not eval answers).

- Counting questions phrased "how many products have/are <status>" were
  counted over delisted products too (only "have/track/are there" was covered).
- Median/average questions about current prices were read from mv_brand_summary
  (all history) instead of current listings.
- When a name search returned a variant (e.g. "... FE ..."), the answer step
  replied "no data" although the row answered the question.
"""
from agents.ask.agent import ANSWER_NOT_IN_DATA, ANSWER_SYSTEM
from agents.ask.schema import SCHEMA_CONTEXT


def test_counts_rule_covers_status_and_trend_counts():
    assert "πόσα προϊόντα έχουν/είναι" in SCHEMA_CONTEXT
    assert "trend" in SCHEMA_CONTEXT.split("COUNTS:")[1].split("\n\n")[0]


def test_current_aggregates_never_come_from_brand_summary():
    assert "Use mv_brand_summary only when the question asks about all history" in SCHEMA_CONTEXT


def test_answer_names_the_variant_instead_of_no_data():
    # The summary prompt is English since 2026-11-16 (see test_ask_english_summary.py).
    assert "variant" in ANSWER_SYSTEM
    assert ANSWER_NOT_IN_DATA in ANSWER_SYSTEM
