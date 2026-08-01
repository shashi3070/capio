"""Ingest capability (RFC-030 §8): chunk and index documents."""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from ..context import Context
from ..events import Event
from ..sdk.capability import CALL_NEXT, Capability
from ._ai import as_list


def _chunk_text(text: str, size: int, overlap: int) -> List[str]:
    if size <= 0:
        return [text] if text else []
    chunks: List[str] = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + size])
        if start + size >= len(text):
            break
        start += size - overlap
    return chunks


class Ingest(Capability):
    name = "ingest"
    version = "1.0.0"
    description = "Chunks and indexes documents into the store (RFC-030 §8)."
    priority = 405
    degradation = "propagate"

    schema = {
        "chunk_size": {"type": "int", "default": 512, "min": 1},
        "overlap": {"type": "int", "default": 64, "min": 0},
        "embedder": {"type": "any", "default": None},
        "store": {"type": "str", "default": "store.memory"},
        "namespace": {"type": "str", "default": "rag"},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self._embedder: Optional[Callable[[str], list]] = None

    def initialize(self, services: Any) -> None:
        super().initialize(services)
        if callable(self.cfg.embedder):
            self._embedder = self.cfg.embedder

    def _doc_text(self, doc: Any) -> str:
        if isinstance(doc, str):
            return doc
        if isinstance(doc, dict):
            return str(doc.get("text", doc.get("content", doc.get("document", ""))))
        return str(doc)

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        documents = as_list(call_next(ctx))
        stored = self._store(ctx, documents)
        return {"stored": stored}

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        documents = as_list(await call_next(ctx))
        stored = self._store(ctx, documents)
        return {"stored": stored}

    def _store(self, ctx: Context, documents: List[Any]) -> int:
        store = self.backend(self.cfg.store)
        if store is None:
            ctx.emit(Event("ingest.missing", {"store": self.cfg.store}))
            return 0
        ns = self.cfg.namespace
        total = 0
        for doc in documents:
            text = self._doc_text(doc)
            for chunk in _chunk_text(text, self.cfg.chunk_size, self.cfg.overlap):
                seq = store.sequence(ns) + 1
                value: dict[str, Any] = {"text": chunk}
                if self._embedder is not None:
                    value["embedding"] = self._embedder(chunk)
                store.put(ns, f"doc-{seq}", value)
                total += 1
        ctx.emit(Event("ingest.stored", {"chunks": total}))
        return total
