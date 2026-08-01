"""The Capability base class (RFC-012 §2-3).

The single required method is ``run(ctx, call_next)``. Capabilities that must be
async-aware (they ``await call_next(ctx)`` or await on other I/O) override ``run_async``;
everything else inherits the default which delegates to ``run``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, Tuple

from ..config import FrozenConfig

if TYPE_CHECKING:
    from ..context import Context
    from ..di import ServiceContainer

CALL_NEXT = Callable[["Context"], Any]


class Capability:
    """Base class for all capabilities (RFC-012 §2)."""

    name: str = ""
    version: str = "1.0.0"
    description: str = ""
    schema: Dict[str, Any] = {}
    priority: int = 500
    supports: Tuple[str, ...] = ("sync", "async")
    depends_on: Tuple[str, ...] = ()
    requires_backends: Tuple[str, ...] = ()
    degradation: str = "propagate"  # "bypass" | "propagate" | "retry-later"

    def __init__(self) -> None:
        self.cfg: FrozenConfig = FrozenConfig()
        self.services: "ServiceContainer" = None  # type: ignore[assignment]
        self.instance_id: str = ""

    # -- lifecycle (RFC-011 §7) --------------------------------------------
    def configure(self, config: FrozenConfig) -> None:
        """Receive the validated, frozen per-application configuration."""
        self.cfg = config

    def initialize(self, services: "ServiceContainer") -> None:
        """Resolve declared dependencies/backends from the service container."""
        self.services = services

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def destroy(self) -> None:
        pass

    # -- the core contract (RFC-012 §3) --------------------------------------
    def run(self, ctx: "Context", call_next: CALL_NEXT) -> Any:
        """Execute this capability step around the inner steps.

        Implementations call ``call_next(ctx)`` exactly once (or loop over it for retries),
        control what happens before/after, and return the inner result.
        """
        raise NotImplementedError(f"{type(self).__name__} must implement run()")

    def run_sync(self, ctx: "Context", call_next: CALL_NEXT) -> Any:
        """Sync execution path (default: delegates to :meth:`run`)."""
        return self.run(ctx, call_next)

    async def run_async(self, ctx: "Context", call_next: CALL_NEXT) -> Any:
        """Async execution path.

        Capabilities that need to ``await call_next(ctx)`` (cache, trace, metrics, log,
        circuit_breaker) or await on I/O (retry, timeout) MUST override this method.
        """
        return self.run(ctx, call_next)

    # -- helpers ---------------------------------------------------------------
    def backend(self, name: str) -> Any:
        """Resolve a backend by name from the service container, or None."""
        if self.services is None:
            return None
        return self.services.get(name)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r} v{self.version}>"
