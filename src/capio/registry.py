"""Capability registry (RFC-014). Maps capability names to their classes."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Dict, Iterator, Tuple, Type

from .exceptions import NameCollisionError, UnknownCapabilityError

if TYPE_CHECKING:
    from .sdk.capability import Capability


class Registry:
    """Registry of capability classes keyed by ``Capability.name``."""

    def __init__(self) -> None:
        self._capabilities: Dict[str, Type["Capability"]] = {}
        self._lock = threading.RLock()

    def register(self, capability_cls: Type["Capability"]) -> Type["Capability"]:
        name = capability_cls.name
        if not name:
            raise NameCollisionError("capability class has empty name")
        with self._lock:
            existing = self._capabilities.get(name)
            if existing is not None and existing is not capability_cls:
                raise NameCollisionError(
                    f"capability {name!r} already registered as {existing.__module__}"
                )
            self._capabilities[name] = capability_cls
        return capability_cls

    def unregister(self, name: str) -> None:
        with self._lock:
            self._capabilities.pop(name, None)

    def get(self, name: str) -> Type["Capability"]:
        with self._lock:
            capability_cls = self._capabilities.get(name)
        if capability_cls is None:
            raise UnknownCapabilityError(
                f"unknown capability {name!r}; registered: {', '.join(self.names())}"
            )
        return capability_cls

    def contains(self, name: str) -> bool:
        with self._lock:
            return name in self._capabilities

    def names(self) -> Tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._capabilities))

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.contains(name)

    def __iter__(self) -> Iterator[Type["Capability"]]:
        with self._lock:
            return iter(list(self._capabilities.values()))


registry = Registry()
