"""Namespaced in-memory KV store backend (RFC-023 §2, RFC-030 §8)."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Tuple

_MISSING = object()


class InMemoryStore:
    """Thread-safe namespaced key-value store with TTL and sequence numbering.

    Used as the shared store for memory, retrieval, outbox, and durable workflow state.
    """

    def __init__(self) -> None:
        self._store: Dict[str, Dict[str, Tuple[Optional[float], Any]]] = {}
        self._seq: Dict[str, int] = {}
        self._lock = threading.RLock()

    def put(self, ns: str, key: str, value: Any, ttl: Optional[float] = None) -> None:
        expires = time.monotonic() + ttl if ttl is not None else None
        with self._lock:
            bucket = self._store.setdefault(ns, {})
            bucket[key] = (expires, value)
            self._seq[ns] = self._seq.get(ns, 0) + 1

    def get(self, ns: str, key: str, default: Any = _MISSING) -> Any:
        with self._lock:
            bucket = self._store.get(ns)
            if not bucket:
                return default
            item = bucket.get(key)
            if item is None:
                return default
            expires, value = item
            if expires is not None and expires <= time.monotonic():
                del bucket[key]
                return default
            return value

    def delete(self, ns: str, key: str) -> bool:
        with self._lock:
            bucket = self._store.get(ns)
            if not bucket:
                return False
            return bucket.pop(key, None) is not None

    def items(self, ns: str) -> List[Tuple[str, Any]]:
        now = time.monotonic()
        with self._lock:
            bucket = self._store.get(ns) or {}
            result: List[Tuple[str, Any]] = []
            for k, (expires, v) in list(bucket.items()):
                if expires is not None and expires <= now:
                    del bucket[k]
                else:
                    result.append((k, v))
        return result

    def scan(self, prefix: str) -> List[Tuple[str, str, Any]]:
        """Return ``(ns, key, value)`` triples where the full key starts with ``prefix``."""
        now = time.monotonic()
        out: List[Tuple[str, str, Any]] = []
        with self._lock:
            for ns, bucket in list(self._store.items()):
                for k, (expires, v) in list(bucket.items()):
                    if expires is not None and expires <= now:
                        del bucket[k]
                    elif (ns + ":" + k).startswith(prefix):
                        out.append((ns, k, v))
        return out

    def sequence(self, ns: str) -> int:
        with self._lock:
            return self._seq.get(ns, 0)

    def clear(self, ns: Optional[str] = None) -> None:
        with self._lock:
            if ns is None:
                self._store.clear()
                self._seq.clear()
            else:
                self._store.pop(ns, None)
                self._seq.pop(ns, None)
