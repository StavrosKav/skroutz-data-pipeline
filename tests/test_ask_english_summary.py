"""/ask summary in English (decision 2026-11-16). Live laptop test, same rows and model
(Nemotron 3 Ultra, ANSWER_SAMPLING): English summaries 6/6 clean, Greek 0/6 (broke after
'κινητών' with 'dao' loops, mixed scripts, Cyrillic). Only the summary changes language;
everything else the bot says stays Greek. No DB, no network."""

import pytest

from agents.ask.agent import ANSWER_NOT_IN_DATA, ANSWER_SYSTEM, NO_DATA, OFF_TOPIC, AskAgent
from agents.ask.output_guard import SUMMARY_FAILED, degenerate_reason
from evals.checks import check

QUESTION = "what is going on with the price of phones this week?"
COLS = ["date", "avg_price", "min_price", "max_price", "products_tracked", "price_drops"]
ROWS = [["2026-11-10", 352.29, 10.0, 3305.0, 1050, 57], ["2026-11-14", 340.59, 10.0, 3180.0, 1049, 272],
        ["2026-11-16", 343.31, 10.0, 3180.0, 1052, 120]]
CTX = {"question": QUESTION, "cols": COLS, "rows": ROWS}
SQL = "```sql\nSELECT date, avg_price FROM mv_daily_market_index LIMIT 50\n```"

ENGLISH_LIVE = {
    "plain-nbhyphen": (
        "Average phone prices declined this week (Nov 10\u201116), falling from €352.29 to €343.31 "
        "(\u2011€9). The lowest daily average was €340.59 on Nov 14, which also saw the most price "
        "drops (272)."),
    "markdown-bold": (
        "Average phone prices declined this week (Nov 10\u201116), falling from **€352.29** on Nov 10 "
        "to **€343.31** on Nov 16. The lowest daily average was **€340.59** on Nov 14."),
}


# ── prompt ───────────────────────────────────────────────────────────────────
def test_answer_system_asks_for_english():
    assert "Answer in English" in ANSWER_SYSTEM
    assert "ελληνικά" not in ANSWER_SYSTEM


def test_answer_system_keeps_the_rules_of_the_greek_prompt():
    s = ANSWER_SYSTEM
    assert "up to 5 lines" in s and "ONLY from the result rows" in s
    assert "do not guess" in s and "EUR" in s
    assert "database search for this question" in s
    assert "variant" in s  # name the exact product/variant (FE, storage) as in the rows
    assert f"'{ANSWER_NOT_IN_DATA}'" in s
    assert ANSWER_NOT_IN_DATA == "Not in the data."


def test_answer_from_rows_uses_english_framing():
    seen = []
    a = AskAgent(lambda m, t: "", lambda sql: ([], []),
                 answer_llm=lambda m, t: seen.append(m) or "ok")
    a.answer_from_rows(QUESTION, ["n"], [[i] for i in range(40)])
    user = seen[0][-1]["content"]
    assert user.startswith(f"Question: {QUESTION}\nRows (shown 30 of 40):\n")
    assert "Ερώτηση" not in user and "Γραμμές" not in user
    a.answer_from_rows(QUESTION, ["n"], [[1]])
    assert seen[1][-1]["content"].startswith(f"Question: {QUESTION}\nRows:\n")


def test_bot_texts_outside_the_summary_stay_greek():
    assert NO_DATA == "Δεν υπάρχει στα δεδομένα."
    assert OFF_TOPIC.startswith("Μπορώ να απαντήσω")
    assert SUMMARY_FAILED.startswith("⚠️ Η σύνοψη απέτυχε")
    res = AskAgent(lambda m, t: SQL, lambda sql: (COLS, [])).ask(QUESTION)
    assert res.answer == NO_DATA


# ── guard on real English summaries ──────────────────────────────────────────
@pytest.mark.parametrize("text", ENGLISH_LIVE.values(), ids=ENGLISH_LIVE.keys())
def test_guard_accepts_real_english_summaries(text):
    assert degenerate_reason(text, **CTX) is None
    assert degenerate_reason(text) is None


@pytest.mark.parametrize("text, rule", [
    ("Average phone prices fell; ~۱۰۵۰ products tracked.", "foreign_digits"),
    ("Average phone prices " + "dao " * 8, "loop"),
    ("Average phone prices fell \ufffd to €343.31.", "replacement_char"),
    ("Average phone prices daoέπεσαν to €343.31.", "mixed_script"),
], ids=["foreign-digits", "loop", "replacement-char", "mixed-script"])
def test_guard_rules_still_apply_to_english(text, rule):
    assert (degenerate_reason(text, **CTX) or "").startswith(rule)


def test_english_summary_flows_through_agent_unchanged():
    text = ENGLISH_LIVE["markdown-bold"]
    a = AskAgent(lambda m, t: SQL, lambda sql: (COLS, ROWS), answer_llm=lambda m, t: text)
    res = a.ask(QUESTION)
    assert res.answer == text and not res.summary_failed


# ── 'not in data' handling ───────────────────────────────────────────────────
def test_not_in_data_reply_passes_guard():
    assert degenerate_reason(ANSWER_NOT_IN_DATA, **CTX) is None


def test_eval_accepts_english_not_in_data_from_summary():
    assert check({"type": "no_data"}, [], [], [["x"]], ANSWER_NOT_IN_DATA)[0]
    assert check({"type": "value", "key": "n"}, ["n"], [], [["x"]], ANSWER_NOT_IN_DATA)[0]
    assert check({"type": "no_data"}, [], [], [["x"]], NO_DATA)[0]  # Greek bot text still ok
    assert not check({"type": "no_data"}, [], [], [["x"]], "Prices fell.")[0]
