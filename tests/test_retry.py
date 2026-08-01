"""Retry capability tests (RFC-017)."""

from __future__ import annotations

import time

import pytest

from capio import use
from capio.exceptions import RetryExhaustedError
from capio.runtime import default_runtime


def test_retries_until_success() -> None:
    state = {"n": 0}

    @use.retry(max_attempts=5, delay="1ms", backoff="fixed", jitter=False)
    def flaky() -> str:
        state["n"] += 1
        if state["n"] < 3:
            raise ValueError("not yet")
        return "ok"

    assert flaky() == "ok"
    assert state["n"] == 3


def test_exhaustion_raises_retry_exhausted() -> None:
    @use.retry(max_attempts=3, delay="1ms", backoff="fixed", jitter=False)
    def always_fails() -> None:
        raise ValueError("boom")

    with pytest.raises(RetryExhaustedError):
        always_fails()


def test_on_final_reraise_original() -> None:
    @use.retry(max_attempts=2, delay="1ms", jitter=False, on_final="reraise_original")
    def always_fails() -> None:
        raise KeyError("boom")

    with pytest.raises(KeyError):
        always_fails()


def test_retry_if_predicate() -> None:
    state = {"n": 0}

    @use.retry(
        max_attempts=3, delay="1ms", jitter=False, retry_if=lambda ctx, exc: "transient" in str(exc)
    )
    def maybe() -> str:
        state["n"] += 1
        raise ValueError("transient")

    with pytest.raises(RetryExhaustedError):
        maybe()
    assert state["n"] == 3


def test_retry_if_false_no_retry() -> None:
    state = {"n": 0}

    @use.retry(max_attempts=3, delay="1ms", jitter=False, retry_if=lambda ctx, exc: False)
    def maybe() -> None:
        state["n"] += 1
        raise ValueError("x")

    with pytest.raises(RetryExhaustedError):
        maybe()
    assert state["n"] == 1


def test_never_retries_cancellation() -> None:
    from capio.exceptions import CapioCancelledError

    state = {"n": 0}

    @use.retry(max_attempts=3, delay="1ms", jitter=False)
    def cancelled() -> None:
        state["n"] += 1
        raise CapioCancelledError("stop")

    with pytest.raises(CapioCancelledError):
        cancelled()
    assert state["n"] == 1


def test_exhaustion_emits_event() -> None:
    runtime = default_runtime()
    seen = []

    def on_exhausted(event):
        if event.name == "retry.exhausted":
            seen.append(event.payload)

    runtime.event_bus.subscribe("*", on_exhausted)
    try:
        @use.retry(max_attempts=2, delay="1ms", jitter=False)
        def fails() -> None:
            raise ValueError("nope")

        with pytest.raises(RetryExhaustedError):
            fails()
    finally:
        runtime.event_bus.unsubscribe("*", on_exhausted)

    assert seen and seen[0]["attempts"] == 2


def test_linear_backoff_delay_is_capped() -> None:
    state = {"n": 0}

    @use.retry(
        max_attempts=4,
        delay="1ms",
        backoff="linear",
        multiplier=2.0,
        max_delay="3ms",
        jitter=False,
    )
    def fails() -> None:
        state["n"] += 1
        raise ValueError("x")

    start = time.monotonic()
    with pytest.raises(RetryExhaustedError):
        fails()
    elapsed = time.monotonic() - start
    assert state["n"] == 4
    assert elapsed < 0.5
