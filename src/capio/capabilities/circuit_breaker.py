"""Circuit breaker capability (RFC-018 §2): fail fast on unhealthy dependencies."""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Deque, Optional

from ..config import parse_duration
from ..context import Context
from ..events import Event
from ..exceptions import CapioCancelledBase, CapioTimeoutError, CircuitOpenError
from ..sdk.capability import CALL_NEXT, Capability


class CircuitBreaker(Capability):
    name = "circuit_breaker"
    version = "1.0.0"
    description = "Fails fast when a dependency is unhealthy (RFC-018 §2)."
    priority = 800
    degradation = "propagate"

    schema = {
        "failure_threshold": {"type": "int", "default": 5, "min": 1},
        "reset_timeout": {"type": "any", "default": "30s"},
        "success_threshold": {"type": "int", "default": 1, "min": 1},
        "window": {"type": "any", "default": "60s"},
        "only_on": {"type": "any", "default": None},
        "exclude": {"type": "any", "default": None},
        "record_timeouts": {"type": "bool", "default": True},
        "half_open_max": {"type": "int", "default": 1, "min": 1},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.RLock()
        self.state = "closed"
        self._failures: Deque[float] = deque()
        self._successes = 0
        self._opened_at: Optional[float] = None
        self._probes = 0

    # -- state transitions --------------------------------------------------------
    def _maybe_transition(self, ctx: Context) -> None:
        if self.state != "open" or self._opened_at is None:
            return
        if time.monotonic() - self._opened_at >= parse_duration(self.cfg.reset_timeout):
            self.state = "half_open"
            self._probes = 0
            self._successes = 0
            ctx.emit(Event("circuit.half_open", {"reset_timeout": self.cfg.reset_timeout}))

    def _counts(self, exc: BaseException) -> bool:
        exclude = self.cfg.exclude
        if exclude is not None:
            types = exclude if isinstance(exclude, tuple) else (exclude,)
            if isinstance(exc, types):
                return False
        if isinstance(exc, CapioTimeoutError):
            return bool(self.cfg.record_timeouts)
        if isinstance(exc, CapioCancelledBase):
            return False
        only_on = self.cfg.only_on
        if only_on is None:
            return isinstance(exc, Exception)
        types = only_on if isinstance(only_on, tuple) else (only_on,)
        return any(isinstance(exc, t) for t in types)

    def _record_failure(self, ctx: Context, exc: BaseException) -> None:
        if not self._counts(exc):
            return
        now = time.monotonic()
        with self._lock:
            self._failures.append(now)
            window = parse_duration(self.cfg.window)
            while self._failures and self._failures[0] <= now - window:
                self._failures.popleft()
            if self.state != "open" and len(self._failures) >= self.cfg.failure_threshold:
                self.state = "open"
                self._opened_at = now
                self._successes = 0
                ctx.emit(Event("circuit.open", {"failures": len(self._failures)}))

    def _record_success(self, ctx: Context) -> None:
        with self._lock:
            if self.state == "half_open":
                self._successes += 1
                if self._successes >= self.cfg.success_threshold:
                    self.state = "closed"
                    self._failures.clear()
                    self._successes = 0
                    self._opened_at = None
                    ctx.emit(Event("circuit.closed", {}))
            elif self.state == "closed":
                self._failures.clear()

    # -- sync path -----------------------------------------------------------------
    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        with self._lock:
            self._maybe_transition(ctx)
            if self.state == "open":
                ctx.emit(Event("circuit.rejected", {}))
                raise CircuitOpenError("circuit is open; rejecting call", capability=self.name)
            if self.state == "half_open":
                if self._probes >= self.cfg.half_open_max:
                    ctx.emit(Event("circuit.rejected", {}))
                    raise CircuitOpenError(
                        "circuit half-open; probe limit reached", capability=self.name
                    )
                self._probes += 1
        try:
            result = call_next(ctx)
        except BaseException as exc:  # noqa: BLE001
            self._record_failure(ctx, exc)
            raise
        self._record_success(ctx)
        return result

    # -- async path ------------------------------------------------------------------
    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        with self._lock:
            self._maybe_transition(ctx)
            if self.state == "open":
                ctx.emit(Event("circuit.rejected", {}))
                raise CircuitOpenError("circuit is open; rejecting call", capability=self.name)
            if self.state == "half_open":
                if self._probes >= self.cfg.half_open_max:
                    ctx.emit(Event("circuit.rejected", {}))
                    raise CircuitOpenError(
                        "circuit half-open; probe limit reached", capability=self.name
                    )
                self._probes += 1
        try:
            result = await call_next(ctx)
        except BaseException as exc:  # noqa: BLE001
            self._record_failure(ctx, exc)
            raise
        self._record_success(ctx)
        return result
