"""Field masking capability (RFC-022 §5): redact sensitive data."""

from __future__ import annotations

from typing import Any

from ..context import Context
from ..events import Event
from ..sdk.capability import CALL_NEXT, Capability


def mask_value(value: Any, mask: str = "*", keep: int = 0) -> str:
    text = str(value)
    if keep <= 0:
        return mask * len(text)
    return text[:keep] + mask * max(len(text) - keep, 0)


class Mask(Capability):
    name = "mask"
    version = "1.0.0"
    description = "Redacts sensitive fields from calls, results, and snapshots (RFC-022 §5)."
    priority = 680
    degradation = "bypass"

    schema = {
        "fields": {"type": "any", "default": None},
        "mask": {"type": "str", "default": "*"},
        "keep": {"type": "int", "default": 0, "min": 0},
        "mode": {"type": "str", "default": "both", "enum": ["args", "result", "both"]},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self._fields: list[str] = []

    def initialize(self, services: Any) -> None:
        super().initialize(services)
        fields = self.cfg.fields
        if fields is None:
            self._fields = []
        elif isinstance(fields, str):
            self._fields = [fields]
        else:
            self._fields = [str(f) for f in fields]

    def _apply(self, target: Any) -> Any:
        if isinstance(target, dict):
            for key, value in target.items():
                if key in self._fields and isinstance(value, (str, int, float, bool)):
                    target[key] = mask_value(value, self.cfg.mask, self.cfg.keep)
            return target
        if isinstance(target, list):
            return [self._apply(item) for item in target]
        return target

    def _mask_kwargs(self, ctx: Context) -> None:
        if self.cfg.mode in ("args", "both"):
            masked = {
                k: (mask_value(v, self.cfg.mask, self.cfg.keep) if k in self._fields else v)
                for k, v in ctx.kwargs.items()
            }
            if masked != ctx.kwargs:
                ctx.emit(Event("mask.applied", {"fields": self._fields}))
            ctx.kwargs = masked

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        self._mask_kwargs(ctx)
        result = call_next(ctx)
        if self.cfg.mode in ("result", "both"):
            result = self._apply(result)
        return result

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        self._mask_kwargs(ctx)
        result = await call_next(ctx)
        if self.cfg.mode in ("result", "both"):
            result = self._apply(result)
        return result

