"""Validation capability (RFC-022 §3): schema-based input/output checks."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from ..context import Context
from ..events import Event
from ..exceptions import ValidationError
from ..sdk.capability import CALL_NEXT, Capability

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _check(value: Any, spec: Any, field: str) -> None:
    """Validate ``value`` against ``spec``; raise ValidationError on failure."""
    if callable(spec):
        ok = spec(value)
        if not ok:
            raise ValidationError(f"field {field!r} failed custom validator")
        return
    if not isinstance(spec, dict):
        return
    type_map = {
        "string": str,
        "str": str,
        "int": int,
        "integer": int,
        "float": float,
        "number": (int, float),
        "bool": bool,
        "boolean": bool,
        "list": list,
        "dict": dict,
    }
    if "type" in spec:
        target = type_map.get(str(spec["type"]))
        if target is not None and value is not None and not isinstance(value, target):
            raise ValidationError(
                f"field {field!r}: expected {spec['type']}, got {type(value).__name__}"
            )
    if "required" in spec and spec["required"] and value is None:
        raise ValidationError(f"field {field!r} is required")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "min" in spec and value < spec["min"]:
            raise ValidationError(f"field {field!r}: below minimum {spec['min']}")
        if "max" in spec and value > spec["max"]:
            raise ValidationError(f"field {field!r}: above maximum {spec['max']}")
    if isinstance(value, str):
        if "min_length" in spec and len(value) < spec["min_length"]:
            raise ValidationError(f"field {field!r}: too short")
        if "max_length" in spec and len(value) > spec["max_length"]:
            raise ValidationError(f"field {field!r}: too long")
        if "email" in spec and not _EMAIL_RE.match(value):
            raise ValidationError(f"field {field!r}: not a valid email")
        if "regex" in spec and re.search(str(spec["regex"]), value) is None:
            raise ValidationError(f"field {field!r}: pattern mismatch")
    if "enum" in spec and value not in spec["enum"]:
        raise ValidationError(f"field {field!r}: not one of {spec['enum']}")
    if "one_of" in spec and value not in spec["one_of"]:
        raise ValidationError(f"field {field!r}: not one of {spec['one_of']}")


class Validate(Capability):
    name = "validate"
    version = "1.0.0"
    description = "Validates inputs/outputs against a schema (RFC-022 §3)."
    priority = 700
    degradation = "propagate"

    schema = {
        "input": {"type": "any", "default": None},
        "output": {"type": "any", "default": None},
        "strict": {"type": "bool", "default": True},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self._input: Optional[Dict[str, Any]] = None
        self._output: Optional[Any] = None

    def initialize(self, services: Any) -> None:
        super().initialize(services)
        self._input = dict(self.cfg.input) if isinstance(self.cfg.input, dict) else None
        self._output = self.cfg.output

    def _validate_input(self, ctx: Context) -> None:
        if not self._input:
            return
        for field, spec in self._input.items():
            if field in ctx.kwargs:
                _check(ctx.kwargs[field], spec, field)
            elif isinstance(field, int) and 0 <= field < len(ctx.args):
                _check(ctx.args[field], spec, f"args[{field}]")
            elif isinstance(spec, dict) and spec.get("required"):
                raise ValidationError(f"field {field!r} is required")

    def _validate_output(self, ctx: Context, result: Any) -> None:
        if self._output is None:
            return
        _check(result, self._output, "result")

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        try:
            self._validate_input(ctx)
        except ValidationError as err:
            ctx.emit(Event("validate.failed", {"error": str(err)}))
            raise
        result = call_next(ctx)
        try:
            self._validate_output(ctx, result)
        except ValidationError as err:
            ctx.emit(Event("validate.failed", {"error": str(err)}))
            raise
        return result

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        try:
            self._validate_input(ctx)
        except ValidationError as err:
            ctx.emit(Event("validate.failed", {"error": str(err)}))
            raise
        result = await call_next(ctx)
        try:
            self._validate_output(ctx, result)
        except ValidationError as err:
            ctx.emit(Event("validate.failed", {"error": str(err)}))
            raise
        return result

