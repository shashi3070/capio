"""Capio quickstart (README §Usage).

Run:  python -m examples.quickstart
"""

from __future__ import annotations

from capio import use


@use.circuit_breaker(failure_threshold=5, reset_timeout="30s")
@use.cache(ttl="5m")
@use.retry(max_attempts=3, delay="100ms", backoff="exponential", jitter=True)
@use.timeout(seconds=5)
@use.trace()
@use.metrics()
def fetch_user(user_id: int) -> dict:
    """Fetch a user by id (simulated)."""
    return {"id": user_id, "name": "Ada"}


@use.rate_limit(limit=10, window="1m")
def ping() -> str:
    return "pong"


@use(retry={"max_attempts": 2, "delay": "10ms"}, log={"on_success": "INFO"})
def composite_example(value: int) -> int:
    return value * 2


def main() -> None:
    print(fetch_user(1))
    print(fetch_user(1))  # served from cache
    print(ping())
    print(composite_example(21))


if __name__ == "__main__":
    main()
