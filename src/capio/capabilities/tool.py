"""Tool capability (RFC-030 §9): register a callable as a model tool."""

from __future__ import annotations

import inspect
from typing import Any, Dict

from ..context import Context
from ..events import Event
from ..sdk.capability import CALL_NEXT, Capability


class Tool(Capability):
    name = "tool"
    version = "1.0.0"
    description = "Exposes the wrapped function as a callable tool with a JSON schema (RFC-030 §9)."
    priority = 402
    degradation = "propagate"

    schema = {
        "name": {"type": "str", "default": None},
        "description": {"type": "str", "default": ""},
        "parameters": {"type": "any", "default": None},
        "requires_approval": {"type": "bool", "default": False},
        "enable": {"type": "any", "default": None},
    }

    def _json_schema(self, ctx: Context) -> Dict[str, Any]:
        parameters = self.cfg.parameters
        if callable(parameters):
            return parameters(ctx)
        if isinstance(parameters, dict):
            return parameters
        signature = inspect.signature(ctx.fn)
        properties: Dict[str, Any] = {}
        required: list[str] = []
        string_map = {
            "int": "integer",
            "integer": "integer",
            "float": "number",
            "number": "number",
            "bool": "boolean",
            "boolean": "boolean",
            "list": "array",
            "dict": "object",
            "str": "string",
            "string": "string",
        }
        for param in signature.parameters.values():
            if param.name in ("self", "ctx"):
                continue
            annotation = param.annotation
            if annotation is inspect.Parameter.empty:
                ptype = "string"
            elif isinstance(annotation, str):
                ptype = string_map.get(annotation, "string")
            else:
                ptype = {
                    int: "integer",
                    float: "number",
                    bool: "boolean",
                    list: "array",
                    dict: "object",
                    str: "string",
                }.get(annotation, "string")
            properties[param.name] = {"type": ptype}
            if param.default is inspect.Parameter.empty:
                required.append(param.name)
        return {"type": "object", "properties": properties, "required": required}

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        name = self.cfg.name or ctx.fn_name
        schema = self._json_schema(ctx)
        slot = ctx.capability("tool")["state"]
        slot["name"] = name
        slot["description"] = self.cfg.description
        slot["schema"] = schema
        slot["requires_approval"] = self.cfg.requires_approval
        ctx.emit(Event("tool.registered", {"name": name}))
        return call_next(ctx)

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        return self.run(ctx, call_next)
