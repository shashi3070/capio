"""Publish capability (RFC-023 §2): send the call's payload to a topic."""

from __future__ import annotations

from typing import Any, Dict

from ..context import Context
from ..events import Event
from ..sdk.capability import CALL_NEXT, Capability


class Publish(Capability):
    name = "publish"
    version = "1.0.0"
    description = "Publishes the invocation payload to a topic (RFC-023 §2)."
    priority = 660
    degradation = "bypass"

    schema = {
        "topic": {"type": "any", "default": None},
        "broker": {"type": "str", "default": "broker.memory"},
        "outbox": {"type": "any", "default": None},
        "group": {"type": "str", "default": "default"},
        "include_result": {"type": "bool", "default": False},
        "strict": {"type": "bool", "default": False},
        "enable": {"type": "any", "default": None},
    }

    def _topic(self, ctx: Context) -> str:
        topic = self.cfg.topic
        if topic is None:
            return ctx.fn_name
        if callable(topic):
            return str(topic(ctx))
        return str(topic)

    def _payload(self, ctx: Context, result: Any) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "invocation_id": ctx.invocation_id,
            "topic": self._topic(ctx),
            "group": self.cfg.group,
            "args": {"count": len(ctx.args), "keys": sorted(ctx.kwargs)},
        }
        if self.cfg.include_result:
            payload["result"] = result
        return payload

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        result = call_next(ctx)
        self._send(ctx, result)
        return result

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        result = await call_next(ctx)
        self._send(ctx, result)
        return result

    def _send(self, ctx: Context, result: Any) -> None:
        topic = self._topic(ctx)
        payload = self._payload(ctx, result)
        broker = self.backend(self.cfg.broker)
        if self.cfg.outbox:
            outbox = self.backend(str(self.cfg.outbox))
        elif broker is None:
            outbox = self.backend("store.memory")
        else:
            outbox = None
        if broker is None and outbox is None:
            ctx.emit(Event("publish.missing", {"topic": topic}))
            if self.cfg.strict:
                raise RuntimeError(f"broker {self.cfg.broker!r} not bound")
            return
        if broker is not None:
            try:
                broker.publish(topic, payload.get("result") if self.cfg.include_result else payload)
                ctx.emit(Event("publish.sent", {"topic": topic}))
                return
            except Exception as err:  # noqa: BLE001 - broker failure is published to the outbox
                ctx.emit(Event("publish.failed", {"topic": topic, "error": repr(err)}))
        if outbox is not None:
            try:
                outbox.put("outbox", f"{topic}:{ctx.invocation_id}", payload)
                ctx.emit(Event("publish.outboxed", {"topic": topic}))
            except Exception as err:  # noqa: BLE001
                ctx.emit(Event("publish.failed", {"topic": topic, "error": repr(err)}))
