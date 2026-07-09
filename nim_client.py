"""
NVIDIA NIM API Client
Async client for calling NIM endpoints (chat completions, embeddings).
Routes requests to appropriate models based on task type.

Requirements:
- NIM_API_KEY in .env (get from https://build.nvidia.com)
- NIM_BASE_URL (default: https://integrate.api.nvidia.com/v1)
- httpx, tenacity, pydantic
"""

import os
import json
import logging
from enum import Enum
from typing import Any, Optional
from dataclasses import dataclass

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential_jitter, retry_if_exception_type
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class NIMModel(str, Enum):
    """NIM models, restricted to what's actually invokable on the free-tier
    build.nvidia.com catalog (verified live 2026-07-09 — being listed in
    GET /v1/models does NOT mean an account can invoke it; most
    non-Llama/Nemotron-Llama models 404 with "Not found for account")."""
    # Chat / Reasoning
    NEMOTRON_SUPER_49B = "nvidia/llama-3.3-nemotron-super-49b-v1"  # Best reasoning available
    LLAMA_3_3_70B = "meta/llama-3.3-70b-instruct"          # General chat, newer than 3.1
    LLAMA_3_1_70B = "meta/llama-3.1-70b-instruct"          # General chat, good balance
    LLAMA_3_1_8B = "meta/llama-3.1-8b-instruct"            # Fast, cheap, simple tasks

    # Embeddings — only nv-embedqa-e5-v5 is invokable on this account;
    # nv-embed-v1/v2 and llama-3.2 embed variants all 404.
    NV_EMBED_QA = "nvidia/nv-embedqa-e5-v5"


class TaskType(str, Enum):
    """Task types that map to optimal models."""
    CHAT_DEFAULT = "chat_default"
    REASONING_HEAVY = "reasoning_heavy"
    CODE_GENERATION = "code_generation"
    CODE_REVIEW = "code_review"
    FAST_SIMPLE = "fast_simple"
    MULTILINGUAL = "multilingual"
    EMBEDDING_QA = "embedding_qa"
    EMBEDDING_GENERAL = "embedding_general"


# Default routing: task -> model.
# No dedicated code or multilingual model is invokable on this account
# (codellama-70b-instruct, mistral-large, codestral, granite, starcoder,
# deepseek-coder all 404) — those tasks fall back to the best general model.
DEFAULT_MODEL_ROUTING: dict[TaskType, NIMModel] = {
    TaskType.CHAT_DEFAULT: NIMModel.LLAMA_3_1_70B,
    TaskType.REASONING_HEAVY: NIMModel.NEMOTRON_SUPER_49B,
    TaskType.CODE_GENERATION: NIMModel.LLAMA_3_3_70B,
    TaskType.CODE_REVIEW: NIMModel.LLAMA_3_3_70B,
    TaskType.FAST_SIMPLE: NIMModel.LLAMA_3_1_8B,
    TaskType.MULTILINGUAL: NIMModel.LLAMA_3_3_70B,
    TaskType.EMBEDDING_QA: NIMModel.NV_EMBED_QA,
    TaskType.EMBEDDING_GENERAL: NIMModel.NV_EMBED_QA,
}


# Cost estimates (USD per 1M tokens in/out) - for monitoring
MODEL_COSTS: dict[NIMModel, tuple[float, float]] = {
    NIMModel.NEMOTRON_SUPER_49B: (0.0, 0.0),    # NVIDIA-hosted, free tier
    NIMModel.LLAMA_3_3_70B: (0.0, 0.0),
    NIMModel.LLAMA_3_1_70B: (0.0, 0.0),
    NIMModel.LLAMA_3_1_8B: (0.0, 0.0),
    NIMModel.NV_EMBED_QA: (0.0, 0.0),
}


class ChatMessage(BaseModel):
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    name: Optional[str] = None
    tool_calls: Optional[list[dict]] = None
    tool_call_id: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    top_p: float = 1.0
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    stream: bool = False
    stop: Optional[list[str]] = None


class ChatCompletionChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: Optional[str] = None


class ChatCompletionUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: ChatCompletionUsage


class EmbeddingRequest(BaseModel):
    model: str
    input: list[str] | str
    encoding_format: str = "float"


class EmbeddingData(BaseModel):
    object: str = "embedding"
    index: int
    embedding: list[float]


class EmbeddingUsage(BaseModel):
    prompt_tokens: int
    total_tokens: int


class EmbeddingResponse(BaseModel):
    object: str = "list"
    data: list[EmbeddingData]
    model: str
    usage: EmbeddingUsage


@dataclass
class NIMResponse:
    """Unified response wrapper with metadata."""
    content: str
    model: str
    usage: dict
    latency_ms: int
    cost_usd: float = 0.0


class NIMClient:
    """
    Async NIM API client with:
    - Automatic model routing by task type
    - Retry with exponential backoff
    - Request/response logging
    - Cost/latency tracking
    - Streaming support
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_routing: Optional[dict[TaskType, NIMModel]] = None,
        default_timeout: float = 60.0,
        max_retries: int = 3,
    ):
        self.api_key = api_key or os.getenv("NIM_API_KEY")
        if not self.api_key:
            raise ValueError("NIM_API_KEY required (set in .env or pass to constructor)")

        self.base_url = (base_url or os.getenv("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")).rstrip("/")
        self.model_routing = model_routing or DEFAULT_MODEL_ROUTING
        self.default_timeout = default_timeout
        self.max_retries = max_retries

        self._client: Optional[httpx.AsyncClient] = None
        self._request_count = 0
        self._total_latency_ms = 0
        self._total_cost = 0.0

    async def __aenter__(self) -> "NIMClient":
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(self.default_timeout),
        )
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()

    def _get_model(self, task: TaskType | NIMModel | str) -> str:
        """Resolve task type or model enum to model string."""
        if isinstance(task, NIMModel):
            return task.value
        if isinstance(task, TaskType):
            return self.model_routing.get(task, DEFAULT_MODEL_ROUTING[task]).value
        return task  # assume raw model string

    @retry(
        wait=wait_exponential_jitter(initial=1, max=30),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError, httpx.NetworkError)),
        reraise=True,
    )
    async def _post(self, path: str, json_data: dict) -> dict:
        assert self._client, "Client not initialized. Use async with NIMClient() as client:"

        response = await self._client.post(path, json=json_data)
        response.raise_for_status()
        return response.json()

    async def chat(
        self,
        messages: list[dict[str, str]] | list[ChatMessage],
        task: TaskType | NIMModel | str = TaskType.CHAT_DEFAULT,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        **kwargs,
    ) -> NIMResponse | httpx.ByteStream:
        """
        Chat completion.

        Args:
            messages: List of {"role": "...", "content": "..."} or ChatMessage objects
            task: TaskType (auto-routes to best model), NIMModel, or raw model string
            temperature: Sampling temperature
            max_tokens: Max completion tokens
            stream: If True, returns async byte stream for SSE parsing

        Returns:
            NIMResponse (non-streaming) or async stream iterator
        """
        import time
        start = time.perf_counter()

        model = self._get_model(task)

        # Normalize messages
        norm_messages = [
            ChatMessage(role=m["role"], content=m["content"]) if isinstance(m, dict) else m
            for m in messages
        ]

        request = ChatCompletionRequest(
            model=model,
            messages=norm_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
            **kwargs,
        )

        logger.debug(f"NIM chat: model={model}, messages={len(norm_messages)}, stream={stream}")

        if stream:
            return self._stream_chat(request)

        response_data = await self._post("/chat/completions", request.model_dump(exclude_none=True))
        response = ChatCompletionResponse(**response_data)

        latency_ms = int((time.perf_counter() - start) * 1000)
        self._request_count += 1
        self._total_latency_ms += latency_ms

        # Cost tracking (NVIDIA-hosted NIMs are free tier, but track for future)
        in_cost, out_cost = MODEL_COSTS.get(NIMModel(model), (0.0, 0.0))
        cost = (response.usage.prompt_tokens * in_cost + response.usage.completion_tokens * out_cost) / 1_000_000
        self._total_cost += cost

        return NIMResponse(
            content=response.choices[0].message.content or "",
            model=response.model,
            usage=response.usage.model_dump(),
            latency_ms=latency_ms,
            cost_usd=cost,
        )

    async def _stream_chat(self, request: ChatCompletionRequest) -> httpx.ByteStream:
        """Stream chat completions via SSE."""
        assert self._client

        async with self._client.stream(
            "POST",
            "/chat/completions",
            json=request.model_dump(exclude_none=True),
            timeout=httpx.Timeout(self.default_timeout * 3),  # longer for streaming
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                yield chunk

    async def embed(
        self,
        texts: list[str] | str,
        task: TaskType | NIMModel | str = TaskType.EMBEDDING_GENERAL,
    ) -> list[list[float]]:
        """
        Generate embeddings.

        Returns: list of embedding vectors (one per input text).
        """
        import time
        start = time.perf_counter()

        model = self._get_model(task)

        request = EmbeddingRequest(
            model=model,
            input=texts if isinstance(texts, list) else [texts],
        )

        response_data = await self._post("/embeddings", request.model_dump())
        response = EmbeddingResponse(**response_data)

        latency_ms = int((time.perf_counter() - start) * 1000)
        self._request_count += 1
        self._total_latency_ms += latency_ms

        # Sort by index to match input order
        embeddings = sorted(response.data, key=lambda d: d.index)
        return [d.embedding for d in embeddings]

    def get_stats(self) -> dict:
        """Return client usage statistics."""
        return {
            "requests": self._request_count,
            "total_latency_ms": self._total_latency_ms,
            "avg_latency_ms": self._total_latency_ms / max(1, self._request_count),
            "total_cost_usd": self._total_cost,
        }


# Convenience function for simple one-off calls
async def quick_chat(
    prompt: str,
    system: str = "You are a helpful assistant.",
    task: TaskType = TaskType.CHAT_DEFAULT,
    **kwargs,
) -> str:
    """Quick one-liner for simple chat calls."""
    async with NIMClient() as client:
        resp = await client.chat([
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ], task=task, **kwargs)
        return resp.content


async def quick_embed(texts: list[str], task: TaskType = TaskType.EMBEDDING_GENERAL) -> list[list[float]]:
    """Quick one-liner for embeddings."""
    async with NIMClient() as client:
        return await client.embed(texts, task=task)


# Example usage / testing
if __name__ == "__main__":
    import asyncio

    async def test():
        async with NIMClient() as client:
            # Chat test
            resp = await client.chat(
                messages=[
                    {"role": "system", "content": "You are a price analysis assistant."},
                    {"role": "user", "content": "Why do Xiaomi phones discount more often than iPhones?"},
                ],
                task=TaskType.REASONING_HEAVY,
            )
            print(f"Response: {resp.content[:200]}...")
            print(f"Model: {resp.model}, Latency: {resp.latency_ms}ms, Tokens: {resp.usage}")

            # Embedding test
            embeds = await client.embed(
                texts=["Xiaomi Redmi Note 13 Pro", "iPhone 15 Pro"],
                task=TaskType.EMBEDDING_QA,
            )
            print(f"Embeddings: {len(embeds)} vectors, dim={len(embeds[0])}")

            print(f"Stats: {client.get_stats()}")

    asyncio.run(test())