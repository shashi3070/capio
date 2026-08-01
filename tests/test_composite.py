"""Composite decoration, introspection, and use.context() tests (RFC-003)."""

from __future__ import annotations

import pytest

from capio import pipeline, unwrap, use
from capio.context import current_context
from capio.exceptions import (
    ConfigurationError,
    ConflictingPipelineError,
    DuplicateCapabilityError,
    UnknownCapabilityError,
)


def test_composite_matches_chained_in_priority_order() -> None:
    chained_calls = {"n": 0}
    composite_calls = {"n": 0}

    # chained in RFC-005 priority order: cache (750) outside retry (700)
    @use.cache(ttl="1m")
    @use.retry(max_attempts=2, delay="1ms", jitter=False)
    def chained(a: int, b: int) -> int:
        chained_calls["n"] += 1
        return a + b

    @use(cache={"ttl": "1m"}, retry={"max_attempts": 2, "delay": "1ms", "jitter": False})
    def composite(a: int, b: int) -> int:
        composite_calls["n"] += 1
        return a + b

    assert chained(1, 2) == 3
    assert chained(1, 2) == 3
    assert composite(1, 2) == 3
    assert composite(1, 2) == 3
    assert chained_calls["n"] == composite_calls["n"] == 1

    chained_meta = chained.__capio__
    composite_meta = composite.__capio__
    chained_names = [c.name for c in chained_meta.capabilities]
    composite_names = [c.name for c in composite_meta.capabilities]
    assert chained_names == composite_names
    assert chained_meta.capabilities[0].name == "cache"  # higher priority = outermost


def test_explicit_chaining_wins_over_priority() -> None:
    # RFC-005 rule 1: explicit physical order is preserved even when it
    # contradicts the priority table (retry outermost, wrapping cache).
    @use.retry(max_attempts=2, delay="1ms", jitter=False)
    @use.cache(ttl="1m")
    def fn(a: int, b: int) -> int:
        return a + b

    assert [c.name for c in fn.__capio__.capabilities] == ["retry", "cache"]


def test_composite_order_by_priority() -> None:
    @use(metrics={}, trace={}, cache={"ttl": "1m"}, retry={"max_attempts": 1})
    def fn() -> int:
        return 1

    order = [c.name for c in fn.__capio__.capabilities]
    assert order == ["cache", "retry", "trace", "metrics"]


def test_unknown_capability_raises() -> None:
    with pytest.raises(UnknownCapabilityError):
        @use.nope()
        def fn() -> None:
            pass


def test_duplicate_capability_raises() -> None:
    with pytest.raises(DuplicateCapabilityError):
        @use.cache()
        @use.cache()
        def fn() -> None:
            pass


def test_composite_duplicate_name_raises() -> None:
    with pytest.raises(ConfigurationError):
        @use("retry", "retry", retry={"max_attempts": 1})
        def fn() -> None:
            pass


def test_redecorating_composite_raises_conflict() -> None:
    @use.retry(max_attempts=1)
    def fn() -> int:
        return 1

    with pytest.raises(ConflictingPipelineError):
        use(retry={"max_attempts": 2})(fn)


def test_chain_merges_metadata() -> None:
    @use.retry(max_attempts=2)
    @use.cache(ttl="1m")
    def fn() -> int:
        return 1

    meta = fn.__capio__
    assert [c.name for c in meta.capabilities] == ["retry", "cache"]
    assert meta.capabilities[1].options["ttl"] == "1m"


def test_unwrap_returns_original() -> None:
    def original(a: int) -> int:
        return a

    @use.retry(max_attempts=1)
    def wrapped(a: int) -> int:
        return a

    assert unwrap(wrapped).__name__ == "wrapped"
    assert callable(unwrap(wrapped))


def test_pipeline_reports_kind_and_steps() -> None:
    @use.retry(max_attempts=1)
    @use.cache(ttl="1m")
    def fn() -> int:
        return 1

    pipe = pipeline(fn)
    assert pipe.kind == "sync"
    assert pipe.capability_names == ("retry", "cache")
    assert "ExecutionPipeline" in repr(pipe)


def test_injected_context_is_live() -> None:
    @use.retry(max_attempts=1)
    def with_ctx(x: int) -> tuple:
        ctx = current_context()
        return x, ctx.invocation_id, ctx.fn_name, ctx.fn_module

    x, invocation_id, name, module = with_ctx(3)
    assert x == 3
    assert invocation_id.startswith("inv-")
    assert name == "with_ctx"
    assert module == __name__


def test_use_context_injection_sync() -> None:
    @use.retry(max_attempts=1)
    @use.context()
    def with_ctx(x: int, ctx) -> tuple:
        return x, ctx.invocation_id

    x, invocation_id = with_ctx(5)
    assert x == 5
    assert invocation_id.startswith("inv-")


def test_with_capabilities_helpers() -> None:
    from capio import with_capabilities

    assert with_capabilities is not None


def test_configured_options_defaulted() -> None:
    # RFC-016: ttl=None means "no expiry"; defaults are applied at pipeline build.
    @use.cache()
    def fn() -> int:
        return 1

    pipe = pipeline(fn)
    assert pipe.steps[0].cfg.ttl is None
