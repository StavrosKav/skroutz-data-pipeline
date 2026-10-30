"""Real adapters: PostgreSQL executor (read-only) and NVIDIA NIM LLM.

Only this file touches the network/DB; agents.ask.agent stays pure.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Sequence

STATEMENT_TIMEOUT_MS = int(os.getenv("ASK_STATEMENT_TIMEOUT_MS", "5000"))


def make_pg_executor(engine=None, timeout_ms: int = STATEMENT_TIMEOUT_MS):
    from sqlalchemy import text

    if engine is None:
        from core.db import get_ask_engine
        engine = get_ask_engine()  # read-only role, never the pipeline superuser

    def run(sql: str) -> tuple[Sequence[str], Sequence[Sequence[Any]]]:
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(text("SET TRANSACTION READ ONLY"))
                conn.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
                result = conn.execute(text(sql))
                cols = list(result.keys())
                rows = [tuple(r) for r in result.fetchall()]
            finally:
                trans.rollback()  # read-only: never commit anything
            return cols, rows

    return run


# Reasoning models (Nemotron 3 Ultra, gpt-oss-20b) spend completion tokens on hidden reasoning before the answer;
# without headroom short budgets come back empty.
REASONING_HEADROOM = 2500
LAST_MODEL: dict[str, str] = {}


def make_nim_llm(task=None, temperature: float = 0.0, model: str | None = None):
    """Sync LLM callable backed by NIMClient (NIMClient retries 429/5xx/timeouts).

    model pins one NIM model and turns fallback off (used by the model comparison eval)."""
    from alerts.nim_client import NIMClient, TaskType

    task = task or TaskType.CODE_GENERATION

    async def _call(messages: list[dict], max_tokens: int) -> str:
        async with NIMClient() as client:
            extra = {"fallback": False} if model else {}
            resp = await client.chat(messages, task=model or task, temperature=temperature,
                                     max_tokens=max_tokens + REASONING_HEADROOM, **extra)
            LAST_MODEL["model"] = resp.model
            LAST_MODEL["latency_ms"] = str(resp.latency_ms)
            return resp.content

    def llm(messages: list[dict], max_tokens: int) -> str:
        return asyncio.run(_call(messages, max_tokens))

    return llm


_AGENT = None


def get_agent():
    global _AGENT
    if _AGENT is None:
        from agents.ask.agent import AskAgent
        _AGENT = AskAgent(make_nim_llm(), make_pg_executor(), llm_attempts=1)
    return _AGENT
