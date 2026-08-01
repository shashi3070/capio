"""Consume capability (RFC-023 §3): process the next message for a topic."""

from __future__ import annotations

from typing import Any

from ..context import Context
from ..events import Event
from ..sdk.capability import CALL_NEXT, Capability


class Consume(Capability):
    name = "consume"
    version = "1.0.0"
    description = "Dispatches the next message for a topic to the wrapped handler (RFC-023 §3)."
    priority = 650
    degradation = "propagate"

    schema = {
        "topic": {"type": "any", "default": None},
        "broker": {"type": "str", "default": "broker.memory"},
        "group": {"type": "str", "default": "default"},
        "arg": {"type": "str", "default": "message"},
        "block": {"type": "bool", "default": False},
        "timeout": {"type": "any", "default": None},
        "skip_value": {"type": "any", "default": None},
        "enable": {"type": "any", "default": None},
    }

    def _topic(self, ctx: Context) -> str:
        topic = self.cfg.topic
        if topic is None:
            return ctx.fn_name
        if callable(topic):
            return str(topic(ctx))
        return str(topic)

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        broker = self.backend(self.cfg.broker)
        if broker is None:
            ctx.emit(Event("consume.missing", {"broker": self.cfg.broker}))
            return self.cfg.skip_value
        message = broker.consume(self._topic(ctx), self.cfg.group)
        if message is None:
            ctx.emit(Event("consume.empty", {"topic": self._topic(ctx)}))
            return self.cfg.skip_value
        ctx.kwargs = {**ctx.kwargs, self.cfg.arg: message}
        topic = self._topic(ctx)
        ctx.emit(Event("consume.delivered", {"topic": topic, "message_id": message.get("id")}))
        return call_next(ctx)

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        broker = self.backend(self.cfg.broker)
        if broker is None:
            ctx.emit(Event("consume.missing", {"broker": self.cfg.broker}))
            return self.cfg.skip_value
        message = broker.consume(self._topic(ctx), self.cfg.group)
        if message is None:
            ctx.emit(Event("consume.empty", {"topic": self._topic(ctx)}))
            return self.cfg.skip_value
        ctx.kwargs = {**ctx.kwargs, self.cfg.arg: message}
        topic = self._topic(ctx)
        ctx.emit(Event("consume.delivered", {"topic": topic, "message_id": message.get("id")}))
        return await call_next(ctx)

