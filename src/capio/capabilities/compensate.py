"""Compensation capability (RFC-023 §8): best-effort recovery on failure."""

from __future__ import annotations

from typing import Any, Callable, List

from ..context import Context
from ..events import Event
from ..sdk.capability import CALL_NEXT, Capability

_Action = Callable[[Context, BaseException], Any]


def _actions(value: Any) -> List[_Action]:
    if value is None:
        return []
    if callable(value):
        return [value]
    return [a for a in value if callable(a)]


class Compensate(Capability):
    name = "compensate"
    version = "1.0.0"
    description = "Runs compensation actions when the call fails (RFC-023 §8)."
    priority = 600
    degradation = "propagate"

    schema = {
        "actions": {"type": "any", "default": None},
        "finalize": {"type": "any", "default": None},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self._actions: List[_Action] = []
        self._finalize: List[_Action] = []

    def initialize(self, services: Any) -> None:
        super().initialize(services)
        self._actions = _actions(self.cfg.actions)
        self._finalize = _actions(self.cfg.finalize)

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        try:
            result = call_next(ctx)
        except BaseException as err:  # noqa: BLE001
            self._compensate(ctx, err)
            raise
        finally:
            self._finalize_now(ctx, None)
        return result

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        try:
            result = await call_next(ctx)
        except BaseException as err:  # noqa: BLE001
            self._compensate(ctx, err)
            raise
        finally:
            self._finalize_now(ctx, None)
        return result

    def _compensate(self, ctx: Context, err: BaseException) -> None:
        if not self._actions:
            return
        ctx.emit(Event("compensate.started", {}))
        for action in self._actions:
            try:
                action(ctx, err)
            except BaseException as inner:  # noqa: BLE001 - best-effort compensation
                ctx.emit(Event("compensate.failed", {"error": repr(inner)}))
        ctx.emit(Event("compensate.executed", {"actions": len(self._actions)}))

    def _finalize_now(self, ctx: Context, err: BaseException | None) -> None:
        for action in self._finalize:
            try:
                action(ctx, err)  # type: ignore[arg-type]
            except BaseException as inner:  # noqa: BLE001 - best-effort finalize
                ctx.emit(Event("compensate.failed", {"error": repr(inner)}))
