"""Execution pipeline (RFC-004 §4, RFC-005 §3): the ordered, memoized step list."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from .config import FrozenConfig
from .context import Context
from .engine.async_engine import execute_async
from .engine.sync import execute_sync
from .exceptions import (
    CapabilityRuntimeError,
    DuplicateCapabilityError,
    UnsupportedExecutionKindError,
)

if TYPE_CHECKING:
    from .runtime import CapioRuntime


def detect_kind(fn: Any) -> str:
    """Classify a callable into one of the four execution kinds (RFC-012 §3)."""
    if inspect.isasyncgenfunction(fn):
        return "async_gen"
    if inspect.iscoroutinefunction(fn):
        return "async"
    if inspect.isgeneratorfunction(fn):
        return "sync_gen"
    return "sync"


class ExecutionPipeline:
    """A compiled, immutable pipeline of capability steps around a callable.

    ``steps`` is ordered outermost-first. The pipeline holds no per-invocation state and is
    safe to share across threads/tasks (RFC-005 §8).
    """

    def __init__(
        self,
        *,
        fn: Any,
        kind: str,
        steps: list,
        config: FrozenConfig,
        runtime: "CapioRuntime",
        meta: Any,
        cls: Optional[type] = None,
    ) -> None:
        self.fn = fn
        self.kind = kind
        self.steps = steps
        self.config = config
        self.runtime = runtime
        self.meta = meta
        self.cls = cls
        try:
            params = inspect.signature(fn).parameters
            first = next(iter(params)) if params else ""
        except (TypeError, ValueError):
            first = ""
        self._self_param = first if first in ("self", "cls") else None

    @property
    def capability_names(self) -> Tuple[str, ...]:
        return tuple(step.name for step in self.steps)

    def build_context(self, args: tuple, kwargs: Dict[str, Any]) -> Context:
        self_or_cls = None
        if self._self_param is not None and args:
            self_or_cls = args[0]
        return Context(
            fn=self.fn,
            args=args,
            kwargs=kwargs,
            cls=self.cls,
            self_or_cls=self_or_cls,
            config=self.config,
            profile=self.runtime.profile,
            env=self.runtime.env,
            strict=self.runtime.strict,
            event_bus=self.runtime.event_bus,
        )

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        """Dispatch to the sync or async engine based on the callable kind."""
        if self.kind in ("async", "async_gen"):
            return execute_async(self, *args, **kwargs)
        return execute_sync(self, *args, **kwargs)

    def __repr__(self) -> str:
        return f"<ExecutionPipeline kind={self.kind} steps={self.capability_names}>"


def build_pipeline(runtime: "CapioRuntime", fn: Any, meta: Any) -> ExecutionPipeline:
    """Compile a pipeline from an unwrapped callable and its CapioMeta (RFC-005 §3)."""
    kind = detect_kind(fn)
    instances = []
    seen = set()
    for info in meta.capabilities:  # outermost-first
        if info.name in seen:
            raise DuplicateCapabilityError(
                f"capability {info.name!r} applied more than once (RFC-003 §3.2)"
            )
        seen.add(info.name)
        capability_cls = runtime.registry.get(info.name)
        if kind not in capability_cls.supports:
            raise UnsupportedExecutionKindError(
                f"capability {info.name!r} does not support execution kind {kind!r} (RFC-003 §3.3)"
            )
        instance = capability_cls()
        instance.instance_id = f"{info.name}@{id(instance):x}"
        options = runtime.resolve_options(capability_cls.schema, info.options)
        instance.configure(FrozenConfig(options))
        instance.initialize(runtime.services)
        try:
            instance.start()
        except Exception as exc:  # noqa: BLE001
            raise CapabilityRuntimeError(
                f"capability {info.name!r} failed to start", capability=info.name
            ) from exc
        instances.append(instance)
    pipeline = ExecutionPipeline(
        fn=fn,
        kind=kind,
        steps=instances,
        config=runtime.config,
        runtime=runtime,
        meta=meta,
    )
    return pipeline
