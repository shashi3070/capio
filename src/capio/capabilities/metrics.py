"""Metrics capability (RFC-019 §3): counters and duration histograms, best-effort."""

from __future__ import annotations

import time
from typing import Any, Dict

from ..context import Context
from ..events import Event
from ..sdk.capability import CALL_NEXT, Capability


class Metrics(Capability):
    name = "metrics"
    version = "1.0.0"
    description = "Records call counters and duration histograms (RFC-019 §3)."
    priority = 500
    degradation = "bypass"

    schema = {
        "name": {"type": "any", "default": "auto"},
        "counter": {"type": "bool", "default": True},
        "tags": {"type": "any", "default": None},
        "tags_from": {"type": "any", "default": None},
        "record_duration": {"type": "bool", "default": True},
        "record_result": {"type": "bool", "default": True},
        "backend": {"type": "str", "default": "metrics.null"},
        "per_instance": {"type": "bool", "default": False},
        "enable": {"type": "any", "default": None},
    }

    def _emit(self, ctx: Context, metric: Dict[str, Any]) -> None:
        backend = self.backend(self.cfg.backend)
        if backend is None:
            return
        try:
            backend.record(metric)
        except Exception as exc:  # noqa: BLE001 - metrics are fail-safe
            ctx.emit(Event("metrics.exporter_failed", {"error": repr(exc)}))

    def _prefix(self, ctx: Context) -> str:
        name = self.cfg.name
        if name == "auto" or name is None:
            return f"{ctx.fn_module}.{ctx.fn_name}"
        return str(name)

    def _tags(self, ctx: Context) -> Dict[str, Any]:
        tags = dict(self.cfg.tags or {})
        if self.cfg.tags_from:
            tags.update(self.cfg.tags_from(ctx))
        if self.cfg.per_instance and ctx.self_or_cls is not None:
            tags["instance"] = str(id(ctx.self_or_cls))
        return tags

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        prefix = self._prefix(ctx)
        tags = self._tags(ctx)
        start = time.monotonic()
        outcome = "success"
        try:
            result = call_next(ctx)
            return result
        except BaseException as exc:  # noqa: BLE001
            outcome = type(exc).__name__
            raise
        finally:
            if self.cfg.counter:
                self._emit(
                    ctx,
                    {
                        "kind": "counter",
                        "name": f"{prefix}.calls_total",
                        "tags": {**tags, "outcome": outcome},
                    },
                )
            if self.cfg.record_duration:
                self._emit(
                    ctx,
                    {
                        "kind": "histogram",
                        "name": f"{prefix}.duration_ms",
                        "value": (time.monotonic() - start) * 1000,
                        "tags": tags,
                    },
                )

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        prefix = self._prefix(ctx)
        tags = self._tags(ctx)
        start = time.monotonic()
        outcome = "success"
        try:
            result = await call_next(ctx)
            return result
        except BaseException as exc:  # noqa: BLE001
            outcome = type(exc).__name__
            raise
        finally:
            if self.cfg.counter:
                self._emit(
                    ctx,
                    {
                        "kind": "counter",
                        "name": f"{prefix}.calls_total",
                        "tags": {**tags, "outcome": outcome},
                    },
                )
            if self.cfg.record_duration:
                self._emit(
                    ctx,
                    {
                        "kind": "histogram",
                        "name": f"{prefix}.duration_ms",
                        "value": (time.monotonic() - start) * 1000,
                        "tags": tags,
                    },
                )
