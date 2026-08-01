"""Memory capability (RFC-030 §6): retrieve and store conversational memories."""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..context import Context
from ..events import Event
from ..sdk.capability import CALL_NEXT, Capability
from ._ai import cosine, query_text, result_text


class Memory(Capability):
    name = "memory"
    version = "1.0.0"
    description = "Loads relevant memories into the request and stores the exchange (RFC-030 §6)."
    priority = 420
    degradation = "bypass"

    schema = {
        "kind": {
            "type": "str",
            "default": "conversation",
            "enum": ["conversation", "episodic", "semantic"],
        },
        "top_k": {"type": "int", "default": 5, "min": 0},
        "store": {"type": "str", "default": "store.memory"},
        "namespace": {"type": "str", "default": "memory"},
        "embedder": {"type": "any", "default": None},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self._embedder: Optional[Callable[[str], list]] = None

    def initialize(self, services: Any) -> None:
        super().initialize(services)
        if callable(self.cfg.embedder):
            self._embedder = self.cfg.embedder

    def _retrieve(self, ctx: Context) -> list:
        store = self.backend(self.cfg.store)
        if store is None:
            return []
        items = store.items(self.cfg.namespace)
        if not items:
            return []
        if self._embedder is not None:
            query = query_text(ctx)
            vector = self._embedder(query)
            items.sort(key=lambda item: cosine(vector, item[1].get("embedding", [])), reverse=True)
        else:
            items.sort(key=lambda item: self._seq(item[0]))
        if self.cfg.top_k <= 0:
            return [item[1] for item in items]
        return [item[1] for item in items[-self.cfg.top_k :]]

    @staticmethod
    def _seq(key: str) -> int:
        suffix = key.rsplit("-", 1)[-1]
        try:
            return int(suffix)
        except ValueError:
            return 0

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        memories = self._retrieve(ctx)
        ctx.kwargs = {**ctx.kwargs, "memories": memories}
        ctx.emit(Event("memory.retrieved", {"count": len(memories)}))
        response = call_next(ctx)
        self._store(ctx, response)
        return response

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        memories = self._retrieve(ctx)
        ctx.kwargs = {**ctx.kwargs, "memories": memories}
        ctx.emit(Event("memory.retrieved", {"count": len(memories)}))
        response = await call_next(ctx)
        self._store(ctx, response)
        return response

    def _store(self, ctx: Context, response: Any) -> None:
        store = self.backend(self.cfg.store)
        if store is None:
            return
        ns = self.cfg.namespace
        seq = store.sequence(ns) + 1
        value: dict[str, Any] = {"input": query_text(ctx), "output": result_text(response)}
        if self._embedder is not None:
            value["embedding"] = self._embedder(value["input"])
        store.put(ns, f"mem-{seq}", value)
        ctx.emit(Event("memory.stored", {"namespace": ns}))

