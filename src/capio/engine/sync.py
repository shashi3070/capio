"""Sync execution engine (RFC-024)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..context import Context

if TYPE_CHECKING:
    from ..pipeline import ExecutionPipeline


def execute_sync(pipeline: "ExecutionPipeline", *args: Any, **kwargs: Any) -> Any:
    ctx = pipeline.build_context(args, kwargs)
    ctx.scope.enter()
    try:
        return _invoke(pipeline, 0, ctx)
    finally:
        ctx.scope.exit()


def _invoke(pipeline: "ExecutionPipeline", idx: int, ctx: Context) -> Any:
    if idx >= len(pipeline.steps):
        return pipeline.fn(*ctx.args, **ctx.kwargs)
    step = pipeline.steps[idx]
    enable = step.cfg.get("enable")
    if enable is not None and not enable(ctx):
        return _invoke(pipeline, idx + 1, ctx)
    return step.run_sync(ctx, lambda c: _invoke(pipeline, idx + 1, c))
