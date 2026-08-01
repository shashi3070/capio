"""Publish, consume, queue, transaction, workflow, cron, compensate, idempotent tests (RFC-023)."""

from __future__ import annotations

import pytest

from capio import use
from capio.exceptions import (
    IdempotencyConflictError,
    TransactionError,
    WorkflowError,
)
from capio.runtime import default_runtime


def test_publish_sends_to_broker() -> None:
    runtime = default_runtime()
    broker = runtime.services.get("broker.memory")
    broker.clear()

    @use.publish(topic="orders.created", include_result=True)
    def create_order(order_id: int) -> dict:
        return {"id": order_id}

    create_order(42)
    messages = broker.peek("orders.created")
    assert len(messages) == 1
    assert messages[0]["payload"]["id"] == 42


def test_publish_outbox_on_missing_broker() -> None:
    runtime = default_runtime()
    outbox = runtime.services.get("store.memory")
    outbox.clear("outbox")

    @use.publish(topic="orders.created", broker="broker.nope")
    def create_order() -> str:
        return "done"

    create_order()
    assert outbox.sequence("outbox") >= 1


def test_consume_dispatches_next_message() -> None:
    runtime = default_runtime()
    broker = runtime.services.get("broker.memory")
    broker.clear()
    broker.publish("orders.created", {"id": 1})
    seen = {}

    @use.consume(topic="orders.created")
    def handle(message: dict) -> str:
        seen["payload"] = message["payload"]
        return "processed"

    assert handle() == "processed"
    assert seen["payload"] == {"id": 1}


def test_consume_skips_when_empty() -> None:
    runtime = default_runtime()
    broker = runtime.services.get("broker.memory")
    broker.clear()

    @use.consume(topic="empty.topic", skip_value="nothing")
    def handle(message: dict) -> str:
        return "processed"

    assert handle() == "nothing"


def test_queue_enqueue() -> None:
    runtime = default_runtime()
    queue = runtime.services.get("queue.memory")
    queue.clear()

    @use.queue(mode="enqueue", queue="emails")
    def send(payload: str) -> str:
        return payload

    envelope = send("hello")
    assert envelope["task"] == "emails"
    assert queue.size == 1


def test_queue_worker_processes_task() -> None:
    runtime = default_runtime()
    queue = runtime.services.get("queue.memory")
    queue.clear()
    queue.put("emails", ("hello",), {"urgent": True})
    seen = {}

    @use.queue(mode="worker", queue="emails")
    def process(task: dict) -> str:
        seen["task"] = task
        return "ok"

    assert process() == "ok"
    assert seen["task"]["args"] == ("hello",)
    assert seen["task"]["kwargs"] == {"urgent": True}


def test_transaction_commits_in_order() -> None:
    events: list = []

    @use.transaction(
        actions={
            "a": {
                "commit": lambda ctx: events.append("a:commit"),
                "rollback": lambda ctx: events.append("a:rollback"),
            },
            "b": {
                "commit": lambda ctx: events.append("b:commit"),
                "rollback": lambda ctx: events.append("b:rollback"),
            },
        }
    )
    def ok_work() -> str:
        return "done"

    assert ok_work() == "done"
    assert events == ["a:commit", "b:commit"]


def test_transaction_rolls_back_on_error() -> None:
    events: list = []

    @use.transaction(
        actions={
            "a": {
                "commit": lambda ctx: None,
                "rollback": lambda ctx: events.append("a:rollback"),
            }
        }
    )
    def fail_work() -> None:
        raise ValueError("boom")

    with pytest.raises(TransactionError):
        fail_work()
    assert events == ["a:rollback"]


def test_workflow_runs_steps_in_order() -> None:
    def step_a(ctx, state: dict) -> None:
        state["a"] = 1

    def step_b(ctx, state: dict) -> None:
        state["b"] = state.get("a", 0) + 1

    @use.workflow(steps=[step_a, step_b])
    def run_workflow() -> str:
        return "unused"

    result = run_workflow()
    assert result == {"a": 1, "b": 2}


def test_workflow_recovery_on_step_failure() -> None:
    recovered = {}

    def bad_step(ctx, state: dict) -> None:
        raise ValueError("step failed")

    def recover(ctx, state: dict, err) -> None:
        recovered["error"] = repr(err)

    @use.workflow(steps=[bad_step], recover=recover, max_attempts=1)
    def run_workflow() -> str:
        return "unused"

    with pytest.raises(WorkflowError):
        run_workflow()
    assert "step failed" in recovered["error"]


def test_cron_runs_when_matching() -> None:
    @use.cron(schedule="* * * * *")
    def job() -> str:
        return "ran"

    assert job() == "ran"


def test_cron_skips_when_not_due() -> None:
    @use.cron(schedule="0 0 1 1 *")
    def job() -> str:
        return "ran"

    assert job() is None


def test_cron_every_skips_within_interval() -> None:
    @use.cron(schedule="every 30s")
    def job() -> str:
        return "ran"

    assert job() == "ran"
    assert job() is None


def test_compensate_runs_on_error() -> None:
    events: list = []

    @use.compensate(actions=[lambda ctx, err: events.append(str(err))])
    def risky() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError):
        risky()
    assert events == ["boom"]


def test_idempotent_replays_stored_result() -> None:
    state = {"n": 0}

    @use.idempotent(key=lambda ctx: f"k:{ctx.args[0]}", ttl="10s")
    def process(x: int) -> str:
        state["n"] += 1
        return f"result-{x}"

    assert process(1) == "result-1"
    assert process(1) == "result-1"
    assert state["n"] == 1
    assert process(2) == "result-2"
    assert state["n"] == 2


def test_idempotent_conflicts_on_different_request() -> None:
    @use.idempotent(key="fixed-key")
    def process(x: int) -> str:
        return f"result-{x}"

    assert process(1) == "result-1"
    with pytest.raises(IdempotencyConflictError):
        process(2)
