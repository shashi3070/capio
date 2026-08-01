"""The ``use`` facade (RFC-003): chained decorators, composite form, introspection."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from .context import current_context
from .exceptions import (
    ConfigurationError,
    ConflictingPipelineError,
    DuplicateCapabilityError,
    UnknownCapabilityError,
    UnsupportedExecutionKindError,
)
from .pipeline import detect_kind
from .registry import registry
from .runtime import CapioRuntime, __version__, default_runtime

Wrapped = Callable[..., Any]


@dataclass(frozen=True)
class CapabilityInfo:
    """Lightweight metadata for one applied capability (RFC-003 §5.3)."""

    name: str
    version: str
    priority: int
    options: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CapioMeta:
    """Pipeline metadata attached to every decorated callable (RFC-003 §5.3)."""

    version: str
    capabilities: Tuple[CapabilityInfo, ...]  # outermost-first
    mode: str = "chained"


def _merge_meta(existing: Optional[CapioMeta], spec: CapabilityInfo) -> CapioMeta:
    if existing is None:
        return CapioMeta(version=__version__, capabilities=(spec,), mode="chained")
    for capability in existing.capabilities:
        if capability.name == spec.name:
            raise DuplicateCapabilityError(
                f"capability {spec.name!r} applied more than once (RFC-003 §3.2)"
            )
    return CapioMeta(
        version=existing.version,
        capabilities=(spec,) + existing.capabilities,
        mode=existing.mode,
    )


def _spec_for(name: str, options: Mapping[str, Any]) -> CapabilityInfo:
    capability_cls = registry.get(name)
    return CapabilityInfo(
        name=name,
        version=capability_cls.version,
        priority=capability_cls.priority,
        options=dict(options),
    )


def unwrap(fn: Any) -> Any:
    """Return the innermost original callable, following ``__wrapped__``.

    Stops at capio context-injection wrappers (``__capio_leaf__``) so they stay part
    of the executed pipeline (RFC-003 §5.2/5.4).
    """
    seen = set()
    while (
        fn is not None
        and hasattr(fn, "__wrapped__")
        and not getattr(fn, "__capio_leaf__", False)
    ):
        if id(fn) in seen:
            break
        seen.add(id(fn))
        fn = getattr(fn, "__wrapped__")
    return fn


class Use:
    """The public decorator facade.

    - ``@use.retry(...)`` chained form (primary)
    - ``@use(retry={...}, cache=True)`` composite form
    - ``use.context()`` injects the current Context into the wrapped callable
    """

    def __init__(self, runtime: Optional[CapioRuntime] = None) -> None:
        self._runtime = runtime
        self._factories: Dict[str, Callable[..., Any]] = {}

    # -- runtime binding -----------------------------------------------------------
    def _get_runtime(self) -> CapioRuntime:
        return self._runtime if self._runtime is not None else default_runtime()

    # -- chained form: use.<capability>(...) -----------------------------------------
    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name.startswith("_"):
            raise AttributeError(name)
        if not registry.contains(name):
            raise UnknownCapabilityError(
                f"unknown capability {name!r}; registered: {', '.join(registry.names())}"
            )

        def factory(**options: Any) -> Callable[[Wrapped], Wrapped]:
            return self._decorate_with(name, options)

        factory.__name__ = f"use.{name}"
        factory.__qualname__ = f"use.{name}"
        factory.__doc__ = f"Decorate a callable with the {name!r} capability (RFC-003)."
        self._factories[name] = factory
        return factory

    def _decorate_with(self, name: str, options: Mapping[str, Any]) -> Callable[[Wrapped], Wrapped]:
        spec = _spec_for(name, options)

        def decorator(fn: Wrapped) -> Wrapped:
            if inspect.isclass(fn):
                return self._decorate_class(fn, spec)
            existing = getattr(fn, "__capio__", None)
            meta = _merge_meta(existing, spec)
            return self._wrap(fn, meta)

        return decorator

    # -- composite form: use(...) ----------------------------------------------------
    def __call__(self, *names: str, **options: Any) -> Callable[[Wrapped], Wrapped]:
        specs: Dict[str, CapabilityInfo] = {}
        for name in names:
            if name in specs:
                raise ConfigurationError(f"capability {name!r} specified more than once")
            specs[name] = _spec_for(name, {})
        for name, opts in options.items():
            if opts is False or opts is None:
                specs.pop(name, None)
                continue
            if name in specs:
                if opts is not True:
                    raise ConfigurationError(f"capability {name!r} specified inconsistently")
                continue
            if opts is True:
                opts = {}
            elif not isinstance(opts, Mapping):
                raise ConfigurationError(f"options for {name!r} must be a mapping or True")
            specs[name] = _spec_for(name, opts)
        ordered = tuple(sorted(specs.values(), key=lambda s: s.priority, reverse=True))
        meta = CapioMeta(version=__version__, capabilities=ordered, mode="composite")

        def decorator(fn: Wrapped) -> Wrapped:
            existing = getattr(fn, "__capio__", None)
            if existing is not None:
                raise ConflictingPipelineError(
                    "composite use(...) cannot be applied to an already-decorated function "
                    "(RFC-003 §5.1)"
                )
            return self._wrap(fn, meta)

        return decorator

    # -- context injection (RFC-003 §5.4) ----------------------------------------------
    def context(self, param_name: str = "ctx") -> Callable[[Wrapped], Wrapped]:
        def decorator(fn: Wrapped) -> Wrapped:
            if inspect.iscoroutinefunction(fn):

                @wraps(fn)
                async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
                    kwargs[param_name] = current_context()
                    return await fn(*args, **kwargs)

                _async_wrapper.__capio_leaf__ = True  # type: ignore[attr-defined]
                return _async_wrapper

            @wraps(fn)
            def _sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                kwargs[param_name] = current_context()
                return fn(*args, **kwargs)

            _sync_wrapper.__capio_leaf__ = True  # type: ignore[attr-defined]
            return _sync_wrapper

        return decorator

    # -- wrapping machinery --------------------------------------------------------------
    def _wrap(self, fn: Wrapped, meta: CapioMeta) -> Wrapped:
        original = unwrap(fn)
        runtime = self._get_runtime()
        self._check_kind(original, meta)
        if inspect.iscoroutinefunction(fn):

            @wraps(fn)
            async def _capio_async_wrapper(*args: Any, **kwargs: Any) -> Any:
                return await runtime.call(original, meta, args, kwargs)

            _capio_async_wrapper.__capio__ = meta  # type: ignore[attr-defined]
            _capio_async_wrapper.__capio_runtime__ = runtime  # type: ignore[attr-defined]
            return _capio_async_wrapper

        @wraps(fn)
        def _capio_sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            return runtime.call(original, meta, args, kwargs)

        _capio_sync_wrapper.__capio__ = meta  # type: ignore[attr-defined]
        _capio_sync_wrapper.__capio_runtime__ = runtime  # type: ignore[attr-defined]
        return _capio_sync_wrapper

    @staticmethod
    def _check_kind(fn: Wrapped, meta: CapioMeta) -> None:
        kind = detect_kind(fn)
        for info in meta.capabilities:
            capability_cls = registry.get(info.name)
            if kind not in capability_cls.supports:
                raise UnsupportedExecutionKindError(
                    f"capability {info.name!r} does not support execution kind {kind!r} "
                    "(RFC-003 §3.3)"
                )

    # -- class decoration (RFC-012 §5.1) ---------------------------------------------------
    def _decorate_class(self, fn: type, spec: CapabilityInfo) -> type:
        options = spec.options
        methods = options.get("methods")
        exclude = set(options.get("exclude") or [])
        include_private = bool(options.get("include_private", False))
        include_dunders = bool(options.get("include_dunders", False))
        for name, attr in list(vars(fn).items()):
            if name in exclude:
                continue
            if getattr(attr, "__capio_skip__", False):
                continue
            if methods is not None and name not in methods:
                continue
            if methods is None:
                if name.startswith("__") and not include_dunders:
                    continue
                if name.startswith("_") and not include_private:
                    continue
            decorated: Any = None
            if isinstance(attr, (staticmethod, classmethod)):
                inner = attr.__func__
                wrapped = self._decorate_with(spec.name, spec.options)(inner)
                if isinstance(attr, staticmethod):
                    decorated = staticmethod(wrapped)
                else:
                    decorated = classmethod(wrapped)
            elif inspect.isfunction(attr):
                decorated = self._decorate_with(spec.name, spec.options)(attr)
            if decorated is not None:
                setattr(fn, name, decorated)
        existing = getattr(fn, "__capio__", None)
        meta = _merge_meta(existing, spec) if existing is not None else CapioMeta(
            version=__version__, capabilities=(spec,), mode="chained"
        )
        fn.__capio__ = meta  # type: ignore[attr-defined]
        return fn


use = Use()


def with_capabilities(fn: Wrapped, **options: Any) -> Wrapped:
    """Apply a composite set of capabilities at runtime (RFC-003 §8.2)."""
    return use(**options)(fn)


def pipeline(fn: Any) -> Any:
    """Return the built pipeline for a decorated callable (RFC-003 §5.2)."""
    meta = getattr(fn, "__capio__", None)
    if meta is None:
        raise ConfigurationError("function is not decorated with capio")
    runtime = getattr(fn, "__capio_runtime__", None) or default_runtime()
    return runtime.get_pipeline(unwrap(fn), meta)
