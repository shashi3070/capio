"""Timeout capability (RFC-018 §3)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ..config import parse_duration
from ..context import Context
from ..events import Event
from ..exceptions import CapioTimeoutError, ConfigurationError
from ..sdk.capability import CALL_NEXT, Capability


class Timeout(Capability):
    name = "timeout"
    version = "1.0.0"
    description = "Bounds execution time (RFC-018 §3)."
    priority = 650
    degradation = "propagate"

    schema = {
        "seconds": {"type": "any", "default": "2s"},
        "hard": {"type": "bool", "default": False},
        "raise_on": {"type": "bool", "default": True},
        "return_on": {"type": "any", "default": None},
        "enable": {"type": "any", "default": None},
    }

    def configure(self, config):
        super().configure(config)
        if self.cfg.return_on is not None and self.cfg.raise_on:
            raise ConfigurationError(
                "timeout: 'return_on' and 'raise_on=True' are mutually exclusive (RFC-018 §3.2)"
            )

    # -- sync path ---------------------------------------------------------------
    # Cooperative (hard=False): ctx.deadline is set; the wrapped callable may check it.
    # Expiry is observed when the callable returns — documented RFC-018 §3.3 limitation.
    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        seconds = parse_duration(self.cfg.seconds)
        ctx.deadline = time.monotonic() + seconds
        if self.cfg.hard:
            ctx.emit(
                Event(
                    "timeout.warning",
                    {"reason": "hard_timeout_unavailable_on_sync_path", "fallback": "cooperative"},
                )
            )
        result = call_next(ctx)
        if time.monotonic() > ctx.deadline:
            return self._on_expiry(ctx, seconds)
        return result

    # -- async path ----------------------------------------------------------------
    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        seconds = parse_duration(self.cfg.seconds)
        ctx.deadline = time.monotonic() + seconds
        try:
            return await asyncio.wait_for(call_next(ctx), timeout=seconds)
        except (asyncio.TimeoutError, TimeoutError):
            ctx.cancel.cancel()
            return self._on_expiry(ctx, seconds)

    def _on_expiry(self, ctx: Context, seconds: float) -> Any:
        ctx.emit(Event("timeout.fired", {"seconds": seconds}))
        if self.cfg.raise_on:
            raise CapioTimeoutError(f"invocation exceeded {seconds}s timeout", seconds=seconds)
        ctx.emit(Event("timeout.handled", {"seconds": seconds}))
        return self.cfg.return_on
