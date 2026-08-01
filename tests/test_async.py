"""Async pipeline tests (RFC-012 §6)."""

from __future__ import annotations

import asyncio

import pytest

from capio import use
from capio.context import current_context
from capio.exceptions import RetryExhaustedError


def test_async_retry_succeeds() -> None:
    state = {"n": 0}

    @use.retry(max_attempts=4, delay="1ms", jitter=False)
    async def fetch() -> int:
        state["n"] += 1
        if state["n"] < 3:
            raise ValueError("retry me")
        return 42

    assert asyncio.run(fetch()) == 42
    assert state["n"] == 3


def test_async_retry_exhausts() -> None:
    @use.retry(max_attempts=2, delay="1ms", jitter=False)
    async def fetch() -> int:
        raise ValueError("boom")

    with pytest.raises(RetryExhaustedError):
        asyncio.run(fetch())


def test_async_cache() -> None:
    state = {"n": 0}

    @use.cache(ttl="1m")
    async def compute(x: int) -> int:
        state["n"] += 1
        await asyncio.sleep(0)
        return x * 3

    async def main() -> None:
        assert await compute(5) == 15
        assert await compute(5) == 15

    asyncio.run(main())
    assert state["n"] == 1


def test_async_use_context_injection() -> None:
    @use.retry(max_attempts=1)
    @use.context()
    async def whoami(x: int, ctx) -> tuple:
        return x, ctx.invocation_id

    x, invocation_id = asyncio.run(whoami(9))
    assert x == 9
    assert invocation_id.startswith("inv-")


def test_async_deep_pipeline() -> None:
    state = {"n": 0}

    @use.retry(max_attempts=3, delay="1ms", jitter=False)
    @use.cache(ttl="1m")
    @use.timeout(seconds=2)
    async def greet(name: str) -> str:
        state["n"] += 1
        await asyncio.sleep(0)
        return f"hello {name}"

    async def main() -> None:
        assert await greet("bob") == "hello bob"
        assert await greet("bob") == "hello bob"

    asyncio.run(main())
    assert state["n"] == 1


def test_async_pipeline_reports_kind() -> None:
    from capio import pipeline

    @use.retry(max_attempts=1)
    async def fn() -> int:
        return 1

    assert pipeline(fn).kind == "async"


def test_async_current_context_inside_fn() -> None:
    seen = {}

    @use.retry(max_attempts=1)
    async def probe() -> None:
        ctx = current_context()
        seen["name"] = ctx.fn_name
        seen["invocation_id"] = ctx.invocation_id

    asyncio.run(probe())
    assert seen["name"] == "probe"
    assert seen["invocation_id"].startswith("inv-")
