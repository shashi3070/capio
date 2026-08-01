"""Async execution engine (RFC-024)."""

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING, Any

from ..context import Context

if TYPE_CHECKING:
    from ..pipeline import ExecutionPipeline


async def execute_async(pipeline: "ExecutionPipeline", *args: Any, **kwargs: Any) -> Any:
    ctx = pipeline.build_context(args, kwargs)
    ctx.loop = asyncio.get_running_loop()
    ctx.scope.enter()
    try:
        return await _invoke(pipeline, 0, ctx)
    finally:
        ctx.scope.exit()


async def _invoke(pipeline: "ExecutionPipeline", idx: int, ctx: Context) -> Any:
    if idx >= len(pipeline.steps):
        result = pipeline.fn(*ctx.args, **ctx.kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result
    step = pipeline.steps[idx]
    enable = step.cfg.get("enable")
    if enable is not None and not enable(ctx):
        return await _invoke(pipeline, idx + 1, ctx)
    result = step.run_async(ctx, lambda c: _invoke(pipeline, idx + 1, c))
    while inspect.isawaitable(result):
        result = await result
    return result
