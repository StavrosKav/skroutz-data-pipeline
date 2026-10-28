"""/ask agent: SQL guard, agent flow with a fake LLM/executor, eval checks.
No DB, no network."""

import json
from pathlib import Path

import pytest

from agents.ask.agent import NO_DATA, AskAgent, extract_sql, format_telegram
from agents.ask.guard import SQLGuardError, validate_sql
from agents.ask.retry import RetryableError, call_with_retry, is_retryable
from evals.checks import check

ROOT = Path(__file__).resolve().parents[1]


# ── guard ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("sql", [
    "DROP TABLE products",
    "DELETE FROM products",
    "UPDATE products SET brand='x'",
    "INSERT INTO products(id) VALUES (1)",
    "SELECT 1; DROP TABLE products",
    "SELECT * FROM products; SELECT 1",
    "SELECT * FROM pg_user",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM pipeline_runs",
    "SELECT * INTO tmp FROM products",
    "SELECT pg_sleep(10)",
    "WITH x AS (DELETE FROM products RETURNING *) SELECT * FROM x",
    "SELECT * FROM products p, secrets s",
    "SELECT * FROM products JOIN pg_shadow ON true",
    "SET statement_timeout = 0",
    "COPY products TO '/tmp/x'",
    "SELECT $$x$$",
    "",
    "EXPLAIN ANALYZE SELECT 1",
    "SELECT current_setting('x')",
])
def test_guard_rejects(sql):
    with pytest.raises(SQLGuardError):
        validate_sql(sql)


def test_guard_allows_select_and_adds_limit():
    out = validate_sql("SELECT product_name, price_eur FROM mv_latest_prices ORDER BY price_eur;")
    assert out.endswith("LIMIT 50") and ";" not in out


def test_guard_caps_big_limit_and_keeps_small():
    assert validate_sql("SELECT * FROM products LIMIT 100000").endswith("LIMIT 200")
    assert validate_sql("SELECT * FROM products LIMIT ALL").endswith("LIMIT 200")
    assert validate_sql("SELECT * FROM products LIMIT 5").endswith("LIMIT 5")


def test_guard_ignores_keywords_inside_strings_and_allows_ctes():
    sql = ("WITH t AS (SELECT * FROM products WHERE product_name ILIKE '%drop; delete%') "
           "SELECT COUNT(*) FROM t JOIN price_snapshots s ON s.product_id = t.id")
    assert "LIMIT" in validate_sql(sql)


def test_guard_rejects_semicolon_hidden_after_comment():
    with pytest.raises(SQLGuardError):
        validate_sql("SELECT 1 FROM products -- x\n; DROP TABLE products")


def test_eval_reference_sql_passes_guard():
    items = json.loads((ROOT / "evals" / "ask_eval.yaml").read_text(encoding="utf-8"))["items"]
    assert len(items) == 20 and len({i["id"] for i in items}) == 20
    for it in items:
        if it["reference_sql"]:
            validate_sql(it["reference_sql"])


# ── fakes ────────────────────────────────────────────────────────────────────
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


class FakeDB:
    def __init__(self, results):
        self.results = list(results)
        self.sql = []

    def __call__(self, sql):
        self.sql.append(sql)
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _agent(llm, db, **kw):
    return AskAgent(llm, db, sleep=lambda s: None, **kw)


SQL1 = "```sql\nSELECT product_name, price_eur FROM mv_latest_prices ORDER BY price_eur LIMIT 1\n```"


def test_happy_path_grounded_answer_and_sql_shown():
    llm = FakeLLM([SQL1, "Το φθηνότερο είναι το X στα 99€."])
    db = FakeDB([(["product_name", "price_eur"], [("X", 99)])])
    res = _agent(llm, db).ask("φθηνότερο κινητό;")
    assert res.answer.startswith("Το φθηνότερο")
    assert db.sql[0].startswith("SELECT product_name")
    answer_prompt = llm.calls[1][0][-1]["content"]
    assert '"X"' in answer_prompt and "99" in answer_prompt
    assert all(mt <= 400 for _, mt in llm.calls)  # token cap
    html = format_telegram(res)
    assert "<pre>SELECT product_name" in html


def test_empty_result_says_no_data_without_second_llm_call():
    llm = FakeLLM([SQL1])
    db = FakeDB([(["product_name", "price_eur"], [])])
    res = _agent(llm, db).ask("κάτι")
    assert res.answer == NO_DATA and len(llm.calls) == 1


def test_self_correction_on_db_error_once():
    llm = FakeLLM(["SELECT nope FROM products", SQL1, "ok"])
    db = FakeDB([RuntimeError("column nope does not exist"), (["product_name", "price_eur"], [("X", 1)])])
    res = _agent(llm, db).ask("q")
    assert res.answer == "ok" and res.attempts == 2
    assert "column nope does not exist" in llm.calls[1][0][-1]["content"]


def test_self_correction_on_guard_error_then_gives_up():
    llm = FakeLLM(["DELETE FROM products", "DROP TABLE products"])
    db = FakeDB([])
    res = _agent(llm, db).ask("σβήσε τα πάντα")
    assert res.error and db.sql == [] and "ασφαλές" in res.answer


def test_cache_hits_skip_llm():
    llm = FakeLLM([SQL1, "ans"])
    db = FakeDB([(["product_name", "price_eur"], [("X", 1)])])
    a = _agent(llm, db)
    a.ask("Φθηνότερο  κινητό")
    r2 = a.ask("φθηνότερο κινητό")
    assert r2.cached and len(llm.calls) == 2


def test_llm_retry_only_on_retryable():
    llm = FakeLLM([RetryableError("429"), SQL1, "ans"])
    db = FakeDB([(["product_name", "price_eur"], [("X", 1)])])
    assert _agent(llm, db).ask("q").answer == "ans"

    llm = FakeLLM([ValueError("bad request")])
    res = _agent(llm, FakeDB([])).ask("q")
    assert res.error == "ValueError" and len(llm.calls) == 1


def test_is_retryable_status_codes():
    class R:
        def __init__(self, c):
            self.status_code = c

    class E(Exception):
        def __init__(self, c):
            self.response = R(c)

    assert is_retryable(E(429)) and is_retryable(E(503)) and is_retryable(TimeoutError())
    assert not is_retryable(E(400)) and not is_retryable(E(401)) and not is_retryable(KeyError())
    n = []
    with pytest.raises(KeyError):
        call_with_retry(lambda: n.append(1) or (_ for _ in ()).throw(KeyError()), sleep=lambda s: None)
    assert len(n) == 1


def test_extract_sql_plain_and_fenced():
    assert extract_sql("SELECT 1") == "SELECT 1"
    assert extract_sql("here:\n```sql\nSELECT 2\n```") == "SELECT 2"


# ── eval checks ──────────────────────────────────────────────────────────────
def test_eval_checks():
    assert check({"type": "value", "key": "n"}, ["n"], [[42]], [[42]], "")[0]
    assert not check({"type": "value", "key": "n"}, ["n"], [[42]], [[41]], "")[0]
    assert check({"type": "value", "key": "a", "tol": 0.02}, ["a"], [[100]], [["101.5"]], "")[0]
    assert check({"type": "top1", "key": "price_eur"}, ["id", "price_eur"], [[1, 99]],
                 [["X", 99]], "")[0]
    assert check({"type": "set", "key": "n"}, ["c", "n"], [["a", 1], ["b", 2]],
                 [["a", 1], ["b", 2]], "")[0]
    assert check({"type": "no_data"}, [], [], [], NO_DATA)[0]
    assert not check({"type": "value", "key": "n"}, ["n"], [[1]], [], NO_DATA)[0]
