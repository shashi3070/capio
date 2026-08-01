"""Circuit breaker capability tests (RFC-018 §3)."""

from __future__ import annotations

import time

import pytest

from capio import use
from capio.exceptions import CapioCancelledError, CapioTimeoutError, CircuitOpenError


def test_opens_after_threshold() -> None:
    state = {"n": 0}

    @use.circuit_breaker(failure_threshold=2, reset_timeout="100ms")
    def flaky() -> int:
        state["n"] += 1
        raise ValueError("x")

    for _ in range(2):
        with pytest.raises(ValueError):
            flaky()
    with pytest.raises(CircuitOpenError):
        flaky()
    assert state["n"] == 2  # third call rejected before reaching the function


def test_recovers_to_half_open_then_closed() -> None:
    state = {"n": 0}

    @use.circuit_breaker(failure_threshold=1, reset_timeout="80ms", success_threshold=1)
    def fn(x: int) -> int:
        state["n"] += 1
        if state["n"] == 1:
            raise ValueError("first")
        return x

    with pytest.raises(ValueError):
        fn(1)
    with pytest.raises(CircuitOpenError):
        fn(2)

    time.sleep(0.12)  # allow reset to half-open
    assert fn(3) == 3  # probe succeeds -> closed
    assert fn(4) == 4
    assert state["n"] == 3


def test_cancellation_never_counts_as_failure() -> None:
    state = {"n": 0}

    @use.circuit_breaker(failure_threshold=2, reset_timeout="100ms")
    def cancelled() -> None:
        state["n"] += 1
        raise CapioCancelledError("stop")

    for _ in range(3):
        with pytest.raises(CapioCancelledError):
            cancelled()
    assert state["n"] == 3  # circuit stayed closed


def test_record_timeouts_counts_timeouts() -> None:
    state = {"n": 0}

    @use.circuit_breaker(failure_threshold=1, reset_timeout="100ms")
    @use.timeout(seconds=0.02)
    def slow() -> None:
        state["n"] += 1
        time.sleep(0.2)

    with pytest.raises(CapioTimeoutError):
        slow()
    with pytest.raises(CircuitOpenError):
        slow()
    assert state["n"] == 1


def test_only_on_predicate() -> None:
    state = {"n": 0}

    @use.circuit_breaker(failure_threshold=1, reset_timeout="100ms", only_on=(ValueError,))
    def fn(x: int) -> int:
        state["n"] += 1
        raise KeyError("not counted")

    for _ in range(3):
        with pytest.raises(KeyError):
            fn(1)
    assert state["n"] == 3  # KeyError ignored by the breaker
