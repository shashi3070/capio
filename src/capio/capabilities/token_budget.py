"""Token budget capability (RFC-030 §12): enforce LLM token limits."""

from __future__ import annotations

from typing import Any, Callable

from ..context import Context
from ..events import Event
from ..exceptions import TokenBudgetExceededError
from ..sdk.capability import CALL_NEXT, Capability
from ._ai import count_tokens, query_text


class TokenBudget(Capability):
    name = "token_budget"
    version = "1.0.0"
    description = "Bounds input tokens for model calls (RFC-030 §12)."
    priority = 470
    degradation = "propagate"

    schema = {
        "budget": {"type": "int", "default": 1000, "min": 1},
        "counter": {"type": "any", "default": None},
        "on_exceeded": {"type": "str", "default": "raise", "enum": ["raise", "trim"]},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self._counter: Callable[[str], int] = count_tokens

    def initialize(self, services: Any) -> None:
        super().initialize(services)
        if callable(self.cfg.counter):
            self._counter = self.cfg.counter

    def _trim(self, text: str, budget: int) -> str:
        words = text.split()
        result: list[str] = []
        total = 0
        for word in words:
            cost = self._counter(word)
            if total + cost > budget:
                break
            result.append(word)
            total += cost
        return " ".join(result)

    def _enforce(self, ctx: Context) -> None:
        text = query_text(ctx)
        used = self._counter(text)
        if used <= self.cfg.budget:
            return
        ctx.emit(Event("token_budget.exceeded", {"used": used, "budget": self.cfg.budget}))
        if self.cfg.on_exceeded == "raise":
            raise TokenBudgetExceededError(
                f"token budget exceeded: {used} > {self.cfg.budget}",
                used=used,
                budget=self.cfg.budget,
            )
        ctx.kwargs["input"] = self._trim(text, self.cfg.budget)

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        self._enforce(ctx)
        return call_next(ctx)

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        self._enforce(ctx)
        return await call_next(ctx)

