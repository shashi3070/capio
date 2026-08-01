"""CapioRuntime (RFC-004 §5.1): owns config, services, backends, and memoized pipelines."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Dict, Optional

from .backends.console_trace import ConsoleTraceBackend
from .backends.memory_cache import MemoryCacheBackend
from .backends.null_metrics import NullMetricsBackend
from .backends.stdio_log import StdioLogBackend
from .config import FrozenConfig, env_from_os, merge_config, validate_config
from .di import ServiceContainer
from .events import EventBus
from .registry import registry

if TYPE_CHECKING:
    from .pipeline import ExecutionPipeline

__version__ = "0.1.1"


class CapioRuntime:
    """A self-contained Capio runtime instance (RFC-004 §5.1).

    Each runtime owns its config, service container, event bus, and pipeline cache. The
    default runtime backs the module-level ``use`` facade.
    """

    def __init__(self, name: str = "default", config: Optional[Dict[str, Any]] = None) -> None:
        self.name = name
        base: Dict[str, Any] = {"env": "dev", "profile": "default", "strict": False}
        self.config = FrozenConfig(merge_config(merge_config(base, config or {}), env_from_os()))
        self.services = ServiceContainer()
        self.event_bus = EventBus()
        self.registry = registry

        # register built-in backends (RFC-015 §4)
        self.services.bind("cache.memory", MemoryCacheBackend())
        self.services.bind("trace.console", ConsoleTraceBackend())
        self.services.bind("metrics.null", NullMetricsBackend())
        self.services.bind("log.stdio", StdioLogBackend())

        # ensure base capabilities are registered (idempotent)
        import capio.capabilities  # noqa: F401

        self._pipelines: Dict[tuple, "ExecutionPipeline"] = {}
        self._lock = threading.RLock()

    # -- config surface ---------------------------------------------------------
    @property
    def env(self) -> str:
        return str(self.config.env)

    @property
    def profile(self) -> str:
        return str(self.config.profile)

    @property
    def strict(self) -> bool:
        return bool(self.config.strict)

    def resolve_options(self, schema: Dict[str, Any], options: Dict[str, Any]) -> Dict[str, Any]:
        """Validate per-capability options against the capability schema (RFC-009)."""
        return validate_config(schema, options)

    # -- pipeline memoization (RFC-005 §8) -----------------------------------------
    def get_pipeline(self, fn: Any, meta: Any) -> "ExecutionPipeline":
        from .pipeline import build_pipeline

        key = (id(fn), id(meta))
        pipeline = self._pipelines.get(key)
        if pipeline is None:
            with self._lock:
                pipeline = self._pipelines.get(key)
                if pipeline is None:
                    pipeline = build_pipeline(self, fn, meta)
                    self._pipelines[key] = pipeline
        return pipeline

    def call(self, fn: Any, meta: Any, args: tuple, kwargs: Dict[str, Any]) -> Any:
        """Execute a decorated callable (dispatches sync/async by kind)."""
        return self.get_pipeline(fn, meta).execute(*args, **kwargs)

    # -- lifecycle (RFC-011 §7) ---------------------------------------------------
    def start(self) -> None:
        for pipeline in list(self._pipelines.values()):
            for step in pipeline.steps:
                step.start()

    def stop(self) -> None:
        for pipeline in reversed(list(self._pipelines.values())):
            for step in reversed(pipeline.steps):
                step.stop()

    def shutdown(self) -> None:
        self.stop()
        self._pipelines.clear()

    # -- helpers ------------------------------------------------------------------
    def bind_backend(self, name: str, backend: Any) -> None:
        self.services.bind(name, backend)

    def __repr__(self) -> str:
        return f"<CapioRuntime name={self.name!r} env={self.env!r} strict={self.strict}>"


_default_runtime: Optional[CapioRuntime] = None
_runtime_lock = threading.Lock()


def default_runtime() -> CapioRuntime:
    """Return the process-wide default runtime, creating it lazily (RFC-004 §6.3)."""
    global _default_runtime
    if _default_runtime is None:
        with _runtime_lock:
            if _default_runtime is None:
                _default_runtime = CapioRuntime("default")
    return _default_runtime
