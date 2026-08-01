"""Model router capability (RFC-030 §13): select a model per request."""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from ..context import Context
from ..events import Event
from ..sdk.capability import CALL_NEXT, Capability


class ModelRouter(Capability):
    name = "model_router"
    version = "1.0.0"
    description = "Routes requests to a model based on rules (RFC-030 §13)."
    priority = 460
    degradation = "bypass"

    schema = {
        "routes": {"type": "any", "default": None},
        "fallback": {"type": "str", "default": "auto"},
        "router": {"type": "any", "default": None},
        "key": {"type": "str", "default": "model"},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self._routes: List[Any] = []
        self._router: Optional[Callable[[Context], str]] = None

    def initialize(self, services: Any) -> None:
        super().initialize(services)
        routes = self.cfg.routes
        if isinstance(routes, dict):
            self._routes = [{"when": cond, "model": model} for cond, model in routes.items()]
        elif isinstance(routes, list):
            self._routes = [route for route in routes if isinstance(route, dict)]
        if callable(self.cfg.router):
            self._router = self.cfg.router

    def _select(self, ctx: Context) -> str:
        if self._router is not None:
            return str(self._router(ctx))
        for route in self._routes:
            when = route.get("when")
            model = route.get("model")
            if callable(when) and when(ctx):
                return str(model)
        return self.cfg.fallback

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        if self.cfg.key not in ctx.kwargs:
            model = self._select(ctx)
            ctx.kwargs[self.cfg.key] = model
            ctx.emit(Event("model_router.selected", {"model": model}))
        return call_next(ctx)

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        if self.cfg.key not in ctx.kwargs:
            model = self._select(ctx)
            ctx.kwargs[self.cfg.key] = model
            ctx.emit(Event("model_router.selected", {"model": model}))
        return await call_next(ctx)
