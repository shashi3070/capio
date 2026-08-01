"""Rate limit capability (RFC-018 §4): admission control per key."""

from __future__ import annotations

import asyncio
import re
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Tuple

from ..config import parse_duration
from ..context import Context
from ..events import Event
from ..exceptions import RateLimitExceededError
from ..sdk.capability import CALL_NEXT, Capability

_RATE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*/\s*(\d*(?:\.\d+)?)s?\s*$")


def _parse_rate(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    match = _RATE_RE.match(str(value))
    if not match:
        raise ValueError(f"invalid refill rate: {value!r}")
    numerator = float(match.group(1))
    denominator = float(match.group(2)) if match.group(2) else 1.0
    return numerator / denominator


class RateLimit(Capability):
    name = "rate_limit"
    version = "1.0.0"
    description = "Bounds call frequency per key (RFC-018 §4)."
    priority = 850
    degradation = "propagate"

    schema = {
        "limit": {"type": "int", "default": 100, "min": 1},
        "window": {"type": "any", "default": "1m"},
        "strategy": {
            "type": "str",
            "default": "sliding",
            "enum": ["fixed", "sliding", "token_bucket"],
        },
        "bucket_capacity": {"type": "int", "default": None, "min": 1},
        "refill_rate": {"type": "any", "default": "100/s"},
        "key": {"type": "any", "default": None},
        "on_exceeded": {"type": "str", "default": "raise", "enum": ["raise", "wait", "return"]},
        "max_wait": {"type": "any", "default": "5s"},
        "fallback": {"type": "any", "default": None},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.RLock()
        self._fixed: Dict[str, list] = {}  # key -> [slot, count]
        self._sliding: Dict[str, Deque[float]] = {}
        self._bucket: Dict[str, list] = {}  # key -> [tokens, last_ts]

    # -- windowing ---------------------------------------------------------------
    def _key(self, ctx: Context) -> str:
        key_cfg = self.cfg.key
        if key_cfg is None:
            return f"fn:{ctx.fn_module}.{ctx.fn_name}"
        if callable(key_cfg):
            return str(key_cfg(ctx))
        return str(key_cfg)

    def _check(self, ctx: Context, now: float) -> Tuple[bool, float]:
        key = self._key(ctx)
        with self._lock:
            strategy = self.cfg.strategy
            if strategy == "fixed":
                return self._check_fixed(key, now)
            if strategy == "token_bucket":
                return self._check_token(key, now)
            return self._check_sliding(key, now)

    def _check_fixed(self, key: str, now: float) -> Tuple[bool, float]:
        window = parse_duration(self.cfg.window)
        slot = int(now // window)
        state = self._fixed.setdefault(key, [slot, 0])
        if state[0] != slot:
            state[0] = slot
            state[1] = 0
        if state[1] >= self.cfg.limit:
            return False, (slot + 1) * window - now
        state[1] += 1
        return True, 0.0

    def _check_sliding(self, key: str, now: float) -> Tuple[bool, float]:
        window = parse_duration(self.cfg.window)
        times = self._sliding.setdefault(key, deque())
        while times and times[0] <= now - window:
            times.popleft()
        if len(times) >= self.cfg.limit:
            return False, times[0] + window - now
        times.append(now)
        return True, 0.0

    def _check_token(self, key: str, now: float) -> Tuple[bool, float]:
        capacity = self.cfg.bucket_capacity or self.cfg.limit
        rate = _parse_rate(self.cfg.refill_rate)
        state = self._bucket.setdefault(key, [float(capacity), now])
        tokens, last = state
        tokens = min(float(capacity), tokens + (now - last) * rate)
        if tokens >= 1.0:
            state[0] = tokens - 1.0
            state[1] = now
            return True, 0.0
        state[1] = now
        return False, (1.0 - tokens) / rate

    # -- shared run body ------------------------------------------------------------
    def _should_wait_raise_or_return(self, ctx: Context, retry_after: float) -> Tuple[bool, Any]:
        """Return (proceed_after_waiting, value_or_None); raises when done."""
        ctx.emit(Event("rate.limited", {"retry_after": retry_after}))
        on_exceeded = self.cfg.on_exceeded
        if on_exceeded == "raise":
            raise RateLimitExceededError(retry_after=retry_after)
        if on_exceeded == "return":
            return True, self.cfg.fallback  # short-circuit: return fallback
        max_wait = parse_duration(self.cfg.max_wait) if self.cfg.max_wait is not None else None
        if max_wait is not None and retry_after > max_wait:
            raise RateLimitExceededError(retry_after=retry_after)
        return False, None  # wait and retry

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        while True:
            ok, retry_after = self._check(ctx, time.monotonic())
            if ok:
                break
            short_circuit, value = self._should_wait_raise_or_return(ctx, retry_after)
            if short_circuit:
                return value
            time.sleep(retry_after)
        return call_next(ctx)

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        while True:
            ok, retry_after = self._check(ctx, time.monotonic())
            if ok:
                break
            short_circuit, value = self._should_wait_raise_or_return(ctx, retry_after)
            if short_circuit:
                return value
            await asyncio.sleep(retry_after)
        return await call_next(ctx)
