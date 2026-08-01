"""Cache capability tests (RFC-016)."""

from __future__ import annotations

import threading
import time

import pytest

from capio import use


def test_hit_and_miss() -> None:
    state = {"n": 0}

    @use.cache(ttl="1m")
    def double(x: int) -> int:
        state["n"] += 1
        return x * 2

    assert double(2) == 4
    assert double(2) == 4
    assert state["n"] == 1
    assert double(3) == 6
    assert state["n"] == 2


def test_none_result_is_cached() -> None:
    state = {"n": 0}

    @use.cache(ttl="1m")
    def nothing(x: int) -> None:
        state["n"] += 1
        return None

    assert nothing(1) is None
    assert nothing(1) is None
    assert state["n"] == 1


def test_ttl_expiry() -> None:
    state = {"n": 0}

    @use.cache(ttl="50ms")
    def now_value() -> int:
        state["n"] += 1
        return state["n"]

    assert now_value() == 1
    assert now_value() == 1
    time.sleep(0.08)
    assert now_value() == 2


def test_key_prefix_and_scope() -> None:
    state = {"n": 0}

    @use.cache(ttl="1m", key_prefix="op:")
    def op(x: int) -> int:
        state["n"] += 1
        return x

    assert op(5) == 5
    assert op(5) == 5
    assert state["n"] == 1


def test_cache_when_predicate() -> None:
    state = {"n": 0}

    @use.cache(ttl="1m", cache_when=lambda ctx, result: result >= 10)
    def compute(x: int) -> int:
        state["n"] += 1
        return x

    assert compute(1) == 1
    assert compute(1) == 1  # not cached -> executed again
    assert state["n"] == 2
    assert compute(10) == 10
    assert compute(10) == 10
    assert state["n"] == 3


def test_errors_not_cached_by_default() -> None:
    state = {"n": 0}

    @use.cache(ttl="1m")
    def flaky() -> int:
        state["n"] += 1
        if state["n"] == 1:
            raise ValueError("x")
        return 42

    with pytest.raises(ValueError):
        flaky()
    assert flaky() == 42
    assert state["n"] == 2


def test_cache_on_error() -> None:

    state = {"n": 0}

    @use.cache(ttl="1m", cache_on_error=True)
    def fail() -> int:
        state["n"] += 1
        raise ValueError("always")

    for _ in range(2):
        with pytest.raises(ValueError):
            fail()
    assert state["n"] == 1


def test_singleflight_sync_concurrency() -> None:
    state = {"n": 0}

    @use.cache(ttl="1m", stampede="singleflight")
    def expensive(x: int) -> int:
        state["n"] += 1
        time.sleep(0.05)
        return x

    results = []
    threads = [threading.Thread(target=lambda: results.append(expensive(7))) for _ in range(5)]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(r == 7 for r in results)
    assert state["n"] == 1


def test_custom_key_builder() -> None:
    state = {"n": 0}

    @use.cache(ttl="1m", key=lambda ctx, a, b: f"add:{a}-{b}")
    def add(a: int, b: int) -> int:
        state["n"] += 1
        return a + b

    assert add(1, 2) == 3
    assert add(1, 2) == 3
    assert state["n"] == 1
    assert add(2, 1) == 3
    assert state["n"] == 2
