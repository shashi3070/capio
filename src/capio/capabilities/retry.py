"""Retry capability (RFC-017): backoff, jitter, retry predicates."""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Optional, Tuple

from ..config import parse_duration
from ..context import Context
from ..events import Event
from ..exceptions import (
    NON_RETRYABLE_ALWAYS,
    NON_RETRYABLE_CAPIO,
    CapioCancelledBase,
    RetryExhaustedError,
)
from ..sdk.capability import CALL_NEXT, Capability

_NEVER_RETRY = (KeyboardInterrupt, SystemExit, asyncio.CancelledError)


class Retry(Capability):
    name = "retry"
    version = "1.0.0"
    description = "Retries the wrapped callable on failure with backoff and jitter (RFC-017)."
    priority = 700
    degradation = "propagate"

    schema = {
        "max_attempts": {"type": "int", "default": 3, "min": 1},
        "delay": {"type": "any", "default": "100ms"},
        "max_delay": {"type": "any", "default": None},
        "backoff": {
            "type": "str",
            "default": "exponential",
            "enum": ["fixed", "linear", "exponential"],
        },
        "multiplier": {"type": "number", "default": 2.0, "min": 1.0},
        "jitter": {"type": "any", "default": True},
        "retry_on": {"type": "any", "default": None},
        "retry_if": {"type": "any", "default": None},
        "on_final": {"type": "str", "default": "wrap", "enum": ["wrap", "reraise_original"]},
        "max_elapsed": {"type": "any", "default": None},
        "log_every": {"type": "int", "default": 1, "min": 1},
        "enable": {"type": "any", "default": None},
    }

    # -- shared helpers -------------------------------------------------------
    def _should_retry(self, ctx: Context, exc: BaseException) -> bool:
        retry_if = self.cfg.retry_if
        if retry_if is not None:
            return bool(retry_if(ctx, exc))
        retry_on = self.cfg.retry_on
        types: Tuple[type, ...]
        if retry_on is None:
            types = (Exception,)
        elif isinstance(retry_on, tuple):
            types = retry_on
        else:
            types = (retry_on,)
        if not any(isinstance(exc, t) for t in types):
            return False
        explicit = any(t in NON_RETRYABLE_ALWAYS or t in NON_RETRYABLE_CAPIO for t in types)
        if explicit:
            return True
        if isinstance(exc, NON_RETRYABLE_ALWAYS) or isinstance(exc, NON_RETRYABLE_CAPIO):
            return False
        return True

    def _compute_delay(self, attempt: int) -> float:
        delay = parse_duration(self.cfg.delay)
        backoff = self.cfg.backoff
        multiplier = self.cfg.multiplier or 1.0
        if backoff == "fixed":
            base = delay
        elif backoff == "linear":
            base = delay * attempt * multiplier
        else:  # exponential
            base = delay * (multiplier ** (attempt - 1))
        if self.cfg.max_delay is not None:
            base = min(base, parse_duration(self.cfg.max_delay))
        jitter = self.cfg.jitter
        if jitter:
            if jitter is True:
                return random.uniform(0, base)
            a, b = jitter
            return random.uniform(a, b) * base
        return base

    # -- sync path -------------------------------------------------------------
    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        attempts = 0
        last_exc: Optional[BaseException] = None
        first_exc: Optional[BaseException] = None
        started = time.monotonic()
        max_elapsed = (
            parse_duration(self.cfg.max_elapsed) if self.cfg.max_elapsed is not None else None
        )
        while True:
            attempts += 1
            try:
                result = call_next(ctx)
                if attempts > 1:
                    ctx.emit(Event("retry.succeeded", {"attempts": attempts}))
                return result
            except (CapioCancelledBase, *_NEVER_RETRY):  # noqa: BLE001
                raise
            except BaseException as exc:  # noqa: BLE001
                last_exc = exc
                if first_exc is None:
                    first_exc = exc
                if attempts >= self.cfg.max_attempts or not self._should_retry(ctx, exc):
                    break
                if max_elapsed is not None and time.monotonic() - started >= max_elapsed:
                    break
                delay = self._compute_delay(attempts)
                log_every = self.cfg.log_every or 1
                if attempts % log_every == 0:
                    ctx.emit(
                        Event(
                            "retry.attempt",
                            {"attempt": attempts, "exc": repr(exc), "delay": delay},
                        )
                    )
                time.sleep(delay)
        ctx.emit(Event("retry.exhausted", {"attempts": attempts, "exc": repr(last_exc)}))
        if self.cfg.on_final == "reraise_original":
            assert first_exc is not None
            raise first_exc
        raise RetryExhaustedError(
            f"retry exhausted after {attempts} attempts",
            capability=self.name,
        ) from last_exc

    # -- async path --------------------------------------------------------------
    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        attempts = 0
        last_exc: Optional[BaseException] = None
        first_exc: Optional[BaseException] = None
        started = time.monotonic()
        max_elapsed = (
            parse_duration(self.cfg.max_elapsed) if self.cfg.max_elapsed is not None else None
        )
        while True:
            attempts += 1
            try:
                result = await call_next(ctx)
                if attempts > 1:
                    ctx.emit(Event("retry.succeeded", {"attempts": attempts}))
                return result
            except (CapioCancelledBase, *_NEVER_RETRY):  # noqa: BLE001
                raise
            except BaseException as exc:  # noqa: BLE001
                last_exc = exc
                if first_exc is None:
                    first_exc = exc
                if attempts >= self.cfg.max_attempts or not self._should_retry(ctx, exc):
                    break
                if max_elapsed is not None and time.monotonic() - started >= max_elapsed:
                    break
                delay = self._compute_delay(attempts)
                log_every = self.cfg.log_every or 1
                if attempts % log_every == 0:
                    ctx.emit(
                        Event(
                            "retry.attempt",
                            {"attempt": attempts, "exc": repr(exc), "delay": delay},
                        )
                    )
                await asyncio.sleep(delay)
        ctx.emit(Event("retry.exhausted", {"attempts": attempts, "exc": repr(last_exc)}))
        if self.cfg.on_final == "reraise_original":
            assert first_exc is not None
            raise first_exc
        raise RetryExhaustedError(
            f"retry exhausted after {attempts} attempts",
            capability=self.name,
        ) from last_exc
