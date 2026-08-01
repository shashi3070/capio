"""In-memory cache backend with TTL and max-size eviction (RFC-016 §7)."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional, Tuple

_MISSING = object()


class MemoryCacheBackend:
    """Thread-safe dict cache with monotonic-clock TTL.

    ``get`` returns ``_MISSING`` (a module sentinel) when the key is absent or expired, so a
    stored ``None`` value is distinguishable from a miss.
    """

    def __init__(self, maxsize: Optional[int] = None) -> None:
        self._store: Dict[str, Tuple[Optional[float], Any]] = {}
        self._maxsize = maxsize
        self._lock = threading.RLock()

    def get(self, key: str, default: Any = _MISSING) -> Any:
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return default
            expires, value = item
            if expires is not None and expires <= time.monotonic():
                del self._store[key]
                return default
            return value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        expires = time.monotonic() + ttl if ttl is not None else None
        with self._lock:
            self._store[key] = (expires, value)
            self._evict()

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._store.pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def flush(self, prefix: str) -> int:
        with self._lock:
            keys = [k for k in self._store if k.startswith(prefix)]
            for k in keys:
                del self._store[k]
            return len(keys)

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._store)

    def _evict(self) -> None:
        if self._maxsize is None or len(self._store) <= self._maxsize:
            return
        now = time.monotonic()
        expired = [k for k, (e, _) in self._store.items() if e is not None and e <= now]
        for k in expired:
            del self._store[k]
        while len(self._store) > self._maxsize:
            oldest = next(iter(self._store))
            del self._store[oldest]
