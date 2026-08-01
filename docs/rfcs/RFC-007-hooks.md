# RFC-007: Hook System

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies the **Hook System**: the complete catalogue of named, typed extension points,
their signatures, their invocation ordering, failure semantics, and the lifecycle diagrams that
relate them to the Execution Lifecycle (RFC-005). Hooks are the *pull* model — the runtime calls
registered callbacks at fixed stages — complementary to the Event Bus's *push* model (RFC-008).

## 2. Hook Fundamentals

### 2.1 Definition

A **Hook** is a fixed, named stage in the execution or capability lifecycle at which the runtime
invokes registered callbacks. A callback is a callable bound to a hook name, with a declared
priority and an owner (a capability or plugin).

```python
@dataclass(frozen=True)
class HookRegistration:
    hook: str
    owner: str                      # capability or plugin name
    callback: Callable[..., Any]
    priority: int                   # lower runs first
    kind: Literal["sync", "async", "either"]
    affects_result: bool            # True: may transform result or short-circuit
```

### 2.2 Registration

- Capabilities register hooks at **configure** time (RFC-011 state machine) via
  `runtime.hooks.register(...)` or declaratively in the capability class
  (RFC-012 `hooks = {...}`).
- The registry is per-runtime; registrations are validated against the hook table.
- A callback may be unregistered at **stopped** time; unload must remove all of a plugin's
  registrations atomically (RFC-011 §7).

### 2.3 Invocation model

- Hooks run **in the caller's execution context** (sync thread or async task) with the current
  Context bound (RFC-006 §6).
- Order within a hook stage: ascending priority; ties broken by registration order (stable).
- Before-hooks run ascending priority; after/error hooks run **descending priority** (mirror),
  matching the enter/exit nesting of RFC-005 §3.
- A hook may be async; on the async path the engine awaits it; on the sync path an async hook
  raises `AsyncHookOnSyncPathError` (RFC-025) at registration or dispatch time (never silently).

### 2.4 Failure semantics per hook kind

| Hook kind | On callback exception |
| --------- | --------------------- |
| `observe` | Exception is caught, recorded on the Context, and emitted as an event; other hooks in the stage continue. Never affects the invocation. |
| `affect` | Exception propagates as a typed `HookError` (RFC-025); remaining hooks in the stage are skipped; the invocation fails unless the hook's capability degrades per RFC-005 §7. |
| `short-circuit` | Returning a result stops the invocation before the function runs (before-hooks only). |

Every hook declares one of these three kinds in the hook table.

## 3. Hook Catalogue

### 3.1 Execution hooks (per invocation)

| Hook | Kind | Runs | Signature (ctx) | Can short-circuit |
| ---- | ---- | ---- | --------------- | ----------------- |
| `before_execution` | affect | stage 7, ascending priority | `(ctx) -> None \| Result` | Yes |
| `after_execution` | affect | stage 9, descending priority | `(ctx, result) -> Result` | No (transform only) |
| `on_exception` | observe | exception path, after stage 8 unwind | `(ctx, exc) -> None` | No |
| `on_success` | observe | after after_execution, no exception | `(ctx, result) -> None` | No |
| `on_timeout` | observe | when timeout capability fires | `(ctx, exc) -> None` | No |
| `on_cancel` | observe | when invocation is cancelled | `(ctx) -> None` | No |
| `on_shutdown` / `on_startup` | observe | runtime lifecycle (RFC-011) | `(ctx_or_runtime)` | No |

### 3.2 Capability-specific hooks

| Hook | Owner | Kind | Purpose |
| ---- | ----- | ---- | ------- |
| `before_retry` | retry | observe | Deciding whether to retry; may mutate retry policy via ctx.capability("retry"). |
| `after_retry` | retry | observe | After a retry attempt completes (success or exhaustion). |
| `before_cache_lookup` | cache | affect | Customize key/namespace; may short-circuit with a value. |
| `after_cache_lookup` | cache | affect | Inspect/transform a hit value; may convert miss→hit. |
| `before_cache_store` | cache | affect | Decide whether/how to store the result; may suppress storage. |
| `before_auth` | auth | affect | Pre-auth policy hook; may short-circuit deny. |
| `after_auth` | auth | observe | Post-auth; records principal. |
| `before_trace` | trace | observe | Customize span attributes. |
| `after_trace` | trace | observe | Finalize span attributes/status. |
| `before_metrics` | metrics | observe | Customize metric names/tags. |
| `after_metrics` | metrics | observe | Post-flush observation. |

### 3.3 Lifecycle hooks (per plugin/capability/backend)

| Hook | Fires | Purpose |
| ---- | ----- | ------- |
| `on_plugin_load` | after plugin validate, before configure | Prepare plugin state. |
| `on_plugin_unload` | reverse of load | Release plugin resources. |
| `on_capability_configure` | at configure transition | Validate/transform resolved config. |
| `on_capability_initialize` | at initialize transition | Acquire backends/resources. |
| `on_capability_start` | at start transition | Begin background work. |
| `on_capability_stop` / `destroy` | stop/destroy | Tear down. |

## 4. Lifecycle Diagram

```
                    Invocation
                        │
                        ▼
      ┌────── before_execution  (affect; ascending priority) ──────┐
      │                │                                           │
      │                ▼                                           │
      │      capability steps  (RFC-005 §3)                        │
      │                │                                           │
      │                ▼                                           │
      │          wrapped function                                 │
      │                │                                           │
      │      ┌─────────┴───────────┐                               │
      │   exception                result                         │
      │      │                      │                             │
      │      ▼                      ▼                             │
      │  on_exception          after_execution                    │
      │  (observe,             (affect, transform)                │
      │   descending)               │                             │
      │                              ▼                            │
      │                         on_success                       │
      └────────────────────────────────────────────────────────────┘
                              cleanup  (RFC-005 stage 10)
                                      │
                                      ▼
                          metrics flush → trace finish → return
```

Cancellation and timeout paths: `on_timeout`/`on_cancel` fire during cleanup ordering; the engine
guarantees all three observe-hooks run even on failure/cancellation (RFC-005 §2.1 stage 10).

## 5. Failure and Ordering Rules (normative)

1. Hooks MUST NOT call the engine or other hooks directly; they interact via the Context and by
   returning values.
2. A short-circuit result from a before-hook: the invocation returns that result; stage 8 (the
   function) does not run; after-hooks still run with the short-circuited result.
3. Transform hooks (`after_execution`, `before_cache_lookup`, etc.) MUST return a value; returning
   `None` where a result is expected is treated as a hook bug and raises `HookContractError`
   unless the hook is declared optional.
4. Observe-hook exceptions are never fatal (rule in §2.4); they are recorded via
   `ctx.capability("<owner>").add_observation(exc)` and observable via `capio inspect`.
5. Async hooks on the sync path are rejected at registration with `AsyncHookOnSyncPathError`;
   sync hooks on the async path are allowed (they run inline) but must not block the loop
   (RFC-024 §5 — enforced by blocking-probe in debug mode).
6. Hook ordering is deterministic and reproducible across processes (same registration order,
   same priorities).

## 6. Hook Execution Contract

A callback signature is always one of:

- `(ctx) -> None | Result` — execution hooks
- `(ctx, value) -> value` — transform hooks
- `(ctx, exc) -> None` — error/cancel hooks
- `(runtime) -> None` — runtime lifecycle hooks

Callbacks are wrapped by the dispatcher, which: binds the Context scope, enforces the sync/async
kind, applies the failure semantics, and emits hook events on the Event Bus
(`hook.invoked`, `hook.failed`) for observability.

## 7. Priority Reference

Default priorities for base capability hooks (lower = earlier):

| Capability | before-hook | after-hook |
| ---------- | ----------- | ---------- |
| auth | 1000 | 1000 |
| validate | 900 | 900 |
| rate_limit | 850 | 850 |
| circuit_breaker | 800 | 800 |
| cache | 750 | 750 |
| retry | 700 | 700 |
| timeout | 650 | 650 |
| trace | 600 | 600 |
| metrics | 500 | 500 |

These mirror the pipeline priority table (RFC-005 §4.2). Before-hooks run in this order; after
and error hooks run in reverse.

## 8. Document Dependencies

- Lifecycle stages: RFC-005; context: RFC-006; events: RFC-008; capability lifecycle: RFC-011;
  capability interface: RFC-012; errors: RFC-025; observability: RFC-019.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
