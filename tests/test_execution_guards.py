"""Throttle and debounce capability tests (RFC-018 §5-6)."""

from __future__ import annotations

import asyncio
import threading
import time

from capio import use
from capio.exceptions import ConcurrencyLimitError


def test_throttle_blocks_concurrency() -> None:
    state = {"active": 0, "max_active": 0}
    lock = threading.Lock()

    @use.throttle(limit=2, strategy="block")
    def work() -> str:
        with lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        time.sleep(0.04)
        with lock:
            state["active"] -= 1
        return "ok"

    threads = [threading.Thread(target=work) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert state["max_active"] <= 2


def test_throttle_rejects_when_limit_hit() -> None:
    results: list = []

    @use.throttle(limit=1, strategy="reject")
    def work() -> str:
        time.sleep(0.08)
        return "ok"

    def run() -> None:
        try:
            results.append(("ok", work()))
        except ConcurrencyLimitError:
            results.append(("rejected", None))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert ("rejected", None) in results


def test_async_throttle_rejects() -> None:
    @use.throttle(limit=1, strategy="reject")
    async def work() -> str:
        await asyncio.sleep(0.05)
        return "ok"

    async def main() -> list:
        results: list = []

        async def run(_: int) -> None:
            try:
                results.append(await work())
            except ConcurrencyLimitError:
                results.append("rejected")

        await asyncio.gather(run(1), run(2))
        return results

    results = asyncio.run(main())
    assert "rejected" in results


def test_debounce_leading_executes_first_drops_rest() -> None:
    state = {"calls": 0}

    @use.debounce(window="50ms", leading=True, trailing=False)
    def tick() -> str:
        state["calls"] += 1
        return "tick"

    assert tick() == "tick"
    assert tick() is None
    assert tick() is None
    time.sleep(0.08)
    assert state["calls"] == 1


def test_debounce_trailing_coalesces() -> None:
    state = {"calls": 0}

    @use.debounce(window="50ms", leading=False, trailing=True)
    def tick() -> str:
        state["calls"] += 1
        return "tick"

    assert tick() is None
    assert tick() is None
    assert tick() is None
    time.sleep(0.12)
    assert state["calls"] == 1


def test_debounce_leading_and_trailing() -> None:
    state = {"calls": 0}

    @use.debounce(window="50ms", leading=True, trailing=True)
    def tick() -> str:
        state["calls"] += 1
        return "tick"

    assert tick() == "tick"
    assert tick() is None
    time.sleep(0.12)
    assert state["calls"] == 2


def test_async_debounce_trailing() -> None:
    state = {"calls": 0}

    @use.debounce(window="50ms")
    async def tick() -> str:
        state["calls"] += 1
        await asyncio.sleep(0)
        return "tick"

    async def main() -> int:
        assert await tick() is None
        assert await tick() is None
        await asyncio.sleep(0.15)
        return state["calls"]

    assert asyncio.run(main()) == 1
