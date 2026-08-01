# RFC-015: Backend Abstraction & Backend SDK

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies the **Backend abstraction** and the **Backend SDK**: the single interface every
infrastructure integration implements, the catalog of backend kinds, the sync/async I/O contract,
the switch-without-code-changes guarantee, and the contract tests that enforce it. A backend
author reads this document (plus RFC-012/013) and implements one interface.

## 2. The Backend contract

A backend is a service behind a **backend kind** (cache, metrics, trace, log, event, db, auth,
secret, lock, queue). It is resolved by `(kind, name)` in the backend registry (RFC-014) and
injected into capabilities via the service container (RFC-010).

```python
from capio import Backend

class RedisCacheBackend(Backend):
    kind = "cache"                     # backend kind
    name = "redis"                     # resolves id cache.redis
    blocking = True                    # declared blocking I/O (RFC-024 §5)
    lazy = False                       # connect on first use instead of initialize

    # lifecycle (RFC-011 §7) — same state machine as capabilities
    def configure(self, config): ...
    def initialize(self): ...          # open connection(s) here
    def start(self): ...
    def stop(self): ...                # graceful close
    def destroy(self): ...             # hard close
```

### 2.1 What the SDK provides

- The `Backend` base class with the lifecycle skeleton.
- Kind-specific **interface protocols** (see §3) the backend must implement (enforced by contract
  tests).
- Blocking/async adaptation helpers (RFC-024 §5): a backend declaring `blocking=True` gets its
  sync calls routed through a bounded executor on the async path automatically.
- Health reporting (`health()` method; `capio doctor` reads it).

## 3. Backend kinds and interfaces

Every kind defines a minimal, backend-agnostic interface. Backends implement it; capabilities
depend only on the interface.

### 3.1 cache

```python
class CacheBackend(Backend):
    kind = "cache"
    def get(self, key: str) -> bytes | None: ...
    def set(self, key: str, value: bytes, ttl: float | None = None) -> None: ...
    def delete(self, key: str) -> None: ...
    def get_many(self, keys: list[str]) -> dict[str, bytes]: ...   # optional bulk
    def set_many(self, items: dict[str, bytes], ttl: float | None = None) -> None: ...
    def incr(self, key: str, delta: int = 1) -> int: ...
    def touch(self, key: str, ttl: float) -> bool: ...
    def flush(self, prefix: str | None = None) -> None: ...
    def health(self) -> BackendHealth: ...
```

Memory, Redis, SQLite, DiskCache, Valkey, Dragonfly, Memcached all implement `CacheBackend`
(RFC-016 defines the cache capability's use of it).

### 3.2 metrics

```python
class MetricsBackend(Backend):
    kind = "metrics"
    def counter(self, name, value=1, tags=None) -> None: ...
    def gauge(self, name, value, tags=None) -> None: ...
    def histogram(self, name, value, tags=None) -> None: ...
    def flush(self) -> None: ...          # called at lifecycle stage 11 (RFC-005)
```

Prometheus, OTel, StatsD, Datadog, NewRelic, CloudWatch implement `MetricsBackend` (RFC-019).

### 3.3 trace

```python
class TraceBackend(Backend):
    kind = "trace"
    def start_span(self, name, *, parent=None, attributes=None) -> SpanHandle: ...
    def end_span(self, handle, status=None, attributes=None) -> None: ...
    def flush(self) -> None: ...
```

OTel, Jaeger, Zipkin, Tempo, Datadog, Elastic implement `TraceBackend` (RFC-019).

### 3.4 log

```python
class LogBackend(Backend):
    kind = "log"
    def log(self, record: LogRecord) -> None: ...   # structured record
```

Stdlib, structlog, loguru, JSON, console, file, cloud logging implement `LogBackend` (RFC-020).

### 3.5 event

```python
class EventBackend(Backend):
    kind = "event"
    def publish(self, topic, payload, *, headers=None) -> None: ...
    def subscribe(self, topic, handler) -> Subscription: ...
```

Kafka, RabbitMQ, NATS, Redis Streams, SQS, Google Pub/Sub, Azure Service Bus implement
`EventBackend` (RFC-023). Note this is the *distributed* event backend; the in-process Event Bus
(RFC-008) is separate.

### 3.6 db

```python
class DbBackend(Backend):
    kind = "db"
    def execute(self, query, params=None) -> Result: ...
    def transaction(self) -> Transaction: ...
```

SQLAlchemy, Django ORM, Peewee, MongoDB, Redis, Neo4j, DuckDB, Postgres, SQLite adapters
implement `DbBackend` (RFC-023 transaction capability).

### 3.7 auth

```python
class AuthBackend(Backend):
    kind = "auth"
    def authenticate(self, carrier) -> Principal: ...
    def authorize(self, principal, resource, action) -> Decision: ...
```

JWT, OAuth/OIDC, API key, LDAP, Keycloak, Auth0, Azure AD, custom providers implement
`AuthBackend` (RFC-021).

### 3.8 secret

```python
class SecretBackend(Backend):
    kind = "secret"
    def get(self, ref: str) -> Secret: ...         # never logs the value
```

Env, vault, cloud KMS, keyring implement `SecretBackend` (RFC-026).

### 3.9 lock

```python
class LockBackend(Backend):
    kind = "lock"
    def acquire(self, name, *, timeout=None) -> LockHandle: ...
    def release(self, handle) -> None: ...
```

Memory, Redis, ZooKeeper implement `LockBackend` (RFC-024).

### 3.10 queue

```python
class QueueBackend(Backend):
    kind = "queue"
    def enqueue(self, task, *, delay=None, headers=None) -> TaskId: ...
    def dequeue(self, timeout=None) -> Task | None: ...
    def ack(self, task_id) -> None: ...
```

Redis, RabbitMQ, SQS, Celery, BullMQ-style adapters implement `QueueBackend` (RFC-023).

## 4. The switch-without-code-changes guarantee

- A capability resolves its backend by kind from the backend registry (RFC-014), never by
  importing a concrete backend (RFC-012 §7).
- Config selects the backend: `cache: {backend: redis, ...}` or `cache: {backend: memory, ...}`
  (RFC-009 §4.1). Changing the backend name is a **configuration change**, not a code change.
- Contract tests (RFC-029) run the same capability test suite against every registered backend of
  a kind, enforcing interface conformance and behavior parity.

## 5. Sync/async & blocking contract

1. A backend's public methods may be sync or async (or both). The SDK adapts: on the sync path,
   an async method is wrapped with a synchronous shim (event-loop bridge via `asyncio.run` is NOT
   allowed inside a running loop — RFC-024 §5); on the async path, a sync method declared
   `blocking=True` is dispatched to a bounded executor; `blocking=False` sync methods run inline.
2. `blocking=True` MUST be declared truthfully. A backend lying about blocking causes event-loop
   starvation, which the debug-profile blocking probe detects (RFC-024 §5) and reports as a
   plugin violation.
3. Backend calls during pipeline build/decoration are prohibited (RFC-003 §3.1); backends connect
   at `initialize` or lazily (`lazy: true`).

## 6. Backend lifecycle & health

- Backends share the capability lifecycle state machine (RFC-011 §7).
- `health()` returns `BackendHealth(status, latency_ms, detail)`; `capio doctor` aggregates it
  (RFC-028).
- Fail-safe degradation (RFC-005 §7) applies at the capability layer: a backend failure is
  observed (`backend.failed` event, RFC-008) and the capability applies its declared degradation
  policy.

## 7. Backend contract tests

Each kind ships a canonical contract-test suite (RFC-029 §5) covering: CRUD semantics, TTL/expiry,
bulk operations, error typing, concurrency safety, health reporting, and fail-safe behavior.
A backend is "compatible" only when it passes the suite for its kind against the real service.

## 8. Document Dependencies

- Concepts: RFC-002 (§4.3); SDK: RFC-012; manifest: RFC-013; registries: RFC-014; cache RFC-016;
  trace/metrics RFC-019; logging RFC-020; auth RFC-021; events/queue RFC-023; concurrency
  RFC-024; security RFC-026; tests RFC-029.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
