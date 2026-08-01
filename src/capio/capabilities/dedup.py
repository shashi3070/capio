"""Dedup capability (RFC-022 §6): coalesce identical in-flight/completed calls."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Dict

from ..config import parse_duration
from ..context import Context
from ..events import Event
from ..sdk.capability import CALL_NEXT, Capability

_MISSING = object()


class Dedup(Capability):
    name = "dedup"
    version = "1.0.0"
    description = "Returns one result for identical calls within a TTL (RFC-022 §6)."
    priority = 670
    degradation = "bypass"

    schema = {
        "key": {"type": "any", "default": None},
        "backend": {"type": "str", "default": "cache.memory"},
        "ttl": {"type": "any", "default": None},
        "wait_timeout": {"type": "any", "default": "10s"},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.RLock()
        self._sync_inflight: Dict[str, threading.Event] = {}
        self._async_inflight: Dict[str, asyncio.Event] = {}

    def _key(self, ctx: Context) -> str:
        key_cfg = self.cfg.key
        if key_cfg is None:
            return f"{ctx.fn_module}.{ctx.fn_name}:{ctx.args!r}:{sorted(ctx.kwargs.items())!r}"
        if callable(key_cfg):
            return str(key_cfg(ctx))
        return str(key_cfg)

    def _wait_timeout(self) -> float | None:
        if self.cfg.wait_timeout is None:
            return None
        return parse_duration(self.cfg.wait_timeout)

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        key = self._key(ctx)
        backend = self.backend(self.cfg.backend)
        if backend is not None:
            value = backend.get(key, _MISSING)
            if value is not _MISSING:
                ctx.emit(Event("dedup.hit", {"key": key}))
                return value
        with self._lock:
            event = self._sync_inflight.get(key)
            if event is not None:
                ctx.emit(Event("dedup.waiting", {"key": key}))
                event.wait(timeout=self._wait_timeout())
                if backend is not None:
                    value = backend.get(key, _MISSING)
                    if value is not _MISSING:
                        ctx.emit(Event("dedup.hit", {"key": key}))
                        return value
            event = threading.Event()
            self._sync_inflight[key] = event
        try:
            result = call_next(ctx)
        except BaseException:
            with self._lock:
                self._sync_inflight.pop(key, None)
                event.set()
            raise
        if backend is not None:
            backend.set(key, result, ttl=parse_duration(self.cfg.ttl) if self.cfg.ttl else None)
        with self._lock:
            self._sync_inflight.pop(key, None)
            event.set()
        ctx.emit(Event("dedup.miss", {"key": key}))
        return result

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        key = self._key(ctx)
        backend = self.backend(self.cfg.backend)
        if backend is not None:
            value = backend.get(key, _MISSING)
            if value is not _MISSING:
                ctx.emit(Event("dedup.hit", {"key": key}))
                return value
        with self._lock:
            event = self._async_inflight.get(key)
            if event is not None:
                ctx.emit(Event("dedup.waiting", {"key": key}))
                try:
                    await asyncio.wait_for(event.wait(), self._wait_timeout())
                except asyncio.TimeoutError:
                    pass
                if backend is not None:
                    value = backend.get(key, _MISSING)
                    if value is not _MISSING:
                        ctx.emit(Event("dedup.hit", {"key": key}))
                        return value
            event = asyncio.Event()
            self._async_inflight[key] = event
        try:
            result = await call_next(ctx)
        except BaseException:
            with self._lock:
                self._async_inflight.pop(key, None)
                event.set()
            raise
        if backend is not None:
            backend.set(key, result, ttl=parse_duration(self.cfg.ttl) if self.cfg.ttl else None)
        with self._lock:
            self._async_inflight.pop(key, None)
            event.set()
        ctx.emit(Event("dedup.miss", {"key": key}))
        return result
