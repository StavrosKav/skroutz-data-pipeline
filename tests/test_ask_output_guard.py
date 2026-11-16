"""/ask output guard (incident 2026-11-16): the answer LLM degenerated into
"dao dao dao ..." (11,920 chars), Telegram rejected it with 400 and the user got nothing.
No DB, no network."""

import json
import types
import urllib.error

import pytest

import agents.ask.runtime as rt
from agents.ask.agent import AskAgent, format_telegram
from agents.ask.output_guard import SUMMARY_FAILED, fallback_answer, is_degenerate

COLS = ["day", "avg_price_eur"]
ROWS = [["2026-11-10", 512.4], ["2026-11-11", 509.9], ["2026-11-16", 498.1]]
SQL = "```sql\nSELECT day, avg_price_eur FROM mv_daily_market_index LIMIT 50\n```"
DEGENERATE = ("Αυτή την εβδομάδα (10-16/11/2026) η μέση τιμή κινητών " + "dao " * 2900).strip()


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, messages, max_tokens):
        self.calls.append((messages, max_tokens))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _agent(llm, **kw):
    return AskAgent(llm, lambda sql: (COLS, ROWS), sleep=lambda s: None, **kw)


# ── detector ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    DEGENERATE,
    "Η τιμή " + "ανέβηκε και έπεσε " * 12,            # repeated 3-gram
    "Τιμή: " + "€€€€€€€€€€" * 40,                      # runaway single token
    "x" * 3000,                                        # far beyond a 5-line answer
], ids=["incident-dao", "ngram-loop", "token-run", "too-long"])
def test_detects_degenerate_text(text):
    assert is_degenerate(text)


@pytest.mark.parametrize("text", [
    "Η μέση τιμή κινητών έπεσε από 512,40€ σε 498,10€ (−2,8%) από 10/11 έως 16/11.",
    "Φθηνότερα: Xiaomi 14 299€, Xiaomi 14 Pro 349€, Xiaomi 14 Ultra 499€, Xiaomi 13 279€.",
    "Τιμές 10/11: 512€\nΤιμές 11/11: 510€\nΤιμές 12/11: 505€\nΤιμές 13/11: 503€\nΤιμές 16/11: 498€",
], ids=["one-line", "repeated-brand", "per-day-lines"])
def test_normal_answers_are_not_flagged(text):
    assert not is_degenerate(text)


def test_fallback_answer_is_short_greek_and_built_from_rows():
    out = fallback_answer(COLS, ROWS)
    assert out.startswith(SUMMARY_FAILED)
    assert "2026-11-16" in out and "498.1" in out and "avg_price_eur" in out
    many = fallback_answer(["n"], [[i] for i in range(200)])
    assert len(many) < 1500 and "200" in many  # capped, says how many rows exist


# ── agent ────────────────────────────────────────────────────────────────────
def test_degenerate_answer_falls_back_to_rows_and_keeps_sql():
    res = _agent(FakeLLM([SQL, DEGENERATE])).ask("τι γίνεται με την τιμή των κινητών αυτή την εβδομάδα;")
    assert "dao dao" not in res.answer
    assert res.answer.startswith(SUMMARY_FAILED) and "498.1" in res.answer
    html = format_telegram(res)
    assert "<pre>SELECT day" in html and len(html) < 4096


def test_answer_llm_exception_still_returns_rows():
    res = _agent(FakeLLM([SQL, RuntimeError("nim down")])).ask("κινητά εβδομάδα")
    assert res.answer.startswith(SUMMARY_FAILED) and res.sql


def test_empty_answer_falls_back():
    res = _agent(FakeLLM([SQL, "   "])).ask("κινητά εβδομάδα")
    assert res.answer.startswith(SUMMARY_FAILED) and "498.1" in res.answer


def test_failed_summary_is_not_cached():
    llm = FakeLLM([SQL, DEGENERATE, SQL, "Η τιμή έπεσε 2,8%."])
    agent = _agent(llm)
    agent.ask("κινητά εβδομάδα")
    res = agent.ask("κινητά εβδομάδα")
    assert not res.cached and res.answer == "Η τιμή έπεσε 2,8%."


def test_separate_answer_llm_is_used_for_the_answer_step():
    sql_llm, answer_llm = FakeLLM([SQL]), FakeLLM(["Η τιμή έπεσε."])
    res = _agent(sql_llm, answer_llm=answer_llm).ask("κινητά")
    assert res.answer == "Η τιμή έπεσε." and len(sql_llm.calls) == 1 and len(answer_llm.calls) == 1


# ── prevention at source ─────────────────────────────────────────────────────
def test_make_nim_llm_forwards_sampling_params(monkeypatch):
    seen = {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def chat(self, messages, task, temperature, max_tokens, **kw):
            seen.update(temperature=temperature, **kw)
            return types.SimpleNamespace(model="m", latency_ms=1, content="ok")

    import alerts.nim_client as nc
    monkeypatch.setattr(nc, "NIMClient", FakeClient)
    llm = rt.make_nim_llm(temperature=0.3, frequency_penalty=0.4, presence_penalty=0.1)
    assert llm([{"role": "user", "content": "q"}], 10) == "ok"
    assert seen == {"temperature": 0.3, "frequency_penalty": 0.4, "presence_penalty": 0.1}


def test_bot_agent_answer_step_uses_repetition_penalty(monkeypatch):
    made = []
    monkeypatch.setattr(rt, "make_nim_llm", lambda **kw: made.append(kw) or (lambda m, t: ""))
    monkeypatch.setattr(rt, "make_pg_executor", lambda: (lambda sql: ([], [])))
    monkeypatch.setattr(rt, "_AGENT", None)
    agent = rt.get_agent()
    assert agent.answer_llm is not agent.llm
    answer_kw = [kw for kw in made if kw.get("frequency_penalty")]
    assert answer_kw and answer_kw[0]["frequency_penalty"] > 0
    assert answer_kw[0].get("temperature", 0) > 0  # greedy decoding loops more easily


# ── Telegram send ────────────────────────────────────────────────────────────
import alerts.telegram_bot as tb  # noqa: E402


def test_fit_html_caps_length_and_closes_tags():
    text = "<b>SQL</b>\n<pre>" + ("SELECT &amp; x " * 600) + "</pre>"
    out = tb._fit_html(text)
    assert tb._tg_len(out) <= 4096
    assert out.count("<pre>") == out.count("</pre>") and out.count("<b>") == out.count("</b>")
    assert "&am" not in out.replace("&amp;", "")  # no broken entity at the cut


def test_fit_html_leaves_short_text_alone():
    assert tb._fit_html("<b>ok</b>") == "<b>ok</b>"


def test_send_never_posts_more_than_4096(monkeypatch):
    sent = []
    monkeypatch.setattr(tb, "_post", lambda m, p: sent.append(p) or {"ok": True})
    tb._send("α" * 12000, chat_id="1")
    assert len(sent) == 1 and tb._tg_len(sent[0]["text"]) <= 4096


def test_send_retries_plain_text_on_400(monkeypatch):
    sent = []

    def fake_post(method, payload):
        sent.append(dict(payload))
        if payload.get("parse_mode") == "HTML":
            return {"ok": False, "error_code": 400, "description": "Bad Request: can't parse entities"}
        return {"ok": True}

    monkeypatch.setattr(tb, "_post", fake_post)
    tb._send("<b>τιμή</b> &lt;5€ <broken", chat_id="1")
    assert len(sent) == 2
    assert "parse_mode" not in sent[1]
    assert sent[1]["text"].startswith("τιμή <5€") and tb._tg_len(sent[1]["text"]) <= 4096


def test_plain_cap_counts_utf16_units():
    out = tb._plain("🤖" * 5000)  # each emoji is 2 UTF-16 units in Telegram's count
    assert 4000 <= tb._tg_len(out) <= 4096


def test_post_reports_http_400_instead_of_empty(monkeypatch):
    body = json.dumps({"ok": False, "error_code": 400, "description": "Bad Request: message is too long"})

    def boom(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, __import__("io").BytesIO(body.encode()))

    monkeypatch.setattr(tb.urllib.request, "urlopen", boom)
    r = tb._post("sendMessage", {"chat_id": "1", "text": "x"})
    assert r["error_code"] == 400 and "too long" in r["description"]


# ── /analyze uses the same guard ─────────────────────────────────────────────
def test_analyze_drops_degenerate_summary():
    from agents.ask import analyze as an

    def execute(sql):
        return ([], [])

    out = an.analyze(execute, lambda m, t: DEGENERATE, "phone")
    assert "dao dao" not in out and "Δεδομένα" in out


def test_nim_response_exposes_finish_reason(monkeypatch):
    """finish_reason='length' means the model hit max_tokens (a runaway answer); keep it visible."""
    import asyncio

    import alerts.nim_client as nc

    async def fake_post(self, path, payload):
        return {"id": "x", "created": 0, "model": payload["model"],
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "dao dao"},
                             "finish_reason": "length"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}

    monkeypatch.setattr(nc.NIMClient, "_post", fake_post)
    r = asyncio.run(nc.NIMClient(api_key="k").chat([{"role": "user", "content": "q"}]))
    assert r.finish_reason == "length"
