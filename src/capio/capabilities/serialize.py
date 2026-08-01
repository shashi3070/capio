"""Serialize capability (RFC-022 §3): input/output serialization boundary."""

from __future__ import annotations

from typing import Any, List

from ..context import Context
from ..events import Event
from ..exceptions import ConfigurationError
from ..sdk.capability import CALL_NEXT, Capability
from ..serialize import decode, encode, is_unsafe


class Serialize(Capability):
    name = "serialize"
    version = "1.0.0"
    description = "Serializes inputs and deserializes outputs across boundaries (RFC-022 §3)."
    priority = 685
    degradation = "propagate"

    schema = {
        "serializer": {"type": "str", "default": "json"},
        "mode": {"type": "str", "default": "both", "enum": ["in", "out", "both"]},
        "fields": {"type": "any", "default": None},
        "trust": {"type": "bool", "default": False},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self._fields: List[str] = []

    def initialize(self, services: Any) -> None:
        super().initialize(services)
        if self.cfg.trust is False and is_unsafe(self.cfg.serializer):
            raise ConfigurationError(
                f"serializer {self.cfg.serializer!r} is unsafe; set trust=True to enable "
                "(RFC-026 §7)"
            )
        fields = self.cfg.fields
        if fields is None:
            self._fields = []
        elif isinstance(fields, str):
            self._fields = [fields]
        else:
            self._fields = [str(f) for f in fields]

    def _serialize_kwargs(self, ctx: Context) -> None:
        if self.cfg.mode not in ("in", "both"):
            return
        kwargs = dict(ctx.kwargs)
        targets = self._fields or list(kwargs)
        for field in targets:
            if field in kwargs:
                kwargs[field] = encode(kwargs[field], self.cfg.serializer)
        ctx.kwargs = kwargs
        ctx.emit(Event("serialize.input", {"serializer": self.cfg.serializer}))

    def _deserialize_result(self, ctx: Context, result: Any) -> Any:
        if self.cfg.mode not in ("out", "both"):
            return result
        value = decode(result, self.cfg.serializer)
        ctx.emit(Event("serialize.output", {"serializer": self.cfg.serializer}))
        return value

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        self._serialize_kwargs(ctx)
        result = call_next(ctx)
        return self._deserialize_result(ctx, result)

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        self._serialize_kwargs(ctx)
        result = await call_next(ctx)
        return self._deserialize_result(ctx, result)
