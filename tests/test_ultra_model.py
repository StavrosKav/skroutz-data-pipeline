"""Model routing/fallback, SQL fence stripping, /analyze brand filter."""
import asyncio

import httpx
import pytest

from agents.ask.agent import extract_sql
from agents.ask import analyze as an
from alerts import nim_client as nc


def test_all_tasks_route_to_ultra():
    for task, model in nc.DEFAULT_MODEL_ROUTING.items():
        assert model is nc.NIMModel.NEMOTRON_3_ULTRA, task


def test_fallback_chain_order():
    assert [m.value for m in nc.FALLBACK_CHAIN] == [
        "nvidia/nemotron-3-ultra-550b-a55b",
        "nvidia/nemotron-3.5-bolt-e4b",
        "openai/gpt-oss-20b",
    ]


def _resp(model):
    return {"id": "x", "created": 0, "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "SELECT 1"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


def _err(code):
    req = httpx.Request("POST", "http://x")
    return httpx.HTTPStatusError("e", request=req, response=httpx.Response(code, request=req))


@pytest.mark.parametrize("code", [404, 410, 503])
def test_falls_back_on_unavailable(code, monkeypatch):
    tried = []

    async def fake_post(self, path, payload):
        tried.append(payload["model"])
        if payload["model"] == nc.NIMModel.NEMOTRON_3_ULTRA.value:
            raise _err(code)
        return _resp(payload["model"])

    monkeypatch.setattr(nc.NIMClient, "_post", fake_post)
    c = nc.NIMClient(api_key="k")
    r = asyncio.run(c.chat([{"role": "user", "content": "q"}]))
    assert tried == [nc.NIMModel.NEMOTRON_3_ULTRA.value, nc.NIMModel.NEMOTRON_35_BOLT.value]
    assert r.model == nc.NIMModel.NEMOTRON_35_BOLT.value


def test_no_fallback_on_400(monkeypatch):
    async def fake_post(self, path, payload):
        raise _err(400)

    monkeypatch.setattr(nc.NIMClient, "_post", fake_post)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(nc.NIMClient(api_key="k").chat([{"role": "user", "content": "q"}]))


@pytest.mark.parametrize("raw", [
    "```sql\nSELECT 1\n```",
    "```sql\nSELECT 1",
    "SELECT 1\n```",
    "sql\nSELECT 1;",
])
def test_extract_sql_strips_stray_fences(raw):
    assert extract_sql(raw) == "SELECT 1"


def test_top_brands_excludes_placeholder_brands():
    q = an._queries("smartwatch")["top_brands"]
    assert "brand IS NOT NULL" in q and "'smartwatch'" in q


def test_analyze_prompt_distinguishes_median():
    assert "never call the median an average" in an.SYSTEM_PROMPT
