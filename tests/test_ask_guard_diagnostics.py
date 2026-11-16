"""/ask guard follow-up (live test 2026-11-16 16:49): a 199-char answer over 7 daily rows
(date/avg_price/min_price/max_price/products_tracked) was rejected as degenerate.
Data answers repeat numbers and use table punctuation; only word loops are degenerate.
No DB, no network."""

import logging

import pytest

from agents.ask.agent import AskAgent
from agents.ask.output_guard import SUMMARY_FAILED, degenerate_reason, is_degenerate, strip_reasoning

COLS = ["date", "avg_price", "min_price", "max_price", "products_tracked"]
ROWS = [[f"2026-11-{d}", 512.4 - (d - 10) * 2, 10.00, 2199.0, 1046 + d] for d in range(10, 17)]
SQL = "```sql\nSELECT date, avg_price, min_price, max_price, products_tracked FROM mv_daily_market_index LIMIT 50\n```"

PLAUSIBLE = {
    "min-list-dot": ("Μέση τιμή κινητών 10-16/11: 512.40€ → 500.40€. Ελάχιστη τιμή κάθε μέρα: "
                     "10.00, 10.00, 10.00, 10.00, 10.00, 10.00, 10.00. Προϊόντα ~1050."),
    "min-list-int": "Min price per day: 10, 10, 10, 10, 10, 10, 10 €. Average fell from 512 € to 500 €.",
    "min-repeat-eur": "Η ελάχιστη τιμή ήταν 10.00 € (10.00 € 10.00 € 10.00 € 10.00 € 10.00 € 10.00 €).",
    "max-list": "Μέγιστη τιμή: 2199.0 / 2199.0 / 2199.0 / 2199.0 / 2199.0 / 2199.0 / 2199.0 €.",
    "md-table": "| Ημ/νία | Μέση |\n|------------|--------|\n| 10/11 | 512.40 |\n| 16/11 | 500.40 |",
    "ellipsis": "Η μέση τιμή έπεσε...... από 512€ σε 500€.",
    "underline": "Σύνοψη\n------\nΗ μέση τιμή έπεσε 2,3% (512.40€ → 500.40€).",
    "thirty-rows-same": "Ελάχιστη: " + ", ".join(["10"] * 30) + " €.",
}


@pytest.mark.parametrize("text", PLAUSIBLE.values(), ids=PLAUSIBLE.keys())
def test_plausible_data_answers_are_not_flagged(text):
    assert len(text) < 1500
    assert degenerate_reason(text) is None, degenerate_reason(text)


@pytest.mark.parametrize("text", [
    "Η τιμή " + "dao " * 6,
    "Αυτή την εβδομάδα η μέση τιμή κινητών " + "dao " * 2900,
    "Price " + "went up and " * 7,
    "Τιμή: " + "€" * 400,
], ids=["dao-x6", "incident", "phrase-loop", "euro-run"])
def test_real_loops_still_flagged(text):
    assert is_degenerate(text)


def test_reason_names_the_rule():
    assert degenerate_reason("x" * 1600).startswith("too_long")
    r = degenerate_reason("Η τιμή " + "dao " * 10)
    assert r.startswith("loop") and "dao" in r
    assert degenerate_reason("") == "empty"


@pytest.mark.parametrize("raw,clean", [
    ("<think>σκέψη για τα δεδομένα</think>\nΗ μέση τιμή έπεσε.", "Η μέση τιμή έπεσε."),
    ("<THINK>a\nb</THINK>Τιμή 500€", "Τιμή 500€"),
    ("<think>reasoning cut off by max_tokens", ""),
    ("Η μέση τιμή έπεσε.", "Η μέση τιμή έπεσε."),
], ids=["closed", "upper", "unclosed", "none"])
def test_strip_reasoning(raw, clean):
    assert strip_reasoning(raw) == clean


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)

    def __call__(self, messages, max_tokens):
        return self.replies.pop(0)


def _ask(answer):
    agent = AskAgent(FakeLLM([SQL, answer]), lambda sql: (COLS, ROWS), sleep=lambda s: None)
    return agent.ask("what is going on with the price of phones this week?")


def test_plausible_answer_over_live_rows_is_kept():
    text = PLAUSIBLE["min-list-dot"]
    res = _ask(text)
    assert res.answer == text and not res.summary_failed


def test_think_block_is_removed_from_answer():
    res = _ask("<think>ας δούμε</think>Η μέση τιμή έπεσε από 512.40€ σε 500.40€.")
    assert res.answer == "Η μέση τιμή έπεσε από 512.40€ σε 500.40€." and not res.summary_failed


def test_rejection_logs_rule_and_text_preview(caplog):
    bad = "Αυτή την εβδομάδα η μέση τιμή κινητών " + "dao " * 300
    with caplog.at_level(logging.WARNING, logger="agents.ask.agent"):
        res = _ask(bad)
    assert res.answer.startswith(SUMMARY_FAILED)
    msg = "\n".join(r.getMessage() for r in caplog.records)
    assert "too_long" in msg or "loop" in msg
    assert "Αυτή την εβδομάδα η μέση τιμή κινητών" in msg
    assert bad[:300] not in msg  # preview is capped at 200 chars


def test_empty_rejection_logs_empty(caplog):
    with caplog.at_level(logging.WARNING, logger="agents.ask.agent"):
        res = _ask("<think>only reasoning")
    assert res.summary_failed
    assert "empty" in "\n".join(r.getMessage() for r in caplog.records)


# ── laptop evidence 16:53: Nemotron 3 Ultra corrupts Greek right after "Η μέση τιμή κινητών" ──
LIVE_TRY0 = ("Η μέση τιμή κινητών daoflouktuάει εβδομάδα (10-16/11/2026): ξεκίνησε στα €352,29 "
             "και έκλεισε στα €343,31 (16/1\ufffd).")


def test_replacement_char_is_degenerate():
    assert degenerate_reason("Η μέση τιμή έκλεισε στα €343,31 (16/1\ufffd).") == "replacement_char"


def test_latin_glued_to_greek_word_is_degenerate():
    r = degenerate_reason("Η μέση τιμή κινητών daoflouktuάει όλη την εβδομάδα.")
    assert r and r.startswith("mixed_script") and "daoflouktuάει" in r


def test_live_try0_is_rejected():
    assert is_degenerate(LIVE_TRY0)


@pytest.mark.parametrize("text", [
    "Το iPhone 15 Pro 128GB κοστίζει 999€ στο Skroutz, με 5G και USB-C.",
    "Η Samsung (Galaxy S24) έπεσε 3%· το Xiaomi Redmi Note 13 μένει στα 199€.",
], ids=["latin-models", "brands"])
def test_latin_brand_names_next_to_greek_are_fine(text):
    assert degenerate_reason(text) is None


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


GOOD = "Η μέση τιμή κινητών έπεσε από 512.40€ σε 500.40€ (−2,3%)."


def _agent2(first, retry):
    return AskAgent(SeqLLM([SQL]), lambda sql: (COLS, ROWS), sleep=lambda s: None,
                    answer_llm=first, answer_retry_llm=retry)


def test_degenerate_answer_is_retried_on_the_retry_model():
    first, retry = SeqLLM(["Η μέση τιμή κινητών " + "dao " * 40]), SeqLLM([GOOD])
    res = _agent2(first, retry).ask("phones this week?")
    assert res.answer == GOOD and not res.summary_failed
    assert first.calls == 1 and retry.calls == 1


def test_retry_not_called_when_first_answer_is_good():
    first, retry = SeqLLM([GOOD]), SeqLLM([])
    res = _agent2(first, retry).ask("phones this week?")
    assert res.answer == GOOD and retry.calls == 0


def test_retry_model_error_or_garbage_falls_back_to_rows():
    res = _agent2(SeqLLM([LIVE_TRY0]), SeqLLM([RuntimeError("bolt down")])).ask("phones?")
    assert res.summary_failed and res.answer.startswith(SUMMARY_FAILED)
    res = _agent2(SeqLLM([LIVE_TRY0]), SeqLLM(["dao " * 50])).ask("phones again?")
    assert res.summary_failed and res.answer.startswith(SUMMARY_FAILED)


def test_bot_agent_retries_answer_on_next_model_in_fallback_chain(monkeypatch):
    import agents.ask.runtime as rt
    from alerts.nim_client import FALLBACK_CHAIN

    made = []
    monkeypatch.setattr(rt, "make_nim_llm", lambda **kw: made.append(kw) or (lambda m, t: ""))
    monkeypatch.setattr(rt, "make_pg_executor", lambda: (lambda sql: ([], [])))
    monkeypatch.setattr(rt, "_AGENT", None)
    agent = rt.get_agent()
    assert agent.answer_retry_llm is not None
    pinned = [kw["model"] for kw in made if kw.get("model")]
    assert pinned == [FALLBACK_CHAIN[1].value]  # nvidia/nemotron-3.5-bolt-e4b, already in the repo
