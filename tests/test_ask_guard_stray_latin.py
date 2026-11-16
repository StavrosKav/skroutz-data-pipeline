"""/ask guard, third pass (live laptop test 2026-11-16, 'what is going on with the price of
phones this week?', 7 daily rows). Nemotron 3 Ultra broke the Greek summary right after
'κινητών' in about half the tries; these outputs passed the guard and would have reached
Telegram. No DB, no network."""

import logging

import pytest

from agents.ask.agent import AskAgent
from agents.ask.output_guard import SUMMARY_FAILED, degenerate_reason

QUESTION = "what is going on with the price of phones this week?"
COLS = ["date", "avg_price", "min_price", "max_price", "products_tracked"]
ROWS = [[f"2026-11-{d}", 352.29 - (d - 10) * 1.5, 10.0, 3180.0, 1046 + d] for d in range(10, 17)]
CTX = {"question": QUESTION, "cols": COLS, "rows": ROWS}
SQL = "```sql\nSELECT date, avg_price, min_price, max_price, products_tracked FROM mv_daily_market_index LIMIT 50\n```"

LIVE_BAD = {
    "daofluctuated-arabic-digits": (
        "Η μέση τιμή κινητών daofluctuated από €352,29 (10/11) σε €340,59 (14/17) και ανήλθε "
        "ελαφρώς σε €343,31 (16/17). ... Παρακολουθούνται ~۱۰۵۰ προϊόντα καθημερινά."),
    "stray-dao": "Η μέση τιμή κινητών dao κυμαίνεται στα €340-355 ...",
    "daofluctuated-week": "Η μέση τιμή κινητών daofluctuated εβδομάδα 10-16/11/2026 από ...",
}
GOOD_ULTRA = ("Η μέση τιμή κινητών μειώθηκε από 352,29€ (10/11) σε 343,31€ (16/11). "
              "Καθημερινά καταγράφηκαν πτώσεις τιμών σε 57-272 προϊόντα...")


@pytest.mark.parametrize("text", LIVE_BAD.values(), ids=LIVE_BAD.keys())
def test_live_corrupted_answers_are_rejected(text):
    assert degenerate_reason(text, **CTX) is not None


def test_arabic_indic_digits_are_rejected_even_without_context():
    assert degenerate_reason("Παρακολουθούνται ~۱۰۵۰ προϊόντα.").startswith("foreign_digits")
    assert degenerate_reason("Tracked ~۱۰۵۰ products.").startswith("foreign_digits")
    assert degenerate_reason("Προϊόντα: ۱۲۳ (Extended Arabic-Indic)").startswith("foreign_digits")
    assert degenerate_reason("Προϊόντα: १०५० (Devanagari)").startswith("foreign_digits")


def test_stray_latin_word_reason_names_the_word():
    assert degenerate_reason(LIVE_BAD["stray-dao"], **CTX) == "stray_latin(dao)"
    assert degenerate_reason(LIVE_BAD["daofluctuated-week"], **CTX) == "stray_latin(daofluctuated)"


def test_good_ultra_answer_passes():
    assert degenerate_reason(GOOD_ULTRA, **CTX) is None
    assert degenerate_reason(GOOD_ULTRA) is None


@pytest.mark.parametrize("text, rows", [
    ("Το iPhone 15 Pro έπεσε στα 999€· το Samsung Galaxy S24 FE στα 549€ (256 GB).",
     [["Apple iPhone 15 Pro 128GB", 999.0], ["Samsung Galaxy S24 FE 256GB", 549.0]]),
    ("Φθηνότερο: Xiaomi Redmi Note 13 στα 199€ στο Skroutz, 8GB/256GB, 5G.",
     [["Xiaomi Redmi Note 13 8GB/256GB", 199.0]]),
], ids=["names-in-rows", "unit-and-site"])
def test_latin_names_grounded_in_rows_are_fine(text, rows):
    assert degenerate_reason(text, question="φθηνότερα κινητά", cols=["name", "price"], rows=rows) is None


def test_column_names_and_question_words_are_grounded():
    text = "Η avg_price έπεσε· τα products_tracked (phones) ήταν ~1050 αυτή την week."
    assert degenerate_reason(text, **CTX) is None


def test_latin_answer_to_english_question_is_not_checked_for_stray_words():
    text = "Average phone price fell from €352.29 (10/11) to €343.31 (16/11), about -2.5%."
    assert degenerate_reason(text, **CTX) is None


# ── agent: primary, primary again, then retry model ─────────────────────────
class SeqLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def __call__(self, messages, max_tokens):
        self.calls += 1
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _agent(primary, retry, **kw):
    return AskAgent(SeqLLM([SQL]), lambda sql: (COLS, ROWS), sleep=lambda s: None,
                    answer_llm=primary, answer_retry_llm=retry, **kw)


def test_primary_is_tried_twice_before_retry_model(caplog):
    primary, retry = SeqLLM([LIVE_BAD["stray-dao"], GOOD_ULTRA]), SeqLLM([])
    with caplog.at_level(logging.WARNING, logger="agents.ask.agent"):
        res = _agent(primary, retry, answer_primary_tries=2).ask(QUESTION)
    assert res.answer == GOOD_ULTRA and not res.summary_failed
    assert primary.calls == 2 and retry.calls == 0
    assert "stray_latin(dao)" in caplog.text and "preview=" in caplog.text


def test_retry_model_after_two_bad_primary_answers():
    bolt = "10/11: 352,29€\n16/11: 343,31€"
    primary = SeqLLM([LIVE_BAD["daofluctuated-week"], LIVE_BAD["daofluctuated-arabic-digits"]])
    retry = SeqLLM([bolt])
    res = _agent(primary, retry, answer_primary_tries=2).ask(QUESTION)
    assert res.answer == bolt and primary.calls == 2 and retry.calls == 1


def test_all_bad_falls_back_to_rows():
    primary = SeqLLM([LIVE_BAD["stray-dao"], LIVE_BAD["stray-dao"]])
    res = _agent(primary, SeqLLM([LIVE_BAD["stray-dao"]]), answer_primary_tries=2).ask(QUESTION)
    assert res.summary_failed and res.answer.startswith(SUMMARY_FAILED)


def test_bot_agent_tries_primary_twice(monkeypatch):
    import agents.ask.runtime as rt

    monkeypatch.setattr(rt, "make_nim_llm", lambda **kw: (lambda m, t: ""))
    monkeypatch.setattr(rt, "make_pg_executor", lambda: (lambda sql: ([], [])))
    monkeypatch.setattr(rt, "_AGENT", None)
    assert rt.get_agent().answer_primary_tries == 2
