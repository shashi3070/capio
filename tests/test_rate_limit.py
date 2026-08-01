"""Rate limit capability tests (RFC-018 §4)."""

from __future__ import annotations

import time

import pytest

from capio import use
from capio.exceptions import RateLimitExceededError


def test_fixed_window_rejects_over_limit() -> None:
    @use.rate_limit(limit=2, window="1s", strategy="fixed")
    def ping() -> str:
        return "pong"

    assert ping() == "pong"
    assert ping() == "pong"
    with pytest.raises(RateLimitExceededError):
        ping()


def test_sliding_window_rejects() -> None:
    @use.rate_limit(limit=2, window="1s", strategy="sliding")
    def ping() -> str:
        return "pong"

    assert ping() == "pong"
    assert ping() == "pong"
    with pytest.raises(RateLimitExceededError):
        ping()


def test_window_resets() -> None:
    @use.rate_limit(limit=1, window="50ms", strategy="fixed")
    def ping() -> str:
        return "pong"

    assert ping() == "pong"
    with pytest.raises(RateLimitExceededError):
        ping()
    time.sleep(0.07)
    assert ping() == "pong"


def test_on_exceeded_return() -> None:
    @use.rate_limit(
        limit=1, window="1s", strategy="fixed", on_exceeded="return", fallback="limited"
    )
    def ping() -> str:
        return "pong"

    assert ping() == "pong"
    assert ping() == "limited"


def test_token_bucket_supports_burst() -> None:
    @use.rate_limit(strategy="token_bucket", bucket_capacity=3, refill_rate="10/s")
    def ping() -> str:
        return "pong"

    assert ping() == "pong"
    assert ping() == "pong"
    assert ping() == "pong"
    with pytest.raises(RateLimitExceededError):
        ping()


def test_retry_after_in_exception() -> None:
    @use.rate_limit(limit=1, window="10s", strategy="fixed")
    def ping() -> str:
        return "pong"

    ping()
    try:
        ping()
        raise AssertionError("should have been limited")
    except RateLimitExceededError as exc:
        assert exc.retry_after is not None
