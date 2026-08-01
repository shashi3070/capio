"""Event bus (RFC-008). Capabilities emit events via ``ctx.emit``."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

_log = logging.getLogger("capio.events")


@dataclass
class Event:
    """A single event published on the bus (RFC-008 §2)."""

    name: str
    payload: Dict[str, Any] = field(default_factory=dict)
    time: float = field(default_factory=time.monotonic)
    runtime: str = "default"
    ctx: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "payload": self.payload,
            "time": self.time,
            "runtime": self.runtime,
            "ctx": self.ctx,
        }


EventHandler = Callable[[Event], Any]


class EventBus:
    """Synchronous in-process event bus with exact-name and wildcard subscriptions."""

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[EventHandler]] = defaultdict(list)

    def subscribe(self, name: str, handler: EventHandler) -> EventHandler:
        """Subscribe ``handler`` to events named ``name`` (``"*"`` subscribes to all)."""
        self._subscribers[name].append(handler)
        return handler

    def unsubscribe(self, name: str, handler: EventHandler) -> None:
        try:
            self._subscribers[name].remove(handler)
        except ValueError:
            pass

    def publish(self, event: Event) -> None:
        for name in (event.name, "*"):
            for handler in list(self._subscribers.get(name, ())):
                try:
                    handler(event)
                except Exception:  # noqa: BLE001 - subscriber errors never break the invocation
                    _log.exception("event handler for %r failed", name)

    def clear(self) -> None:
        self._subscribers.clear()
