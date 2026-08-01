"""Logging capability (RFC-020 §2): structured invocation records."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

from ..context import Context
from ..events import Event
from ..sdk.capability import CALL_NEXT, Capability

_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


class Log(Capability):
    name = "log"
    version = "1.0.0"
    description = "Emits structured invocation log records (RFC-020 §2)."
    priority = 550
    degradation = "bypass"

    schema = {
        "logger_name": {"type": "any", "default": "auto"},
        "level": {"type": "str", "default": "INFO"},
        "on_success": {"type": "str", "default": "INFO"},
        "on_error": {"type": "str", "default": "WARNING"},
        "include_args": {"type": "bool", "default": False},
        "include_result": {"type": "bool", "default": False},
        "include_duration": {"type": "bool", "default": True},
        "backend": {"type": "str", "default": "log.stdio"},
        "message": {"type": "any", "default": None},
        "enable": {"type": "any", "default": None},
    }

    def _record(self, ctx: Context, outcome: str, duration: float, exc: Any) -> None:
        backend = self.backend(self.cfg.backend)
        if backend is None:
            return
        fields: Dict[str, Any] = {
            "invocation_id": ctx.invocation_id,
            "request_id": ctx.request_id,
            "correlation_id": ctx.correlation_id,
            "fn": f"{ctx.fn_module}.{ctx.fn_name}",
            "outcome": outcome,
        }
        if self.cfg.include_duration:
            fields["duration_ms"] = round(duration * 1000, 3)
        if self.cfg.include_args:
            fields["arg_count"] = len(ctx.args)
            fields["kwarg_keys"] = sorted(ctx.kwargs)
        if self.cfg.include_result and exc is None:
            fields["result_type"] = type(ctx.result()).__name__ if ctx.has_result() else "none"
        if exc is not None:
            fields["error"] = repr(exc)
        level_name = self.cfg.on_error if outcome != "success" else self.cfg.on_success
        level = _LEVELS.get(str(level_name).upper(), logging.INFO)
        message = self.cfg.message or "capio invocation"
        try:
            backend.log(level, message, **fields)
        except Exception as err:  # noqa: BLE001 - log backend failure is never raised
            ctx.emit(Event("log.failed", {"error": repr(err)}))

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        start = time.monotonic()
        outcome = "success"
        exc = None
        try:
            result = call_next(ctx)
            ctx.set_result(result)
            return result
        except BaseException as err:  # noqa: BLE001
            outcome = "error"
            exc = err
            raise
        finally:
            self._record(ctx, outcome, time.monotonic() - start, exc)

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        start = time.monotonic()
        outcome = "success"
        exc = None
        try:
            result = await call_next(ctx)
            ctx.set_result(result)
            return result
        except BaseException as err:  # noqa: BLE001
            outcome = "error"
            exc = err
            raise
        finally:
            self._record(ctx, outcome, time.monotonic() - start, exc)
