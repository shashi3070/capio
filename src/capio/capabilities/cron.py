"""Cron capability (RFC-023 §7): schedule invocations on a cron expression."""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any, List, Set, Tuple

from ..context import Context
from ..events import Event
from ..sdk.capability import CALL_NEXT, Capability

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

_EVERY_RE = re.compile(r"^\s*every\s+(\d+)\s*([smhd])?\s*$")


def _parse_field(spec: str, lo: int, hi: int) -> Set[int]:
    values: Set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            part, _, step_s = part.partition("/")
            step = int(step_s or 1)
        if part == "*":
            base = list(range(lo, hi + 1))
        elif "-" in part:
            start_s, _, end_s = part.partition("-")
            base = list(range(int(start_s), int(end_s) + 1))
        else:
            base = [int(part)]
        values.update(v for v in base[::step] if lo <= v <= hi)
    return values


def parse_cron(expression: str) -> Tuple[List[Set[int]], bool]:
    """Parse a 5- or 6-field cron expression; returns (fields, has_seconds)."""
    parts = [p.strip() for p in expression.split()]
    if len(parts) == 5:
        minutes, hours, dom, months, dow = parts
        fields = [
            _parse_field(minutes, 0, 59),
            _parse_field(hours, 0, 23),
            _parse_field(dom, 1, 31),
            _parse_field(months, 1, 12),
            _parse_field(dow, 0, 6),
        ]
        return fields, False
    if len(parts) == 6:
        seconds, minutes, hours, dom, months, dow = parts
        fields = [
            _parse_field(seconds, 0, 59),
            _parse_field(minutes, 0, 59),
            _parse_field(hours, 0, 23),
            _parse_field(dom, 1, 31),
            _parse_field(months, 1, 12),
            _parse_field(dow, 0, 6),
        ]
        return fields, True
    raise ValueError(f"invalid cron expression: {expression!r}")


def matches(expression: str, now: datetime) -> bool:
    fields, has_seconds = parse_cron(expression)
    if has_seconds:
        seconds, minutes, hours, dom, months, dow = fields
        if now.second not in seconds:
            return False
    else:
        minutes, hours, dom, months, dow = fields
    return (
        now.minute in minutes
        and now.hour in hours
        and now.day in dom
        and now.month in months
        and now.weekday() in dow
    )


def _parse_every(value: str) -> float:
    match = _EVERY_RE.match(value)
    if not match:
        raise ValueError(f"invalid 'every' schedule: {value!r}")
    amount = int(match.group(1))
    unit = match.group(2) or "s"
    return amount * _UNITS.get(unit, 1)


class Cron(Capability):
    name = "cron"
    version = "1.0.0"
    description = "Runs the invocation only when the cron schedule is due (RFC-023 §7)."
    priority = 610
    degradation = "propagate"

    schema = {
        "schedule": {"type": "str", "default": "* * * * *"},
        "backend": {"type": "str", "default": "store.memory"},
        "skip_value": {"type": "any", "default": None},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self._last: dict[str, float] = {}

    def _due(self, ctx: Context) -> bool:
        now = time.time()
        schedule = str(self.cfg.schedule).strip()
        if schedule.startswith("every"):
            interval = _parse_every(schedule)
            last = self._last.get("last", 0.0)
            if now - last < interval:
                return False
            self._last["last"] = now
            return True
        try:
            if not matches(schedule, datetime.now()):
                return False
        except ValueError:
            ctx.emit(Event("cron.invalid", {"schedule": schedule}))
            return False
        last = self._last.get("last", 0.0)
        if now - last < 1.0:
            return False
        self._last["last"] = now
        return True

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        if not self._due(ctx):
            ctx.emit(Event("cron.skipped", {"schedule": str(self.cfg.schedule)}))
            return self.cfg.skip_value
        ctx.emit(Event("cron.fired", {"schedule": str(self.cfg.schedule)}))
        return call_next(ctx)

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        if not self._due(ctx):
            ctx.emit(Event("cron.skipped", {"schedule": str(self.cfg.schedule)}))
            return self.cfg.skip_value
        ctx.emit(Event("cron.fired", {"schedule": str(self.cfg.schedule)}))
        return await call_next(ctx)
