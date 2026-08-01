"""In-memory pub/sub broker backend (RFC-023 §2-3)."""

from __future__ import annotations

import threading
import uuid
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple


class InMemoryBroker:
    """Thread-safe in-process topic pub/sub with per-group consumer offsets.

    ``publish`` appends a message to a topic. ``consume`` returns the next unacked message
    for a (topic, group) pair, tracking a per-group cursor. ``ack`` marks a message delivered.
    """

    def __init__(self) -> None:
        self._topics: Dict[str, Deque[Dict[str, Any]]] = {}
        self._cursor: Dict[Tuple[str, str], int] = {}
        self._lock = threading.RLock()

    def publish(
        self,
        topic: str,
        payload: Any,
        *,
        headers: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        message = {
            "id": str(uuid.uuid4()),
            "topic": topic,
            "payload": payload,
            "headers": dict(headers or {}),
        }
        with self._lock:
            self._topics.setdefault(topic, deque()).append(message)
        return message

    def consume(self, topic: str, group: str = "default") -> Optional[Dict[str, Any]]:
        with self._lock:
            messages = self._topics.get(topic)
            if not messages:
                return None
            cursor = self._cursor.get((topic, group), 0)
            if cursor >= len(messages):
                return None
            message = messages[cursor]
            self._cursor[(topic, group)] = cursor + 1
            return dict(message)

    def peek(self, topic: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(m) for m in self._topics.get(topic, deque())]

    @property
    def size(self) -> int:
        with self._lock:
            return sum(len(q) for q in self._topics.values())

    def clear(self) -> None:
        with self._lock:
            self._topics.clear()
            self._cursor.clear()
