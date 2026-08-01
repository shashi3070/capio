"""Timeout capability tests (RFC-018 §2)."""

from __future__ import annotations

import asyncio
import time

import pytest

from capio import use
from capio.exceptions import CapioTimeoutError


def test_sync_returns_fast() -> None:
    @use.timeout(seconds=1)
    def fast() -> str:
        return "ok"

    assert fast() == "ok"


def test_sync_slow_fires_deadline_warning() -> None:
    from capio.runtime import default_runtime

    runtime = default_runtime()
    seen = []

    def on_warning(event):
        if event.name == "timeout.warning":
            seen.append(event)

    runtime.event_bus.subscribe("*", on_warning)
    try:
        @use.timeout(seconds=0.05, hard=True, raise_on=False, return_on="late")
        def slow() -> str:
            time.sleep(0.2)
            return "late"

        assert slow() == "late"
    finally:
        runtime.event_bus.unsubscribe("*", on_warning)

    assert seen


def test_sync_slow_with_raise_on_raises() -> None:
    @use.timeout(seconds=0.05, hard=True)
    def slow() -> str:
        time.sleep(0.2)
        return "late"

    with pytest.raises(CapioTimeoutError):
        slow()


def test_async_timeout_raises() -> None:
    @use.timeout(seconds=0.05)
    async def slow() -> str:
        await asyncio.sleep(0.5)
        return "late"

    with pytest.raises(CapioTimeoutError):
        asyncio.run(slow())


def test_async_timeout_return_on() -> None:
    @use.timeout(seconds=0.05, raise_on=False, return_on="fallback")
    async def slow() -> str:
        await asyncio.sleep(0.5)
        return "late"

    assert asyncio.run(slow()) == "fallback"


def test_async_fast_succeeds() -> None:
    @use.timeout(seconds=1)
    async def fast(x: int) -> int:
        await asyncio.sleep(0.001)
        return x + 1

    assert asyncio.run(fast(1)) == 2


def test_raise_and_return_are_exclusive() -> None:
    from capio.exceptions import ConfigurationError

    @use.timeout(seconds=1, raise_on=True, return_on="x")
    def fn() -> None:
        pass

    with pytest.raises(ConfigurationError):
        fn()  # pipeline build raises (configure) on first invocation
