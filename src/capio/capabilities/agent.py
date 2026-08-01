"""Agent capability (RFC-030 §10): tool-calling loop."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from ..context import Context
from ..events import Event
from ..sdk.capability import CALL_NEXT, Capability


def _resolve_tools(value: Any) -> Dict[str, Callable[..., Any]]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items() if callable(v)}
    tools: Dict[str, Callable[..., Any]] = {}
    for tool in value:
        if callable(tool):
            tools[getattr(tool, "__name__", "tool")] = tool
    return tools


class Agent(Capability):
    name = "agent"
    version = "1.0.0"
    description = "Drives a tool-calling loop around the wrapped model step (RFC-030 §10)."
    priority = 401
    degradation = "propagate"

    schema = {
        "tools": {"type": "any", "default": None},
        "max_steps": {"type": "int", "default": 10, "min": 1},
        "final_detector": {"type": "any", "default": None},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self._tools: Dict[str, Callable[..., Any]] = {}
        self._final_detector: Optional[Callable[[Any, Context], bool]] = None

    def initialize(self, services: Any) -> None:
        super().initialize(services)
        self._tools = _resolve_tools(self.cfg.tools)
        if callable(self.cfg.final_detector):
            self._final_detector = self.cfg.final_detector

    def _tool_calls(self, response: Any) -> List[Dict[str, Any]]:
        if not isinstance(response, dict):
            return []
        calls = response.get("tool_calls") or response.get("tool_call")
        if calls is None:
            return []
        if isinstance(calls, dict):
            calls = [calls]
        return [call for call in calls if isinstance(call, dict)]

    def _is_final(self, response: Any, ctx: Context) -> bool:
        if self._final_detector is not None:
            return bool(self._final_detector(response, ctx))
        return not self._tool_calls(response)

    def _step(self, ctx: Context, call_next: CALL_NEXT, messages: List[Any]) -> Any:
        ctx.kwargs = {**ctx.kwargs, "messages": messages}
        return call_next(ctx)

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        messages = list(ctx.kwargs.get("messages") or [])
        ctx.emit(Event("agent.started", {"tools": sorted(self._tools)}))
        response = None
        for step in range(self.cfg.max_steps):
            response = self._step(ctx, call_next, messages)
            ctx.emit(Event("agent.step", {"step": step + 1}))
            if self._is_final(response, ctx):
                ctx.emit(Event("agent.finished", {"steps": step + 1}))
                return {"response": response, "steps": step + 1}
            for call in self._tool_calls(response):
                name = str(call.get("name", ""))
                args = call.get("arguments") or call.get("args") or {}
                fn = self._tools.get(name)
                if fn is None:
                    ctx.emit(Event("agent.tool_missing", {"name": name}))
                    content = f"unknown tool {name!r}"
                    messages.append({"role": "tool", "name": name, "content": content})
                    continue
                ctx.emit(Event("agent.tool_call", {"name": name}))
                try:
                    result = fn(**args) if isinstance(args, dict) else fn(args)
                except BaseException as err:  # noqa: BLE001 - feed the error back to the model
                    result = f"tool error: {err!r}"
                messages.append({"role": "tool", "name": name, "content": str(result)})
        ctx.emit(Event("agent.finished", {"steps": self.cfg.max_steps, "exhausted": True}))
        return {"response": response, "steps": self.cfg.max_steps, "exhausted": True}

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        messages = list(ctx.kwargs.get("messages") or [])
        ctx.emit(Event("agent.started", {"tools": sorted(self._tools)}))
        response = None
        for step in range(self.cfg.max_steps):
            response = await self._step(ctx, call_next, messages)
            ctx.emit(Event("agent.step", {"step": step + 1}))
            if self._is_final(response, ctx):
                ctx.emit(Event("agent.finished", {"steps": step + 1}))
                return {"response": response, "steps": step + 1}
            for call in self._tool_calls(response):
                name = str(call.get("name", ""))
                args = call.get("arguments") or call.get("args") or {}
                fn = self._tools.get(name)
                if fn is None:
                    ctx.emit(Event("agent.tool_missing", {"name": name}))
                    content = f"unknown tool {name!r}"
                    messages.append({"role": "tool", "name": name, "content": content})
                    continue
                ctx.emit(Event("agent.tool_call", {"name": name}))
                try:
                    result = fn(**args) if isinstance(args, dict) else fn(args)
                except BaseException as err:  # noqa: BLE001 - feed the error back to the model
                    result = f"tool error: {err!r}"
                messages.append({"role": "tool", "name": name, "content": str(result)})
        ctx.emit(Event("agent.finished", {"steps": self.cfg.max_steps, "exhausted": True}))
        return {"response": response, "steps": self.cfg.max_steps, "exhausted": True}

