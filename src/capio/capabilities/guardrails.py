"""Guardrails capability (RFC-030 §11): input/output safety checks."""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..context import Context
from ..events import Event
from ..exceptions import GuardrailError
from ..sdk.capability import CALL_NEXT, Capability
from ._ai import query_text, result_text


class Guardrails(Capability):
    name = "guardrails"
    version = "1.0.0"
    description = "Applies input and output guardrail checks (RFC-030 §11)."
    priority = 480
    degradation = "propagate"

    schema = {
        "input": {"type": "any", "default": None},
        "output": {"type": "any", "default": None},
        "message": {"type": "str", "default": "guardrail violation"},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self._input: Optional[Callable[[Any, Context], bool]] = None
        self._output: Optional[Callable[[Any, Context], bool]] = None

    def initialize(self, services: Any) -> None:
        super().initialize(services)
        if callable(self.cfg.input):
            self._input = self.cfg.input
        if callable(self.cfg.output):
            self._output = self.cfg.output

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        if self._input is not None and not self._input(query_text(ctx), ctx):
            ctx.emit(Event("guardrails.violated", {"stage": "input"}))
            raise GuardrailError(self.cfg.message)
        result = call_next(ctx)
        if self._output is not None and not self._output(result_text(result), ctx):
            ctx.emit(Event("guardrails.violated", {"stage": "output"}))
            raise GuardrailError(self.cfg.message)
        return result

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        if self._input is not None and not self._input(query_text(ctx), ctx):
            ctx.emit(Event("guardrails.violated", {"stage": "input"}))
            raise GuardrailError(self.cfg.message)
        result = await call_next(ctx)
        if self._output is not None and not self._output(result_text(result), ctx):
            ctx.emit(Event("guardrails.violated", {"stage": "output"}))
            raise GuardrailError(self.cfg.message)
        return result
