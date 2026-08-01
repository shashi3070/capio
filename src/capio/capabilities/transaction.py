"""Transaction capability (RFC-023 §5): commit/rollback participants."""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from ..context import Context
from ..events import Event
from ..exceptions import TransactionError
from ..sdk.capability import CALL_NEXT, Capability

_Action = Callable[[Context], Any]


def _participants(actions: Any) -> List[Dict[str, _Action]]:
    if actions is None:
        return []
    if isinstance(actions, dict):
        result: List[Dict[str, _Action]] = []
        for name, spec in actions.items():
            if isinstance(spec, dict) and callable(spec.get("commit")):
                result.append(
                    {
                        "name": str(name),
                        "commit": spec["commit"],
                        "rollback": spec.get("rollback"),
                    }
                )
            elif callable(spec):
                result.append({"name": str(name), "commit": spec, "rollback": None})
        return result
    if callable(actions):
        return [{"name": "participant", "commit": actions, "rollback": None}]
    return []


class Transaction(Capability):
    name = "transaction"
    version = "1.0.0"
    description = "Runs participants with commit/rollback semantics (RFC-023 §5)."
    priority = 630
    degradation = "propagate"

    schema = {
        "actions": {"type": "any", "default": None},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self._participants: List[Dict[str, _Action]] = []

    def initialize(self, services: Any) -> None:
        super().initialize(services)
        self._participants = _participants(self.cfg.actions)

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        ctx.emit(Event("transaction.started", {}))
        try:
            result = call_next(ctx)
        except BaseException as err:  # noqa: BLE001
            self._rollback(ctx, err)
            if isinstance(err, TransactionError):
                raise
            raise TransactionError(f"transaction failed: {err!r}") from err
        for action in self._participants:
            action["commit"](ctx)
        ctx.emit(Event("transaction.committed", {"participants": len(self._participants)}))
        return result

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        ctx.emit(Event("transaction.started", {}))
        try:
            result = await call_next(ctx)
        except BaseException as err:  # noqa: BLE001
            self._rollback(ctx, err)
            if isinstance(err, TransactionError):
                raise
            raise TransactionError(f"transaction failed: {err!r}") from err
        for action in self._participants:
            action["commit"](ctx)
        ctx.emit(Event("transaction.committed", {"participants": len(self._participants)}))
        return result

    def _rollback(self, ctx: Context, err: BaseException) -> None:
        for action in reversed(self._participants):
            rollback = action.get("rollback")
            if rollback is not None:
                try:
                    rollback(ctx)
                except BaseException as rb:  # noqa: BLE001 - best-effort rollback
                    ctx.emit(Event("transaction.rollback_failed", {"error": repr(rb)}))
        ctx.emit(Event("transaction.rolled_back", {}))
