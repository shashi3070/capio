"""In-memory append-only audit log backend (RFC-020 §3)."""

from __future__ import annotations

import hashlib
import threading
import time
import uuid
from typing import Any, Dict, List, Optional


class InMemoryAuditBackend:
    """Thread-safe append-only audit log.

    Each record is stored with a ``hash`` field chaining the previous record's hash, giving
    tamper-evidence: ``verify`` recomputes the chain and reports any discontinuity.
    """

    def __init__(self) -> None:
        self._records: List[Dict[str, Any]] = []
        self._lock = threading.RLock()

    def append(self, record: Dict[str, Any]) -> Dict[str, Any]:
        entry = dict(record)
        entry.setdefault("id", str(uuid.uuid4()))
        entry.setdefault("timestamp", time.time())
        prev = self._records[-1].get("hash") if self._records else "0000"
        body = "|".join(f"{k}={entry[k]}" for k in sorted(entry) if k != "hash")
        entry["hash"] = hashlib.sha256(f"{prev}:{body}".encode()).hexdigest()
        with self._lock:
            self._records.append(entry)
        return dict(entry)

    def query(
        self,
        *,
        actor: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            items = [
                dict(r)
                for r in self._records
                if (actor is None or r.get("actor") == actor)
                and (action is None or r.get("action") == action)
            ]
        return items[-limit:]

    def verify(self) -> bool:
        """Return True if the hash chain is intact."""
        with self._lock:
            prev = "0000"
            for r in self._records:
                if r.get("hash") is None:
                    return False
                body = "|".join(f"{k}={r[k]}" for k in sorted(r) if k != "hash")
                if hashlib.sha256(f"{prev}:{body}".encode()).hexdigest() != r["hash"]:
                    return False
                prev = r["hash"]
        return True

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._records)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
