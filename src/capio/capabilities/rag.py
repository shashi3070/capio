"""RAG capability (RFC-030 §7): retrieval-augmented generation."""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from ..context import Context
from ..events import Event
from ..sdk.capability import CALL_NEXT, Capability
from ._ai import query_text


class Rag(Capability):
    name = "rag"
    version = "1.0.0"
    description = "Retrieves context documents and injects them into the prompt (RFC-030 §7)."
    priority = 410
    degradation = "bypass"

    schema = {
        "retriever": {"type": "any", "default": None},
        "top_k": {"type": "int", "default": 4, "min": 0},
        "store": {"type": "str", "default": "store.memory"},
        "namespace": {"type": "str", "default": "rag"},
        "context_key": {"type": "str", "default": "context"},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self._retriever: Optional[Callable[[str], List[Any]]] = None

    def initialize(self, services: Any) -> None:
        super().initialize(services)
        if callable(self.cfg.retriever):
            self._retriever = self.cfg.retriever

    def _retrieve(self, ctx: Context) -> List[Any]:
        query = query_text(ctx)
        if self._retriever is not None:
            docs = self._retriever(query)
        else:
            store = self.backend(self.cfg.store)
            docs = [value for _, value in (store.items(self.cfg.namespace) if store else [])]
        if self.cfg.top_k > 0:
            docs = docs[: self.cfg.top_k]
        return docs

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        docs = self._retrieve(ctx)
        ctx.kwargs = {**ctx.kwargs, self.cfg.context_key: docs}
        ctx.emit(Event("rag.retrieved", {"count": len(docs)}))
        return call_next(ctx)

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        docs = self._retrieve(ctx)
        ctx.kwargs = {**ctx.kwargs, self.cfg.context_key: docs}
        ctx.emit(Event("rag.retrieved", {"count": len(docs)}))
        return await call_next(ctx)
