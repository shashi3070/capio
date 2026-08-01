"""Cache capability (RFC-016): TTL, key building, stampede protection."""

from __future__ import annotations

import asyncio
import hashlib
import random
import threading
from typing import Any, Callable, Dict, Optional

from ..backends.memory_cache import _MISSING, MemoryCacheBackend
from ..config import parse_duration
from ..context import Context
from ..events import Event
from ..exceptions import BackendUnavailableError, CacheKeyError
from ..sdk.capability import CALL_NEXT, Capability

# -- canonical serialization for key building (RFC-016 §3.1) --------------------


def _canonicalize(value: Any) -> str:
    if value is None:
        return "n"
    if isinstance(value, bool):
        return "b" + str(int(value))
    if isinstance(value, (int, float)):
        return "i" + repr(value)
    if isinstance(value, str):
        return "s" + value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "y" + bytes(value).hex()
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonicalize(v) for v in value) + "]"
    if isinstance(value, dict):
        items = ",".join(
            _canonicalize(k) + ":" + _canonicalize(v) for k, v in sorted(value.items(), key=repr)
        )
        return "{" + items + "}"
    if isinstance(value, (set, frozenset)):
        return "~{" + ",".join(sorted(_canonicalize(v) for v in value)) + "}"
    return "o" + type(value).__module__ + "." + type(value).__qualname__ + "@" + str(id(value))


def _stable_hash(args: tuple, kwargs: Dict[str, Any]) -> str:
    parts = [_canonicalize(a) for a in args]
    for key in sorted(kwargs):
        parts.append("k" + key + "=" + _canonicalize(kwargs[key]))
    return hashlib.sha256("|".join(parts).encode("utf-8", "surrogatepass")).hexdigest()


KeyBuilder = Callable[[Context, tuple, Dict[str, Any]], str]
key_builders: Dict[str, KeyBuilder] = {}


def register_key_builder(name: str, builder: Optional[KeyBuilder] = None) -> KeyBuilder:
    """Register a named cache-key builder (RFC-016 §3.2).

    Usable as ``register_key_builder("name", fn)`` or ``@register_key_builder("name")``.
    """

    def _register(fn: KeyBuilder) -> KeyBuilder:
        key_builders[name] = fn
        return fn

    if builder is not None:
        key_builders[name] = builder
        return builder
    return _register


@register_key_builder("auto")
def _auto_key(ctx: Context, args: tuple, kwargs: Dict[str, Any]) -> str:
    return _stable_hash(args, kwargs)


# -- stored-value wrappers --------------------------------------------------------


class _StoredExc:
    __slots__ = ("exc",)

    def __init__(self, exc: BaseException):
        self.exc = exc


class _Ok:
    __slots__ = ("value",)

    def __init__(self, value: Any):
        self.value = value


class _Err:
    __slots__ = ("exc",)

    def __init__(self, exc: BaseException):
        self.exc = exc


def _unwrap(stored: Any) -> Any:
    if isinstance(stored, _StoredExc):
        raise stored.exc
    if isinstance(stored, _Ok):
        return stored.value
    if isinstance(stored, _Err):
        raise stored.exc
    return stored


# -- singleflight (RFC-016 §6) -------------------------------------------------------


class _SingleFlight:
    """In-process per-key singleflight for the sync path."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._inflight: Dict[str, Any] = {}
        self._events: Dict[str, threading.Event] = {}

    def acquire(self, key: str) -> Optional[threading.Event]:
        with self._lock:
            if key not in self._inflight:
                self._inflight[key] = None
                self._events[key] = threading.Event()
                return None
            if self._inflight[key] is not None:
                self._inflight.pop(key, None)
                self._events.pop(key, None)
                self._inflight[key] = None
                self._events[key] = threading.Event()
                return None
            return self._events[key]

    def set_result(self, key: str, result: Any) -> None:
        with self._lock:
            self._inflight[key] = result
        evt = self._events.get(key)
        if evt is not None:
            evt.set()

    def wait_result(self, key: str, evt: threading.Event) -> Any:
        evt.wait()
        with self._lock:
            return self._inflight.get(key, _MISSING)

    def release(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)
            self._inflight.pop(key, None)


# -- capability ----------------------------------------------------------------------


class Cache(Capability):
    name = "cache"
    version = "1.0.0"
    description = "Caches results with TTL and stampede protection (RFC-016)."
    priority = 750
    requires_backends = ("cache",)
    degradation = "bypass"

    schema = {
        "ttl": {"type": "any", "default": None},
        "key": {"type": "any", "default": "auto"},
        "key_prefix": {"type": "any", "default": None},
        "namespace": {"type": "any", "default": None},
        "key_scope": {"type": "str", "default": "class", "enum": ["class", "instance"]},
        "backend": {"type": "str", "default": "cache.memory"},
        "tags": {"type": "any", "default": None},
        "cache_when": {"type": "any", "default": None},
        "cache_on_error": {"type": "bool", "default": False},
        "stampede": {
            "type": "str",
            "default": "probabilistic",
            "enum": ["none", "singleflight", "probabilistic"],
        },
        "maxsize": {"type": "int", "default": None, "min": 1},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self._sf = _SingleFlight()
        self._async_inflight: Dict[str, asyncio.Future] = {}

    # -- helpers -------------------------------------------------------------------
    def _backend_or_degrade(self, ctx: Context) -> Optional[MemoryCacheBackend]:
        backend = self.backend(self.cfg.backend)
        if backend is None:
            ctx.emit(
                Event("cache.failed", {"reason": "backend_missing", "backend": self.cfg.backend})
            )
            if ctx.strict:
                raise BackendUnavailableError(f"cache backend {self.cfg.backend!r} not bound")
            return None
        return backend

    def _build_key(self, ctx: Context) -> str:
        key_spec = self.cfg.key
        if callable(key_spec):
            raw = key_spec(ctx, ctx.args, ctx.kwargs)
        elif isinstance(key_spec, str) and key_spec not in ("auto", "default"):
            builder = key_builders.get(key_spec)
            if builder is None:
                raise CacheKeyError(f"unknown cache key builder {key_spec!r}")
            raw = builder(ctx, ctx.args, ctx.kwargs)
        else:
            raw = _stable_hash(ctx.args, ctx.kwargs)
        if not isinstance(raw, str):
            raise CacheKeyError("cache key builder must return a str")
        namespace = self.cfg.namespace
        if callable(namespace):
            namespace = namespace(ctx)
        namespace = namespace or self.cfg.key_prefix or f"{ctx.fn_module}.{ctx.fn_name}"
        instance_part = ""
        if self.cfg.key_scope == "instance" and ctx.self_or_cls is not None:
            instance_part = "@" + str(id(ctx.self_or_cls))
        return f"{namespace}{instance_part}:{raw}"

    def _effective_ttl(self) -> Optional[float]:
        if self.cfg.ttl is None:
            return None
        ttl = parse_duration(self.cfg.ttl)
        if self.cfg.stampede == "probabilistic":
            ttl *= random.uniform(0.8, 1.0)
        return ttl

    def _get(self, ctx: Context, backend: MemoryCacheBackend, key: str) -> Any:
        try:
            return backend.get(key)
        except Exception as exc:  # noqa: BLE001 - fail-safe degrade
            ctx.emit(
                Event("cache.failed", {"reason": "backend_get", "error": repr(exc), "key": key})
            )
            if ctx.strict:
                raise
            return _MISSING

    def _store(self, ctx: Context, backend: MemoryCacheBackend, key: str, value: Any) -> None:
        try:
            backend.set(key, value, ttl=self._effective_ttl())
            ctx.emit(Event("cache.stored", {"key": key}))
        except Exception as exc:  # noqa: BLE001 - fail-safe degrade
            ctx.emit(
                Event("cache.failed", {"reason": "backend_set", "error": repr(exc), "key": key})
            )
            if ctx.strict:
                raise

    def _should_store(self, ctx: Context, result: Any) -> bool:
        cache_when = self.cfg.cache_when
        if cache_when is None:
            return True
        return bool(cache_when(ctx, result))

    # -- sync path -------------------------------------------------------------------
    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        backend = self._backend_or_degrade(ctx)
        if backend is None:
            return call_next(ctx)
        try:
            key = self._build_key(ctx)
        except CacheKeyError as exc:
            ctx.emit(Event("cache.failed", {"reason": "key", "error": repr(exc)}))
            return call_next(ctx)
        if self.cfg.stampede == "singleflight":
            return self._run_singleflight(ctx, call_next, backend, key)
        hit = self._get(ctx, backend, key)
        if hit is not _MISSING:
            ctx.emit(Event("cache.hit", {"key": key}))
            return _unwrap(hit)
        ctx.emit(Event("cache.miss", {"key": key}))
        try:
            result = call_next(ctx)
        except BaseException as exc:  # noqa: BLE001
            if self.cfg.cache_on_error:
                self._store(ctx, backend, key, _StoredExc(exc))
            raise
        if self._should_store(ctx, result):
            self._store(ctx, backend, key, result)
        return result

    def _run_singleflight(
        self, ctx: Context, call_next: CALL_NEXT, backend: MemoryCacheBackend, key: str
    ) -> Any:
        waiter = self._sf.acquire(key)
        if waiter is not None:
            settled = self._sf.wait_result(key, waiter)
            if settled is not _MISSING:
                return _unwrap(settled)
        try:
            hit = self._get(ctx, backend, key)
            if hit is not _MISSING:
                ctx.emit(Event("cache.hit", {"key": key}))
                return _unwrap(hit)
            ctx.emit(Event("cache.miss", {"key": key}))
            result = call_next(ctx)
            self._sf.set_result(key, _Ok(result))
            if self._should_store(ctx, result):
                self._store(ctx, backend, key, result)
            return result
        except BaseException as exc:  # noqa: BLE001
            self._sf.set_result(key, _Err(exc))
            raise
        finally:
            self._sf.release(key)

    # -- async path --------------------------------------------------------------------
    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        backend = self._backend_or_degrade(ctx)
        if backend is None:
            return await call_next(ctx)
        try:
            key = self._build_key(ctx)
        except CacheKeyError as exc:
            ctx.emit(Event("cache.failed", {"reason": "key", "error": repr(exc)}))
            return await call_next(ctx)
        if self.cfg.stampede == "singleflight":
            return await self._run_singleflight_async(ctx, call_next, backend, key)
        hit = self._get(ctx, backend, key)
        if hit is not _MISSING:
            ctx.emit(Event("cache.hit", {"key": key}))
            return _unwrap(hit)
        ctx.emit(Event("cache.miss", {"key": key}))
        try:
            result = await call_next(ctx)
        except BaseException as exc:  # noqa: BLE001
            if self.cfg.cache_on_error:
                self._store(ctx, backend, key, _StoredExc(exc))
            raise
        if self._should_store(ctx, result):
            self._store(ctx, backend, key, result)
        return result

    async def _run_singleflight_async(
        self, ctx: Context, call_next: CALL_NEXT, backend: MemoryCacheBackend, key: str
    ) -> Any:
        loop = asyncio.get_running_loop()
        existing = self._async_inflight.get(key)
        if existing is not None and not existing.done():
            return _unwrap(await existing)
        fut = loop.create_future()
        self._async_inflight[key] = fut
        try:
            hit = self._get(ctx, backend, key)
            if hit is not _MISSING:
                ctx.emit(Event("cache.hit", {"key": key}))
                fut.set_result(_Ok(_unwrap(hit)))
                return _unwrap(hit)
            ctx.emit(Event("cache.miss", {"key": key}))
            result = await call_next(ctx)
            if self._should_store(ctx, result):
                self._store(ctx, backend, key, result)
            fut.set_result(_Ok(result))
            return result
        except BaseException as exc:  # noqa: BLE001
            if not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            self._async_inflight.pop(key, None)
