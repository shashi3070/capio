"""Trace capability (RFC-019 §2): span recording, always best-effort."""

from __future__ import annotations

import time
from typing import Any, Dict

from ..context import Context, _new_id
from ..events import Event
from ..sdk.capability import CALL_NEXT, Capability


class Trace(Capability):
    name = "trace"
    version = "1.0.0"
    description = "Records invocation spans (RFC-019 §2)."
    priority = 600
    degradation = "bypass"

    schema = {
        "name": {"type": "any", "default": "auto"},
        "attributes": {"type": "any", "default": None},
        "attributes_from": {"type": "any", "default": None},
        "capture_args": {"type": "bool", "default": False},
        "capture_result": {"type": "bool", "default": False},
        "backend": {"type": "str", "default": "trace.console"},
        "span_kind": {"type": "str", "default": "internal"},
        "enable": {"type": "any", "default": None},
    }

    def _emit_span(self, ctx: Context, span: Dict[str, Any]) -> None:
        backend = self.backend(self.cfg.backend)
        if backend is None:
            ctx.emit(Event("trace.exporter_failed", {"reason": "backend_missing"}))
            return
        try:
            backend.emit(span)
        except Exception as exc:  # noqa: BLE001 - tracing is always best-effort
            ctx.emit(Event("trace.exporter_failed", {"error": repr(exc)}))

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        name = self.cfg.name
        if name == "auto" or name is None:
            name = f"{ctx.fn_module}.{ctx.fn_name}"
        trace_id = ctx.trace_id or _new_id("tr")
        parent_span = ctx.span_id
        span_id = _new_id("span")
        ctx.trace_id = trace_id
        ctx.span_id = span_id
        attributes = dict(self.cfg.attributes or {})
        if self.cfg.attributes_from:
            attributes.update(self.cfg.attributes_from(ctx))
        start = time.monotonic()
        status = "ok"
        error = None
        try:
            result = call_next(ctx)
            return result
        except BaseException as exc:  # noqa: BLE001
            status = "error"
            error = repr(exc)
            raise
        finally:
            span = {
                "name": name,
                "trace_id": trace_id,
                "span_id": span_id,
                "parent_span_id": parent_span,
                "fn": f"{ctx.fn_module}.{ctx.fn_name}",
                "kind": self.cfg.span_kind,
                "status": status,
                "error": error,
                "duration_ms": (time.monotonic() - start) * 1000,
                "attributes": attributes,
            }
            if self.cfg.capture_args:
                span["args"] = {"count": len(ctx.args), "keys": sorted(ctx.kwargs)}
            if self.cfg.capture_result:
                span["result_type"] = type(result).__name__
            self._emit_span(ctx, span)

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        name = self.cfg.name
        if name == "auto" or name is None:
            name = f"{ctx.fn_module}.{ctx.fn_name}"
        trace_id = ctx.trace_id or _new_id("tr")
        parent_span = ctx.span_id
        span_id = _new_id("span")
        ctx.trace_id = trace_id
        ctx.span_id = span_id
        attributes = dict(self.cfg.attributes or {})
        if self.cfg.attributes_from:
            attributes.update(self.cfg.attributes_from(ctx))
        start = time.monotonic()
        status = "ok"
        error = None
        result = None
        try:
            result = await call_next(ctx)
            return result
        except BaseException as exc:  # noqa: BLE001
            status = "error"
            error = repr(exc)
            raise
        finally:
            span = {
                "name": name,
                "trace_id": trace_id,
                "span_id": span_id,
                "parent_span_id": parent_span,
                "fn": f"{ctx.fn_module}.{ctx.fn_name}",
                "kind": self.cfg.span_kind,
                "status": status,
                "error": error,
                "duration_ms": (time.monotonic() - start) * 1000,
                "attributes": attributes,
            }
            if self.cfg.capture_args:
                span["args"] = {"count": len(ctx.args), "keys": sorted(ctx.kwargs)}
            if self.cfg.capture_result and result is not None:
                span["result_type"] = type(result).__name__
            self._emit_span(ctx, span)
