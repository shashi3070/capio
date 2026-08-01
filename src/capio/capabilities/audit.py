"""Audit capability (RFC-020 §4): append-only trail of sensitive actions."""

from __future__ import annotations

import time
from typing import Any

from ..context import Context
from ..events import Event
from ..sdk.capability import CALL_NEXT, Capability


def _resolve(value: Any, ctx: Context, fallback: str) -> str:
    if value is None:
        return fallback
    if callable(value):
        return str(value(ctx))
    return str(value)


class Audit(Capability):
    name = "audit"
    version = "1.0.0"
    description = "Records auditable actions to an append-only trail (RFC-020 §4)."
    priority = 760
    degradation = "bypass"

    schema = {
        "backend": {"type": "str", "default": "audit.memory"},
        "action": {"type": "any", "default": None},
        "resource": {"type": "any", "default": None},
        "actor": {"type": "any", "default": None},
        "include_payload": {"type": "bool", "default": False},
        "strict": {"type": "bool", "default": False},
        "enable": {"type": "any", "default": None},
    }

    def _write(self, ctx: Context, outcome: str, duration: float, exc: Any) -> None:
        backend = self.backend(self.cfg.backend)
        if backend is None:
            ctx.emit(Event("audit.missing", {"backend": self.cfg.backend}))
            if self.cfg.strict:
                raise RuntimeError(f"audit backend {self.cfg.backend!r} not bound")
            return
        record: dict[str, Any] = {
            "invocation_id": ctx.invocation_id,
            "request_id": ctx.request_id,
            "actor": _resolve(self.cfg.actor, ctx, "anonymous"),
            "action": _resolve(self.cfg.action, ctx, ctx.fn_name),
            "resource": _resolve(self.cfg.resource, ctx, f"{ctx.fn_module}.{ctx.fn_name}"),
            "outcome": outcome,
            "duration_ms": round(duration * 1000, 3),
            "trace_id": ctx.trace_id,
        }
        if self.cfg.include_payload:
            record["arg_count"] = len(ctx.args)
            record["kwarg_keys"] = sorted(ctx.kwargs)
        if exc is not None:
            record["error"] = repr(exc)
        try:
            backend.append(record)
        except Exception as err:  # noqa: BLE001 - audit write failure is never raised
            ctx.emit(Event("audit.failed", {"error": repr(err)}))

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        start = time.monotonic()
        outcome = "success"
        exc: BaseException | None = None
        try:
            result = call_next(ctx)
            return result
        except BaseException as err:  # noqa: BLE001
            outcome = "error"
            exc = err
            raise
        finally:
            self._write(ctx, outcome, time.monotonic() - start, exc)

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        start = time.monotonic()
        outcome = "success"
        exc: BaseException | None = None
        try:
            result = await call_next(ctx)
            return result
        except BaseException as err:  # noqa: BLE001
            outcome = "error"
            exc = err
            raise
        finally:
            self._write(ctx, outcome, time.monotonic() - start, exc)
