"""Throttle capability (RFC-018 §5): bounded concurrency admission control."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from ..config import parse_duration
from ..context import Context
from ..events import Event
from ..exceptions import ConcurrencyLimitError
from ..sdk.capability import CALL_NEXT, Capability


class Throttle(Capability):
    name = "throttle"
    version = "1.0.0"
    description = "Bounds the number of in-flight calls (RFC-018 §5)."
    priority = 840
    degradation = "propagate"

    schema = {
        "limit": {"type": "int", "default": 100, "min": 1},
        "strategy": {"type": "str", "default": "block", "enum": ["block", "reject"]},
        "timeout": {"type": "any", "default": None},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self._sync_sem: threading.BoundedSemaphore | None = None
        self._async_sem: Any = None

    def _timeout(self) -> float | None:
        if self.cfg.timeout is None:
            return None
        return parse_duration(self.cfg.timeout)

    def _reject(self, ctx: Context) -> None:
        ctx.emit(Event("throttle.rejected", {"limit": self.cfg.limit}))
        raise ConcurrencyLimitError(message="concurrency limit exceeded", retry_after=0.1)

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        if self._sync_sem is None:
            self._sync_sem = threading.BoundedSemaphore(self.cfg.limit)
        blocking = self.cfg.strategy == "block"
        acquired = self._sync_sem.acquire(blocking=blocking, timeout=self._timeout())
        if not acquired:
            self._reject(ctx)
        try:
            return call_next(ctx)
        finally:
            self._sync_sem.release()

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        if self._async_sem is None:
            self._async_sem = asyncio.Semaphore(self.cfg.limit)
        if self.cfg.strategy == "reject":
            if self._async_sem.locked():
                self._reject(ctx)
            await self._async_sem.acquire()
        else:
            timeout = self._timeout()
            try:
                acquired = await asyncio.wait_for(self._async_sem.acquire(), timeout)
            except asyncio.TimeoutError:
                acquired = False
            if not acquired:
                self._reject(ctx)
        try:
            return await call_next(ctx)
        finally:
            self._async_sem.release()
