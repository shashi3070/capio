"""Debounce capability (RFC-018 §6): coalesce rapid calls into a trailing execution."""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..config import parse_duration
from ..context import Context
from ..events import Event
from ..sdk.capability import CALL_NEXT, Capability


@dataclass
class _Pending:
    key: str
    window: float
    window_until: float
    count: int = 0
    ran_leading: bool = False
    ctx: Optional[Context] = None
    call_next: Optional[CALL_NEXT] = None
    timer: Optional[threading.Timer] = None
    handle: Optional[Any] = None
    result: Any = None


class Debounce(Capability):
    name = "debounce"
    version = "1.0.0"
    description = "Coalesces rapid calls within a window (RFC-018 §6)."
    priority = 830
    degradation = "propagate"

    schema = {
        "window": {"type": "any", "default": "200ms"},
        "leading": {"type": "bool", "default": False},
        "trailing": {"type": "bool", "default": True},
        "key": {"type": "any", "default": None},
        "drop_value": {"type": "any", "default": None},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.RLock()
        self._pending: Dict[str, _Pending] = {}

    def _key(self, ctx: Context) -> str:
        key_cfg = self.cfg.key
        if key_cfg is None:
            return f"{ctx.fn_module}.{ctx.fn_name}"
        if callable(key_cfg):
            return str(key_cfg(ctx))
        return str(key_cfg)

    # -- sync ----------------------------------------------------------------
    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        window = parse_duration(self.cfg.window)
        key = self._key(ctx)
        now = time.monotonic()
        with self._lock:
            pending = self._pending.get(key)
            if pending is None or now >= pending.window_until:
                if pending is not None:
                    self._pending.pop(key, None)
                pending = _Pending(key=key, window=window, window_until=now + window)
                self._pending[key] = pending
                if self.cfg.leading or not self.cfg.trailing:
                    pending.ran_leading = True
                    ctx.emit(Event("debounce.executed", {"key": key, "leading": True}))
                    if self.cfg.trailing:
                        pending.timer = threading.Timer(window, self._fire, args=(key,))
                        pending.timer.daemon = True
                        pending.timer.start()
                    return call_next(ctx)
                pending.ctx = ctx
                pending.call_next = call_next
                pending.count = 1
                pending.timer = threading.Timer(window, self._fire, args=(key,))
                pending.timer.daemon = True
                pending.timer.start()
                ctx.emit(Event("debounce.scheduled", {"key": key}))
                return self.cfg.drop_value
            # within the quiet window: coalesce
            pending.count += 1
            if self.cfg.trailing:
                pending.ctx = ctx
                pending.call_next = call_next
                if pending.timer is not None:
                    pending.timer.cancel()
                pending.timer = threading.Timer(window, self._fire, args=(key,))
                pending.timer.daemon = True
                pending.timer.start()
            ctx.emit(Event("debounce.dropped", {"key": key, "count": pending.count}))
            return self.cfg.drop_value

    def _fire(self, key: str) -> None:
        with self._lock:
            pending = self._pending.pop(key, None)
        if pending is None or pending.ctx is None or pending.call_next is None:
            return
        if pending.ran_leading and pending.count == 0:
            return
        try:
            pending.result = pending.call_next(pending.ctx)
            pending.ctx.emit(Event("debounce.executed", {"key": key, "coalesced": pending.count}))
        except BaseException as exc:  # noqa: BLE001 - never crash the timer thread
            pending.ctx.emit(Event("debounce.error", {"key": key, "error": repr(exc)}))

    # -- async ---------------------------------------------------------------
    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        window = parse_duration(self.cfg.window)
        key = self._key(ctx)
        loop = asyncio.get_running_loop()
        now = time.monotonic()
        with self._lock:
            pending = self._pending.get(key)
            if pending is None or now >= pending.window_until:
                if pending is not None:
                    self._pending.pop(key, None)
                pending = _Pending(key=key, window=window, window_until=now + window)
                self._pending[key] = pending
                if self.cfg.leading or not self.cfg.trailing:
                    pending.ran_leading = True
                    ctx.emit(Event("debounce.executed", {"key": key, "leading": True}))
                    if self.cfg.trailing:
                        pending.handle = loop.call_later(window, self._schedule_fire, key)
                    return await call_next(ctx)
                pending.ctx = ctx
                pending.call_next = call_next
                pending.count = 1
                pending.handle = loop.call_later(window, self._schedule_fire, key)
                ctx.emit(Event("debounce.scheduled", {"key": key}))
                return self.cfg.drop_value
            pending.count += 1
            if self.cfg.trailing:
                pending.ctx = ctx
                pending.call_next = call_next
                if pending.handle is not None:
                    pending.handle.cancel()
                pending.handle = loop.call_later(window, self._schedule_fire, key)
            ctx.emit(Event("debounce.dropped", {"key": key, "count": pending.count}))
            return self.cfg.drop_value

    def _schedule_fire(self, key: str) -> None:
        with self._lock:
            pending = self._pending.get(key)
        if pending is None or pending.ctx is None or pending.call_next is None:
            return
        if pending.ran_leading and pending.count == 0:
            return
        asyncio.get_running_loop().create_task(self._fire_async(key))

    async def _fire_async(self, key: str) -> None:
        with self._lock:
            pending = self._pending.pop(key, None)
        if pending is None or pending.ctx is None or pending.call_next is None:
            return
        try:
            value = pending.call_next(pending.ctx)
            if asyncio.iscoroutine(value):
                value = await value
            pending.result = value
            pending.ctx.emit(Event("debounce.executed", {"key": key, "coalesced": pending.count}))
        except BaseException as exc:  # noqa: BLE001
            pending.ctx.emit(Event("debounce.error", {"key": key, "error": repr(exc)}))

    def stop(self) -> None:
        with self._lock:
            for pending in self._pending.values():
                if pending.timer is not None:
                    pending.timer.cancel()
                if pending.handle is not None:
                    pending.handle.cancel()
            self._pending.clear()
