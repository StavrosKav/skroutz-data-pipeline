"""The eval can pin one model with fallback off, so a score belongs to that model."""
import types

import agents.ask.runtime as rt


def test_make_nim_llm_pins_model_without_fallback(monkeypatch):
    seen = {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def chat(self, messages, task, temperature, max_tokens, fallback=True):
            seen.update(task=task, fallback=fallback)
            return types.SimpleNamespace(model=task, latency_ms=5, content="ok")

    import alerts.nim_client as nc
    monkeypatch.setattr(nc, "NIMClient", FakeClient)
    llm = rt.make_nim_llm(model="openai/gpt-oss-20b")
    assert llm([{"role": "user", "content": "hi"}], 10) == "ok"
    assert seen == {"task": "openai/gpt-oss-20b", "fallback": False}
    assert rt.LAST_MODEL["model"] == "openai/gpt-oss-20b"
