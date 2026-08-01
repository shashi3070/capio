"""Semantic cache capability (RFC-030 §4): embedding-similarity cache."""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from ..context import Context
from ..events import Event
from ..sdk.capability import CALL_NEXT, Capability
from ._ai import cosine, query_text

_MISSING = object()


class SemanticCache(Capability):
    name = "semantic_cache"
    version = "1.0.0"
    description = "Cache keyed on embedding similarity (RFC-030 §4)."
    priority = 440
    degradation = "bypass"

    schema = {
        "backend": {"type": "str", "default": "cache.memory"},
        "embedder": {"type": "any", "default": None},
        "threshold": {"type": "number", "default": 0.9, "min": 0.0, "max": 1.0},
        "max_entries": {"type": "int", "default": 1000, "min": 1},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self._embedder: Optional[Callable[[str], list]] = None

    def initialize(self, services: Any) -> None:
        super().initialize(services)
        if callable(self.cfg.embedder):
            self._embedder = self.cfg.embedder

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        backend = self.backend(self.cfg.backend)
        if backend is None or self._embedder is None:
            return call_next(ctx)
        text = query_text(ctx)
        vector = self._embedder(text)
        index = backend.get("semantic:index", _MISSING)
        index = json.loads(index) if index is not _MISSING and isinstance(index, str) else {}
        for entry in index.values():
            score = cosine(vector, entry["vector"])
            if score >= self.cfg.threshold:
                ctx.emit(Event("semantic_cache.hit", {"score": round(score, 4)}))
                return entry["response"]
        response = call_next(ctx)
        key = f"semantic:{len(index)}"
        index[key] = {"vector": vector, "response": response}
        while len(index) > self.cfg.max_entries:
            index.pop(next(iter(index)))
        backend.set("semantic:index", json.dumps(index))
        ctx.emit(Event("semantic_cache.miss", {}))
        return response

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        return self.run(ctx, call_next)

