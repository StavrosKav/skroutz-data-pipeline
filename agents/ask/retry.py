"""Retry only what is worth retrying: timeouts, 429, 5xx, network errors."""

from __future__ import annotations

import time
from typing import Callable, TypeVar

T = TypeVar("T")


class RetryableError(Exception):
    """Raise (or wrap) in an LLM adapter to mark a transient failure."""


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (RetryableError, TimeoutError, ConnectionError)):
        return True
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is not None:
        return status == 429 or 500 <= int(status) < 600
    name = type(exc).__name__
    return name in {"TimeoutException", "ReadTimeout", "ConnectTimeout", "NetworkError",
                    "ConnectError", "RemoteProtocolError"}


def call_with_retry(fn: Callable[[], T], attempts: int = 3, base_delay: float = 1.0,
                    sleep: Callable[[float], None] = time.sleep) -> T:
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - re-raised when not retryable
            if i == attempts - 1 or not is_retryable(exc):
                raise
            sleep(base_delay * (2 ** i))
    raise AssertionError("unreachable")
