"""Queue capability (RFC-023 §4): enqueue or process background tasks."""

from __future__ import annotations

from typing import Any

from ..config import parse_duration
from ..context import Context
from ..events import Event
from ..sdk.capability import CALL_NEXT, Capability


class Queue(Capability):
    name = "queue"
    version = "1.0.0"
    description = "Pushes tasks onto a queue or consumes them as a worker (RFC-023 §4)."
    priority = 640
    degradation = "propagate"

    schema = {
        "mode": {"type": "str", "default": "enqueue", "enum": ["enqueue", "worker"]},
        "queue": {"type": "str", "default": "default"},
        "backend": {"type": "str", "default": "queue.memory"},
        "task": {"type": "any", "default": None},
        "wait": {"type": "any", "default": "200ms"},
        "skip_value": {"type": "any", "default": None},
        "enable": {"type": "any", "default": None},
    }

    def _task(self, ctx: Context) -> str:
        task = self.cfg.task
        if task is None:
            return self.cfg.queue
        if callable(task):
            return str(task(ctx))
        return str(task)

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        backend = self.backend(self.cfg.backend)
        if backend is None:
            ctx.emit(Event("queue.missing", {"backend": self.cfg.backend}))
            return self.cfg.skip_value
        if self.cfg.mode == "enqueue":
            envelope = backend.put(self._task(ctx), ctx.args, ctx.kwargs)
            ctx.emit(Event("queue.enqueued", {"task": self._task(ctx), "id": envelope.get("id")}))
            return envelope
        envelope = backend.get(timeout=parse_duration(self.cfg.wait) if self.cfg.wait else None)
        if envelope is None:
            ctx.emit(Event("queue.empty", {"queue": self.cfg.queue}))
            return self.cfg.skip_value
        ctx.kwargs = {**ctx.kwargs, "task": envelope}
        ctx.emit(Event("queue.dequeued", {"task": envelope.get("task"), "id": envelope.get("id")}))
        result = call_next(ctx)
        backend.task_done(envelope)
        return result

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        backend = self.backend(self.cfg.backend)
        if backend is None:
            ctx.emit(Event("queue.missing", {"backend": self.cfg.backend}))
            return self.cfg.skip_value
        if self.cfg.mode == "enqueue":
            envelope = backend.put(self._task(ctx), ctx.args, ctx.kwargs)
            ctx.emit(Event("queue.enqueued", {"task": self._task(ctx), "id": envelope.get("id")}))
            return envelope
        envelope = backend.get(timeout=parse_duration(self.cfg.wait) if self.cfg.wait else None)
        if envelope is None:
            ctx.emit(Event("queue.empty", {"queue": self.cfg.queue}))
            return self.cfg.skip_value
        ctx.kwargs = {**ctx.kwargs, "task": envelope}
        ctx.emit(Event("queue.dequeued", {"task": envelope.get("task"), "id": envelope.get("id")}))
        result = await call_next(ctx)
        backend.task_done(envelope)
        return result
