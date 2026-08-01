"""Workflow capability (RFC-023 §6): durable multi-step orchestration."""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from ..context import Context
from ..events import Event
from ..exceptions import WorkflowError
from ..sdk.capability import CALL_NEXT, Capability

_Step = Callable[[Context, Dict[str, Any]], Any]


def _steps(value: Any) -> List[_Step]:
    if value is None:
        return []
    if callable(value):
        return [value]
    return [step for step in value if callable(step)]


class Workflow(Capability):
    name = "workflow"
    version = "1.0.0"
    description = "Runs ordered steps with retry and recovery (RFC-023 §6)."
    priority = 620
    degradation = "propagate"

    schema = {
        "steps": {"type": "any", "default": None},
        "state": {"type": "any", "default": None},
        "max_attempts": {"type": "int", "default": 1, "min": 1},
        "recover": {"type": "any", "default": None},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self._steps: List[_Step] = []
        self._recover: Callable[[Context, Dict[str, Any], BaseException], Any] | None = None

    def initialize(self, services: Any) -> None:
        super().initialize(services)
        self._steps = _steps(self.cfg.steps)
        self._recover = self.cfg.recover if callable(self.cfg.recover) else None

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        state = dict(self.cfg.state or {})
        ctx.emit(Event("workflow.started", {"steps": len(self._steps)}))
        try:
            for index, step in enumerate(self._steps):
                attempts = 0
                while True:
                    attempts += 1
                    try:
                        step(ctx, state)
                        ctx.emit(Event("workflow.step", {"index": index, "attempt": attempts}))
                        break
                    except BaseException as err:  # noqa: BLE001
                        if attempts < self.cfg.max_attempts:
                            continue
                        ctx.emit(
                            Event("workflow.step_failed", {"index": index, "error": repr(err)})
                        )
                        if self._recover is not None:
                            self._recover(ctx, state, err)
                        raise WorkflowError(f"workflow step {index} failed: {err!r}") from err
            ctx.emit(Event("workflow.completed", {"steps": len(self._steps)}))
            return state
        except WorkflowError:
            raise
        except BaseException as err:  # noqa: BLE001
            raise WorkflowError(f"workflow failed: {err!r}") from err

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        return self.run(ctx, call_next)

