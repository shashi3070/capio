"""Prompt cache capability (RFC-030 §5): provider-side prompt block caching."""

from __future__ import annotations

from typing import Any

from ..context import Context
from ..events import Event
from ..sdk.capability import CALL_NEXT, Capability
from ._ai import messages, sha


class PromptCache(Capability):
    name = "prompt_cache"
    version = "1.0.0"
    description = "Adds cache-control markers to prompt blocks and tracks reuse (RFC-030 §5)."
    priority = 450
    degradation = "bypass"

    schema = {
        "backend": {"type": "str", "default": "cache.memory"},
        "block_last": {"type": "bool", "default": True},
        "enable": {"type": "any", "default": None},
    }

    def _mark(self, ctx: Context) -> str:
        backend = self.backend(self.cfg.backend)
        msgs = messages(ctx)
        if msgs and isinstance(msgs[-1], dict):
            msgs[-1]["cache_control"] = {"type": "ephemeral"}
        prefix = sha(msgs[:-1]) if len(msgs) > 1 else sha(msgs)
        cached = backend is not None and backend.get(f"prompt:{prefix}") is not None
        if backend is not None:
            backend.set(f"prompt:{prefix}", True)
        ctx.emit(
            Event(
                "prompt_cache.hit" if cached else "prompt_cache.miss",
                {"prefix_hash": prefix, "blocks": len(msgs)},
            )
        )
        return prefix

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        self._mark(ctx)
        return call_next(ctx)

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        self._mark(ctx)
        return await call_next(ctx)
