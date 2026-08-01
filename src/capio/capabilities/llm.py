"""LLM capability (RFC-030 §2): structured provider call boundary."""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..context import Context
from ..events import Event
from ..exceptions import ProviderError
from ..sdk.capability import CALL_NEXT, Capability


class LLM(Capability):
    name = "llm"
    version = "1.0.0"
    description = "Structured boundary around a model provider call (RFC-030 §2)."
    priority = 400
    degradation = "propagate"

    schema = {
        "provider": {"type": "any", "default": None},
        "model": {"type": "str", "default": "auto"},
        "temperature": {"type": "any", "default": None},
        "max_tokens": {"type": "any", "default": None},
        "fallback": {"type": "any", "default": None},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self._provider: Optional[Callable[[Any], Any]] = None

    def initialize(self, services: Any) -> None:
        super().initialize(services)
        if callable(self.cfg.provider):
            self._provider = self.cfg.provider

    def _apply_defaults(self, ctx: Context) -> None:
        if self.cfg.model != "auto" and "model" not in ctx.kwargs:
            ctx.kwargs["model"] = self.cfg.model
        if self.cfg.temperature is not None and "temperature" not in ctx.kwargs:
            ctx.kwargs["temperature"] = self.cfg.temperature
        if self.cfg.max_tokens is not None and "max_tokens" not in ctx.kwargs:
            ctx.kwargs["max_tokens"] = self.cfg.max_tokens

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        self._apply_defaults(ctx)
        ctx.emit(Event("llm.started", {"model": ctx.kwargs.get("model", self.cfg.model)}))
        try:
            if self._provider is not None:
                request = call_next(ctx)
                response = self._provider(request)
            else:
                response = call_next(ctx)
        except Exception as err:  # noqa: BLE001
            ctx.emit(Event("llm.failed", {"error": repr(err)}))
            if self.cfg.fallback is not None:
                return self.cfg.fallback(ctx) if callable(self.cfg.fallback) else self.cfg.fallback
            raise ProviderError(f"provider call failed: {err!r}") from err
        slot = ctx.capability("llm")["state"]
        slot["response"] = response
        ctx.emit(Event("llm.completed", {"model": ctx.kwargs.get("model", self.cfg.model)}))
        return response

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        self._apply_defaults(ctx)
        ctx.emit(Event("llm.started", {"model": ctx.kwargs.get("model", self.cfg.model)}))
        try:
            if self._provider is not None:
                request = await call_next(ctx)
                response = self._provider(request)
            else:
                response = await call_next(ctx)
        except Exception as err:  # noqa: BLE001
            ctx.emit(Event("llm.failed", {"error": repr(err)}))
            if self.cfg.fallback is not None:
                return self.cfg.fallback(ctx) if callable(self.cfg.fallback) else self.cfg.fallback
            raise ProviderError(f"provider call failed: {err!r}") from err
        slot = ctx.capability("llm")["state"]
        slot["response"] = response
        ctx.emit(Event("llm.completed", {"model": ctx.kwargs.get("model", self.cfg.model)}))
        return response
