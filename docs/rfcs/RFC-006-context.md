# RFC-006: Context Object & Propagation

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies the **Context** object — the per-invocation state container — and **Context
Propagation** — how a context flows across threads, tasks, processes, and transports. Context is
the spine of the platform (RFC-001 §4.3): capabilities communicate only through it, and it is the
single carrier of identity, state, handles, and cancellation.

## 2. The Context Object

### 2.1 Core definition

```python
@dataclass(slots=True, frozen=False)
class Context:
    # identity
    request_id: str
    correlation_id: str
    parent_id: str | None
    invocation_id: str
    # input
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    fn: Callable[..., Any]                # the original wrapped callable
    fn_name: str
    fn_module: str
    cls: type | None                      # owning class for methods
    self_or_cls: Any | None               # bound instance / class
    # resolved configuration (RFC-009)
    config: Mapping[str, Any]
    profile: str
    env: str
    strict: bool
    # capability state
    capabilities: Mapping[str, "CapabilityState"]   # per-capability private state slot
    # plugin state
    plugin_state: Mapping[str, Any]
    # handles (lazily bound; RFC-010)
    logger: Any
    tracer: Any
    metrics: Any
    cache: Any
    auth: AuthPrincipal | None
    # execution control
    cancel: CancellationToken
    deadline: float | None                # monotonic absolute deadline
    # environment
    start_time: float                     # monotonic
    thread_id: int
    process_id: int
    loop: object | None                   # running asyncio loop, if async path
    # propagation
    carrier: PropagationCarrier | None    # inbound context (see §5)
    scope: "ContextScope"                 # outbound propagation (see §6)
```

Every field has a typed accessor and is documented in the API reference (RFC-031). The Context is
**mutable by capabilities within an invocation** (capability state, timing, handles) but its
identity fields and input fields are immutable after creation.

### 2.2 Field documentation

| Field | Type | Meaning |
| ----- | ---- | ------- |
| `request_id` | `str` | Identifies one logical request (may span many invocations). |
| `correlation_id` | `str` | Identifies a causal group across services (tracing correlation). |
| `invocation_id` | `str` | Uniquely identifies this single invocation; monotonic within a runtime. |
| `parent_id` | `str \| None` | Invocation or span id that spawned this one (for propagation). |
| `args` / `kwargs` | tuple/dict | The bound positional/keyword arguments passed to the decorated callable. |
| `fn` / `fn_name` / `fn_module` | … | Function identity for traces, cache keys, audit, validation. |
| `cls` / `self_or_cls` | … | For methods: the owning class and the bound instance/class. |
| `config` | `Mapping` | Fully resolved, validated configuration for this invocation (RFC-009). |
| `profile` / `env` / `strict` | … | Active profile, environment name, strict-mode flag. |
| `capabilities` | mapping | Per-capability state slots, keyed by capability instance id (RFC-012 §6). |
| `plugin_state` | mapping | Namespaced scratch space for plugins (keyed by plugin name). |
| `logger` / `tracer` / `metrics` | handles | Lazy-bound service handles (RFC-010, RFC-019/020). |
| `cache` | handle | The resolved cache backend handle (RFC-016). |
| `auth` | `AuthPrincipal \| None` | Authenticated principal after the auth capability (RFC-021). |
| `cancel` | token | Cooperative cancellation (RFC-024). |
| `deadline` | float | Monotonic deadline enforced by the timeout capability (RFC-018). |
| `start_time` | float | Monotonic start for duration measurement. |
| `thread_id` / `process_id` | int | Identity of the executing thread/process (diagnostics). |
| `loop` | object | The running asyncio loop on the async path (None on sync path). |
| `carrier` | carrier | The inbound propagation carrier this context was derived from (§5). |
| `scope` | scope | The outbound propagation scope this context is registered in (§6). |

### 2.3 Invariant rules

1. One Context per invocation; created at lifecycle stage 1 (RFC-005 §2.1).
2. Identity/input fields are set before any capability runs and MUST NOT be mutated.
3. Capabilities store their invocation state ONLY in `capabilities[name]`; never on their
   instance for per-invocation data (enables reentrancy, RFC-004 §3.5).
4. Handles are bound lazily and cached on the Context; a failed handle resolution raises
   `ContextBindingError` (RFC-025) unless the capability degrades.
5. Context is never shared between concurrent invocations; each invocation gets its own.

## 3. Context Creation

### 3.1 Algorithm

```
create_context(fn, args, kwargs, *, cls=None, self_or_cls=None, carrier=None, parent=None):
    inbound = decode(carrier) if carrier else None        # §5
    request_id = inbound.request_id or new_id("req")
    correlation_id = inbound.correlation_id or request_id
    parent_id = inbound.span_id or (parent.invocation_id if parent else None)
    invocation_id = new_id("inv")                        # monotonic
    config = config_store.resolve(fn, cls, self_or_cls)  # RFC-009
    ctx = Context(... all fields ...)
    register_scope(ctx)                                  # §6
    return ctx
```

IDs are generated with a monotonic counter plus process-random suffix, so they are unique within
a process and practically unique across processes without coordination.

### 3.2 Explicit context reuse / injection

A user may supply a carrier explicitly (e.g., an inbound HTTP header mapping) when calling a
decorated function; the runtime derives the parent chain from it (RFC-033 integration examples).

## 4. Context API (capability-facing)

Capabilities receive the context as their `run(ctx, next_step)` parameter (RFC-005 §3.2) and use
these accessors:

```python
ctx.args, ctx.kwargs            # invocation input (read-only)
ctx.result() / ctx.set_result() # result access for after-hooks / short-circuit
ctx.exception() / ctx.set_exception()
ctx.capability("retry").state   # typed per-capability state
ctx.emit(event)                 # convenience: publish event with ctx context (RFC-008)
ctx.cancel_token                 # cancellation
ctx.bind("logger", x)           # bind/replace a handle (plugins)
ctx.to_dict()                   # serializable snapshot for audit/logging (RFC-020)
```

`to_dict()` MUST exclude secrets (RFC-026): auth tokens, API keys, and `auth` handle are redacted
by default; redaction is configurable per plugin.

## 5. Context Propagation — Carriers

Propagation is the transfer of context identity/state across a boundary. The unit of transfer is
a **Carrier**: an ordered map of key→value pairs (header-like). Capio follows the W3C
Trace Context shape for its wire fields and extends it for Capio-specific state.

### 5.1 Canonical fields in a carrier

| Key | Meaning |
| --- | ------- |
| `capio-request-id` | request_id |
| `capio-correlation-id` | correlation_id |
| `traceparent` / `tracestate` | standard W3C trace identity (RFC-019) |
| `capio-parent` | parent span/invocation id |
| `capio-user-id` | authenticated user identity (subject) |
| `capio-tenant-id` | tenant scope, when multi-tenancy is active |
| `capio-flags` | bitset (e.g., `strict`, `sampled`) |

Carrier codecs are registered per transport in the serializer registry (RFC-014): HTTP header,
W3C trace context, JSON, OpenTelemetry baggage, Celery headers, Kafka headers, MCP fields, etc.

### 5.2 Carrier semantics

- **Encoding/decoding is lossy by design.** Only identity + small flags travel; heavy state
  (handles, config) never propagates.
- Decoding is forgiving: malformed or missing fields fall back to fresh IDs, never raise.
- Plugins may register additional fields via a `ContextField` extension point; unknown inbound
  fields are preserved and re-emitted when possible (forwarding).

## 6. Propagation Scopes (in-process)

In-process propagation uses **ContextVars** (PEP 567) as the single mechanism, which gives correct
behavior for asyncio tasks and thread-local fallback. A **ContextScope** is the registration of a
Context in the current execution context.

```python
class ContextScope:
    def enter(self) -> None: ...
    def exit(self) -> None: ...   # restores previous context
    @property
    def current(self) -> Context | None: ...
```

- `scope.current` returns the innermost active Context (or None outside Capio code).
- Sync threads: ContextVars are thread-local, so each thread has its own current context.
- Async tasks: ContextVars propagate correctly across `await` and child tasks created within the
  same task context (RFC-024 §4). `asyncio.create_task` inside a decorated coroutine inherits the
  parent context automatically.
- Child invocations (a decorated function calling another decorated function) read the parent's
  context via `scope.current`, producing a child context with `parent_id` set.

### 6.1 Rules for scope lifetime

1. Scope is entered at lifecycle stage 1 and exited at cleanup (stage 10), guaranteed even on
   exception.
2. Nested invocations stack: exiting a scope restores the previous context.
3. The scope is how `use.context()` injection (RFC-003 §5.4) retrieves the current Context.

## 7. Propagation Across Transports

| Transport | Mechanism |
| --------- | --------- |
| Thread | ContextVars (thread-local), automatic. |
| asyncio task | ContextVars via PEP 567, automatic for same-task awaits; explicit for new tasks. |
| Process (spawn/fork) | Serialize carrier over process boundary (pickle-safe subset) — opt-in. |
| HTTP (outbound) | Carrier codec → headers on the request. |
| HTTP (inbound) | Middleware decodes headers → carrier → context (RFC-013 integrations). |
| Celery task | `capio-celery` propagates carrier in task headers (RFC-013). |
| Kafka / NATS / RabbitMQ | Carrier in message headers (RFC-023). |
| WebSocket | Carrier in the CONNECT/initial frame. |
| MCP | Carrier in MCP request metadata fields (RFC-030, RFC-033). |
| CLI | No inbound carrier; fresh IDs; `--capio-request-id` flag MAY inject an explicit id. |

### 7.1 Async task propagation rules

1. A coroutine created inside a decorated callable inherits the current ContextVar context
   automatically (PEP 567).
2. `asyncio.create_task(...)` inside a decorated coroutine: the new task receives the parent's
   ContextVars; the child invocation inside it creates a child context. The parent does not wait
   on the child by default (fire-and-forget) and does not propagate its result.
3. Threads spawned from within a decorated callable do NOT inherit the async context; code that
   needs propagation into a thread must pass a serialized carrier explicitly (opt-in helper
   `capio.propagate.to_thread(fn, *args)`).

## 8. Context and Concurrency

### 8.1 Reentrancy

Because per-invocation state lives in the Context, the same capability instance can be re-entered
safely: each nested invocation has its own Context. Capability instances MUST NOT store
per-invocation data (RFC-004 §3.5). The engine verifies this with a contract test (RFC-029).

### 8.2 Concurrent invocations of the same callable

Each gets an independent Context; the pipeline and capability instances are shared but stateless
per invocation. Shared resources (a cache backend, a rate limiter bucket) are the backend's
responsibility to synchronize (RFC-015, RFC-016).

### 8.3 Cancellation

`ctx.cancel` is a `CancellationToken` (RFC-002 §6.5). The token is marked cancelled when:
- an `asyncio.CancelledError` propagates on the async path (the engine wraps the run and marks
  the token), or
- the timeout capability observes `ctx.deadline` exceeded (RFC-018), or
- a hook calls `ctx.cancel.cancel()` (external shutdown signal).

Capabilities MUST check the token at safe points or await cancellation-aware backends; the engine
guarantees after-hooks and cleanup run on cancellation (RFC-005 §2.1 stage 10).

## 9. Context Snapshot & Serialization

- `ctx.snapshot()` returns a frozen, redacted, JSON-serializable view (for audit, logs, and
  `capio trace`).
- Snapshot includes: IDs, fn identity, profile/env, timing (start, duration), capability list
  (names + status), and redacted input size (not raw args unless configured).
- Raw arguments are excluded by default for privacy and size; `audit` capability may opt in with
  an explicit schema (RFC-020).

## 10. Multi-Runtime Isolation

Each `CapioRuntime` (RFC-004 §5.1) owns its own Context scope storage. Isolated runtimes do not
leak contexts into each other; the default `use` facade binds to the default runtime's scope. When
an application drives multiple runtimes, context flows through the runtime bound to the current
execution context (RFC-004 §6.3).

## 11. Document Dependencies

- Concepts: RFC-002 (§3.3–3.4, §6.5); lifecycle stages: RFC-005; hooks: RFC-007; config: RFC-009;
  DI handles: RFC-010; capability state: RFC-012; backends: RFC-015; observability: RFC-019;
  audit: RFC-020; security/redaction: RFC-026; concurrency: RFC-024; integrations: RFC-013,
  RFC-033.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
