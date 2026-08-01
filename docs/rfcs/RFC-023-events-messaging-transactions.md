# RFC-023: Events, Messaging, Transactions, Workflows, Cron

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies the **asynchronous and batch execution capabilities**: distributed **Events &
Messaging**, **Queue**, **Transaction**, **Workflow**, **Cron/Scheduling**, and **Compensation**.
These extend Capio from single-function behavior into composed, asynchronous, and scheduled
execution — while keeping the same capability model (RFC-012) and pipeline semantics (RFC-005).

## 2. Events & Messaging capabilities

### 2.1 API

```python
@use.publish(
    topic="order.created",        # or dynamic (ctx) -> topic
    backend="kafka",              # event backend (RFC-015 §3.5)
    serializer="json",
    headers=None,                 # extra headers; ctx carrier auto-attached
    key=None,                     # partition key (ctx) -> str
)
def on_order_created(order: dict) -> dict: ...
```

- Runs on the success path: after the wrapped callable returns, the result is published to the
  topic (outbox semantics, §2.2).
- The **context carrier is auto-attached** to outbound message headers (`traceparent`,
  `capio-*` — RFC-006 §7) so consumers reconstruct the causal chain.
- `@use.consume(topic=..., group=...)` is the *subscriber-side* mirror: it declares a function as
  the handler for a topic; the engine runs it with a reconstructed Context from the inbound
  carrier (RFC-006 §5.2).

### 2.2 Transactional outbox

For reliable publish alongside a DB write, the `publish` capability supports
`outbox=True` with a transaction-capable `db` backend (RFC-015 §3.6): the message is written in
the SAME transaction as the business data, then a dispatcher forwards it. This prevents the
dual-write problem and is the recommended pattern for order/Kafka style systems.

### 2.3 Backends

`EventBackend` (RFC-015 §3.5): Kafka, RabbitMQ, NATS, Redis Streams, SQS, Google Pub/Sub, Azure
Service Bus, plus in-process `memory` for tests. Backend failure on publish:
`degradation="bypass"` (message dropped + observed) by default, or `propagate` in strict mode;
publish is never silently successful.

## 3. Queue capability

### 3.1 API

```python
@use.queue(
    backend="redis",              # queue backend (RFC-015 §3.10)
    serializer="json",
    max_retries=3,                # consumer-side retries (RFC-017 interop)
    visibility_timeout="30s",
    delay=None,
    worker="inline",              # "inline" | "worker" | external (Celery)
    concurrency=4,
)
def process_media(item_id: str) -> None: ...
```

- `worker="inline"` (default): `@use.queue` defers execution into the backend's task queue; the
  caller gets an `enqueued` result immediately.
- `worker="worker"`: a built-in worker consumes the queue and runs the function; retry and
  circuit-breaker capabilities still apply on the consumer side (the pipeline runs on the
  worker).
- **Celery interop**: `capio-celery` exposes the same `@use.queue` surface backed by Celery,
  propagating the context carrier in task headers (RFC-013 §3.2). Capio is NOT a broker
  (RFC-001 §7.3); it composes with Celery.
- Completion of a queued function is observable via `queue.completed` events and result store.

## 4. Transaction capability

### 4.1 API

```python
@use.transaction(
    backend="sqlalchemy",         # db backend (RFC-015 §3.6)
    isolation="READ_COMMITTED",
    readonly=False,
    savepoint_on_error=True,      # attempt savepoint recovery
)
def apply_payment(payment: dict) -> Payment: ...
```

### 4.2 Semantics

- The capability opens a transaction scope around the wrapped call; the commit point is the
  successful exit of the callable, and rollback on any exception or cancellation (RFC-005
  stage 10 cleanup guarantees LIFO).
- **Nested transactions**: the engine joins the ambient transaction when one is already open
  (savepoint semantics) unless `join="new"`.
- Interactions: `retry` must sit OUTSIDE `transaction` (a rollback must not be retried as part
  of the same transaction); the retry `before_retry` hook may open a fresh transaction. Cache
  writes inside a transaction are deferred to commit (cache-aside, RFC-016 interop) to avoid
  caching rolled-back data.
- Transaction context participates in the outbox (§2.2) — publish happens at commit.

## 5. Workflow capability

### 5.1 Purpose

Model multi-step business/agent processes as composed, durable, resumable executions. Capio's
workflow is a **composition of capabilities + steps**, not a separate DAG engine.

### 5.2 API

```python
@use.workflow(
    name="onboarding",
    steps=[validate_input, enrich, write_db, send_email],
    mode="sequential",            # "sequential" | "parallel" | "conditional"
    on_failure="compensate",      # "stop" | "compensate" | "continue"
    durable=True,                 # checkpoint step results (RFC-016/018)
    max_parallel=8,
)
def onboarding(user: dict) -> dict: ...
```

- Each step is itself a Capio-decorated callable (its own pipeline, retry, timeout, audit).
- `durable=True` checkpoints each step's result so a crash resumes from the last completed step
  (snapshot/checkpoint capability, RFC-005 reuse; storage via a `db`/`cache` backend).
- `on_failure="compensate"` triggers the **Compensation** capability (§7): each completed step
  may register an undo action (`step.undo`), executed in reverse order.
- Human-in-the-loop: a step may be an `approval` step — it pauses the workflow and resumes when
  the approval event arrives (review decision from RFC-021 §4.2). This is the base mechanism for
  agent approval flows (RFC-030 §6.4).

### 5.3 LLM/agent workflows

Workflows are the natural host for **agent loops** (RFC-030 §5): an agent workflow is a
`mode="conditional"` workflow whose steps are tool calls and whose control flow is decided by the
model. Capio's durable checkpointing makes agent runs resumable and observable.

## 6. Cron / Scheduling capability

### 6.1 API

```python
@use.cron(
    schedule="0 2 * * *",         # cron expression or "every 30s"
    backend="memory",             # scheduler backend (RFC-015)
    timezone="UTC",
    max_overlap=1,                # prevent overlapping runs
    misfire_policy="skip",        # "skip" | "run_late" | "now"
)
def nightly_cleanup() -> None: ...
```

- In-process scheduler (APScheduler-compatible expression parsing) for `backend="memory"`;
  distributed scheduling (Redis, Celery beat) via backends for multi-worker correctness.
- Runs the function through a full pipeline (retry, timeout, audit, telemetry) as a synthetic
  invocation with its own Context (carrier-less; fresh IDs).

## 7. Compensation capability

### 7.1 Purpose

Saga-style reverse operations: when a workflow/transaction fails partway, run registered undo
actions in reverse order.

### 7.2 API

```python
@use.compensate(on="workflow", actions={"enrich": undo_enrich, "write_db": undo_db})
def step_enrich(user: dict) -> dict: ...
```

- Each decorated step may declare its `undo` callable. On failure, the engine executes completed
  steps' undos in reverse completion order, each through its own pipeline (with retry/timeout).
- Compensation failures are recorded and escalated (`compensation.failed` event + audit record);
  a failed compensation MUST NOT be silently ignored (RFC-020 §3.4 ethos).
- Used by: workflow `on_failure="compensate"`, transaction savepoint recovery, agent tool-rollback
  (RFC-030 §5.4), and idempotency recovery (RFC-022 §6).

## 8. Backend summary

| Capability | Backend kind (RFC-015) | Examples |
| ---------- | ---------------------- | -------- |
| publish/consume | `event` | Kafka, RabbitMQ, NATS, Redis Streams, SQS, Pub/Sub, Service Bus |
| queue | `queue` | Redis, RabbitMQ, SQS, Celery |
| transaction | `db` | SQLAlchemy, Django ORM, Peewee, Mongo, Postgres, SQLite, DuckDB |
| workflow durable | `db`/`cache` + `lock` | Postgres, Redis, SQLite |
| cron | `scheduler` backend | memory, Redis, Celery beat |

## 9. Document Dependencies

- Concepts: RFC-002; pipeline/lifecycle: RFC-005; context propagation across transports: RFC-006
  §7; hooks/events: RFC-007/008; backends: RFC-015; errors: RFC-025; async: RFC-024; AI/agents:
  RFC-030.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
