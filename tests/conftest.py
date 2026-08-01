"""Shared helpers and fixtures for the capio test suite."""

from __future__ import annotations

from typing import Any, Callable

import pytest

from capio.backends.null_metrics import NullMetricsBackend
from capio.runtime import default_runtime


@pytest.fixture()
def fresh_runtime():
    runtime = default_runtime()
    backend = NullMetricsBackend()
    runtime.services.bind_replace("metrics.null", backend)
    return runtime, backend


def make_counted(fn: Callable) -> Callable:
    state = {"n": 0}

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        state["n"] += 1
        return fn(*args, **kwargs)

    wrapper.calls = state
    return wrapper
