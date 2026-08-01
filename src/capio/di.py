"""Service container / dependency injection (RFC-010)."""

from __future__ import annotations

from typing import Any, Dict, Iterator

from .exceptions import DependencyResolutionError, ServiceAlreadyBound


class ServiceContainer:
    """A tiny registry of named services (backends, loggers, tracers, ...).

    Capabilities resolve handles through this container (RFC-010 §4). Binding a name twice
    raises ``ServiceAlreadyBound`` unless ``replace`` is used explicitly.
    """

    def __init__(self) -> None:
        self._services: Dict[str, Any] = {}

    def bind(self, name: str, service: Any) -> None:
        if name in self._services:
            raise ServiceAlreadyBound(f"service {name!r} already bound")
        self._services[name] = service

    def bind_replace(self, name: str, service: Any) -> None:
        self._services[name] = service

    def get(self, name: str, default: Any = None) -> Any:
        return self._services.get(name, default)

    def require(self, name: str) -> Any:
        service = self._services.get(name)
        if service is None and name not in self._services:
            raise DependencyResolutionError(f"service {name!r} not bound")
        return service

    def __contains__(self, name: object) -> bool:
        return name in self._services

    def __iter__(self) -> Iterator[str]:
        return iter(self._services)

    def names(self) -> tuple[str, ...]:
        return tuple(self._services)

    def clear(self) -> None:
        self._services.clear()
