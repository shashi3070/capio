# RFC-008: Event Bus & Internal Message Bus

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies Capio's two in-process communication channels:

1. The **Event Bus** — pub/sub of immutable events describing things that happened.
2. The **Internal Message Bus** — request/reply commands between runtime components.

Both keep components decoupled (RFC-004 §7) and make the runtime itself observable (RFC-001
§4.8). The Event Bus is the *push* model; hooks (RFC-007) are the *pull* model. They coexist:
hooks are the declarative, typed, order-sensitive extension points; events are the loose,
one-to-many notification channel.

## 2. Event Bus

### 2.1 Event definition

```python
@dataclass(frozen=True, slots=True)
class Event:
    type: str                 # e.g. "retry.scheduled", "cache.hit", "plugin.loaded"
    id: str
    timestamp: float          # monotonic
    runtime: str              # owning runtime name
    scope_id: str             # invocation_id | pipeline_id | runtime_id
    payload: Mapping[str, Any]
    context_snapshot: Mapping[str, Any] | None   # redacted (RFC-006 §9)
```

- Events are immutable and append-only once created.
- `payload` MUST be JSON-serializable (no raw objects); binary data is represented by registered
  serializers or omitted. This keeps the bus simple, safe, and replayable.
- Event `type` is namespaced: `<domain>.<verb>` e.g. `retry.attempt`, `cache.miss`,
  `plugin.loaded`, `pipeline.built`, `hook.failed`.

### 2.2 Publish/subscribe

```python
# publish (component-side)
bus.publish(Event(type="cache.miss", scope_id=ctx.invocation_id, payload={...}))

# subscribe (plugin-side)
sub = bus.subscribe("cache.*", handler, *, buffer=None, filter=lambda e: ...)
sub.unsubscribe()
```

- Topic matching supports `*` (single segment) and `**` (multi segment).
- Handlers run **synchronously in the publisher's context** by default, in subscription order.
- Subscribers that are slow MUST NOT block the invocation path: default delivery is synchronous
  and bounded, but any subscriber may declare `buffer="async"` (RFC-024) to receive on a bounded
  queue in the async engine; overflow applies backpressure rules (§2.4).

### 2.3 Ordering guarantees

| Scope | Guarantee |
| ----- | --------- |
| Same publisher, same event | Handlers run in subscription order. |
| Same invocation | Events from one invocation are emitted in causal order (engine guarantees emission order at each stage). |
| Across invocations | No global ordering guarantee (concurrency); each event carries its own `timestamp` and `scope_id`. |

### 2.4 Backpressure and failure

- Synchronous delivery: if a handler raises, the bus records the failure, emits `bus.handler_failed`
  (observed), and continues to the next handler — a subscriber failure never breaks the publisher.
- Async delivery: bounded queue (default 1,000 events). On overflow, the bus applies the policy
  `drop_newest` (default), `drop_oldest`, or `block` per runtime config. Dropped events are
  counted in the `bus.dropped` metric (RFC-019).
- Event emission cost is O(subscribers) and MUST be skipped entirely (zero-cost) when no
  subscriber matches a type — implemented via a compiled match index (RFC-027).

### 2.5 Event catalogue (core)

Runtime: `runtime.created`, `runtime.started`, `runtime.stopped`, `runtime.shutdown`,
`config.changed`, `pipeline.built`, `pipeline.invalidated`.

Plugin: `plugin.discovered`, `plugin.validated`, `plugin.loaded`, `plugin.configured`,
`plugin.initialized`, `plugin.started`, `plugin.stopped`, `plugin.unloaded`, `plugin.failed`.

Capability: `<cap>.configured`, `<cap>.initialized`, `<cap>.started`, `<cap>.stopped`,
`capability.failed`, `capability.degraded`.

Invocation: `invocation.started`, `invocation.finished`, `invocation.failed`,
`invocation.cancelled`, `invocation.timed_out`.

Capability behavior: `retry.attempt`, `retry.scheduled`, `retry.exhausted`, `cache.hit`,
`cache.miss`, `cache.stored`, `cache.evicted`, `circuit.open`, `circuit.closed`,
`circuit.half_open`, `rate.limited`, `auth.denied`, `auth.granted`.

### 2.6 Subscription vs hooks

| Aspect | Hooks | Events |
| ------ | ----- | ------ |
| Model | pull (runtime calls you) | push (you observe) |
| Ordering | strict, priority-based | subscription order per type |
| Failure | typed, can affect invocation | observed, never affects publisher |
| Result | may transform/short-circuit | fire-and-forget |
| Use for | behavior modification | observation, integration, logging, replay |

## 3. Internal Message Bus

### 3.1 Purpose

The Internal Message Bus carries **commands** between runtime components (Registry → Plugin
Loader, Config Store → Pipeline Builder invalidation, etc.). It exists so components depend on the
bus interface, not on each other (RFC-004 §2.2, rule 2), enabling testability and isolation.

### 3.2 Command shape

```python
@dataclass(frozen=True, slots=True)
class Command:
    name: str                 # e.g. "registry.lookup", "plugin.reload", "config.invalidate"
    request_id: str
    payload: Mapping[str, Any]
    reply_to: str | None
```

### 3.3 Request/reply

- Commands are handled by exactly one registered handler per name (unlike events).
- Handlers return a reply payload or raise a typed exception (RFC-025), which is propagated to the
  caller.
- Delivery is synchronous by default (components are in-process); async handlers are supported on
  the async path.
- Unknown command names raise `UnknownCommandError`.
- Timeouts: a command with a declared deadline that is not handled in time raises
  `CommandTimeoutError`.

### 3.4 Component use cases

| Command | Handler | Purpose |
| ------- | ------- | ------- |
| `registry.lookup` | Registries | Resolve a capability/backend by name. |
| `plugin.resolve` | Plugin Loader | Ensure a named plugin is loaded. |
| `config.resolve` | Config Store | Resolve config for a callable (RFC-009). |
| `config.invalidate` | Config Store | Bump config fingerprint, notify pipeline builder. |
| `pipeline.invalidate` | Pipeline Builder | Invalidate memoized pipelines for a callable set. |
| `container.resolve` | Service Container | Resolve a service (lazy). |

### 3.5 Naming and namespacing

Component commands are namespaced `<component>.<verb>` and MUST be listed in a component's
documented surface (RFC-031) so plugins can rely on stability: the command surface is semver-
governed (RFC-032).

## 4. Bus Lifecycle & Multi-Runtime

- Each `CapioRuntime` owns its Event Bus and Message Bus (RFC-004 §5.1); isolated runtimes have
  isolated buses.
- The bus starts delivering when the runtime starts and stops accepting new subscriptions at
  runtime stop; `runtime.shutdown` drains pending async buffers before final delivery.
- A plugin's subscriptions and handlers are torn down at `plugin.unloaded` (RFC-011 §7),
  atomically with its registries, so unload leaves no dangling handlers.

## 5. Observability of the buses

The buses are themselves observable:

- Metrics: `bus.events_emitted`, `bus.events_dropped`, `bus.handlers_failed`, `bus.queue_depth`
  (RFC-019).
- Events: `bus.handler_failed`, `bus.queue_full`.
- The `capio doctor` / `capio trace` CLI (RFC-028) can dump recent events for debugging in debug
  profile (ring buffer, bounded, default 1,000).

## 6. Document Dependencies

- Architecture: RFC-004; hooks: RFC-007; config: RFC-009; plugins: RFC-011; observability:
  RFC-019; concurrency: RFC-024; errors: RFC-025; performance: RFC-027.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
