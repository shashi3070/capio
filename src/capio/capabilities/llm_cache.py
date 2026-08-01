"""LLM cache capability (RFC-030 §3): exact-match prompt caching."""

from __future__ import annotations

from typing import Any

from ..config import parse_duration
from ..context import Context
from ..events import Event
from ..sdk.capability import CALL_NEXT, Capability
from ._ai import request_signature

_MISSING = object()


class LLMCache(Capability):
    name = "llm_cache"
    version = "1.0.0"
    description = "Exact-match cache for identical LLM requests (RFC-030 §3)."
    priority = 430
    degradation = "bypass"

    schema = {
        "backend": {"type": "str", "default": "cache.memory"},
        "ttl": {"type": "any", "default": None},
        "enable": {"type": "any", "default": None},
    }

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        backend = self.backend(self.cfg.backend)
        if backend is None:
            return call_next(ctx)
        key = f"llm:{request_signature(ctx)}"
        cached = backend.get(key, _MISSING)
        if cached is not _MISSING:
            ctx.emit(Event("llm_cache.hit", {"key": key}))
            return cached
        response = call_next(ctx)
        ttl = parse_duration(self.cfg.ttl) if self.cfg.ttl else None
        backend.set(key, response, ttl=ttl)
        ctx.emit(Event("llm_cache.miss", {"key": key}))
        return response

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        backend = self.backend(self.cfg.backend)
        if backend is None:
            return await call_next(ctx)
        key = f"llm:{request_signature(ctx)}"
        cached = backend.get(key, _MISSING)
        if cached is not _MISSING:
            ctx.emit(Event("llm_cache.hit", {"key": key}))
            return cached
        response = await call_next(ctx)
        ttl = parse_duration(self.cfg.ttl) if self.cfg.ttl else None
        backend.set(key, response, ttl=ttl)
        ctx.emit(Event("llm_cache.miss", {"key": key}))
        return response
