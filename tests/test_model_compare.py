"""evals/compare.py + evals/prompts.py: offline, fake LLM and executor."""
import asyncio

import httpx
import pytest

from agents.ask import agent as ag
from agents.ask.schema import SCHEMA_CONTEXT
from alerts import nim_client as nc
from evals import compare as cmp
from evals import prompts as pr
from evals.checks import check


def test_v1_is_production_prompt():
    assert pr.sql_system("v1") == ag.SQL_SYSTEM


def test_v0_drops_only_the_hints():
    v0 = pr.sql_system("v0")
    for h in pr.V1_HINT_LINES:
        assert h in ag.SQL_SYSTEM and h not in v0
    assert "mv_latest_prices(" in v0 and "New this week" in v0


def test_v2_adds_fewshot_not_from_eval():
    from evals.run_ask_eval import load_items
    v2 = pr.sql_system("v2")
    assert v2.startswith(ag.SQL_SYSTEM) and v2.count("```sql") >= 3
    for it in load_items():
        assert it["question"] not in v2


def test_unknown_variant():
    with pytest.raises(ValueError):
        pr.sql_system("v9")


def test_classify():
    assert cmp.classify(True, None, "SELECT 1", []) is None
    assert cmp.classify(False, None, "SELECT 1", []) == "wrong_result"
    assert cmp.classify(False, "ReadTimeout", None, [{"error": "ReadTimeout"}]) == "timeout"
    assert cmp.classify(False, "HTTPStatusError", None, [{"error": "HTTPStatusError"}]) == "api_error"
    assert cmp.classify(False, "Only SELECT", None, [{"content": "I cannot"}]) == "no_sql"
    assert cmp.classify(False, "column x", None, [{"content": "SELECT x FROM t"}]) == "invalid_sql"


def test_percentile_and_summary():
    assert cmp._pct([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.9) == pytest.approx(9.1)
    rows = [{"ok": True, "failure": None, "sql_valid": True, "latency_ms": 1000,
             "prompt_tokens": 10, "completion_tokens": 5, "models_used": ["m"]},
            {"ok": False, "failure": "timeout", "sql_valid": False, "latency_ms": 3000,
             "prompt_tokens": 20, "completion_tokens": 0, "models_used": ["m"]}]
    s = cmp.summarize(rows)
    assert (s["passed"], s["sql_valid"], s["median_s"]) == (1, 1, 2.0)
    assert s["failures"]["timeout"] == 1 and s["prompt_tokens_avg"] == 15


def _fake_chat(sql):
    async def chat(messages, max_tokens):
        if "SQL" in messages[0]["content"] or "SELECT" in messages[0]["content"]:
            return f"```sql\n{sql}\n```", "fake/model", {"prompt_tokens": 7, "completion_tokens": 3}
        return "απάντηση", "fake/model", {"prompt_tokens": 2, "completion_tokens": 1}
    return chat


def test_run_one_records_tokens_model_and_pass():
    items = [{"id": "q", "question": "πόσα;", "reference_sql": "REF",
              "check": {"type": "value", "key": "n"}}]

    def execute(sql):
        return ["n"], [(42,)]

    llm = cmp.RecordingLLM("fake/model", chat=_fake_chat("SELECT 42 AS n LIMIT 1"))
    run = cmp.run_one("fake/model", "v2", items, execute, check, llm=llm)
    it = run["items"][0]
    assert it["ok"] and it["failure"] is None and it["sql_valid"]
    assert it["llm_calls"] == 2 and it["prompt_tokens"] == 9 and it["completion_tokens"] == 4
    assert it["models_used"] == ["fake/model"] and run["summary"]["passed"] == 1


def test_render_markdown_table():
    rows = [{"id": "q", "ok": False, "failure": "no_sql", "sql_valid": False, "latency_ms": 500,
             "prompt_tokens": 1, "completion_tokens": 1, "models_used": ["m"]}]
    md = cmp.render_markdown([{"model": "m", "model_key": "m", "variant": "v1", "tag": "r1",
                               "summary": cmp.summarize(rows), "items": rows}])
    assert "| r1 | `m` | v1 | 0/1 |" in md and "| q | no_sql |" in md
    assert "Small set" in md
    lines = md.splitlines()
    hdr = next(i for i, ln in enumerate(lines) if ln.startswith("| Run |"))
    assert lines[hdr].count("|") == lines[hdr + 1].count("|")


def test_nim_chat_fallback_can_be_disabled(monkeypatch):
    monkeypatch.setenv("NIM_API_KEY", "test")
    tried = []

    async def post(self, path, payload):
        tried.append(payload["model"])
        raise httpx.HTTPStatusError("gone", request=httpx.Request("POST", "http://x"),
                                    response=httpx.Response(410))

    monkeypatch.setattr(nc.NIMClient, "_post", post)

    async def go():
        async with nc.NIMClient() as c:
            await c.chat([{"role": "user", "content": "x"}], task=nc.NIMModel.NEMOTRON_3_ULTRA,
                         fallback=False)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(go())
    assert tried == [nc.NIMModel.NEMOTRON_3_ULTRA.value]


def test_schema_context_unchanged_by_variants():
    pr.sql_system("v0")
    pr.sql_system("v2")
    assert "NULLS LAST" in SCHEMA_CONTEXT
