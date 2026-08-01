# RFC-005: Execution Lifecycle & Pipeline

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC defines the **Execution Lifecycle** (the ordered stages of one invocation) and the
**Execution Pipeline** (the ordered composition of capabilities around a callable), including
priority rules, dependency order, nested capabilities, conditional capabilities, and conflict
resolution. This is the normative ordering contract referenced by RFC-003.

## 2. Invocation Lifecycle Overview

```
Function Called
      │
      ▼
1. Context Created
      │
      ▼
2. Global Config Loaded
      │
      ▼
3. Function Config Merged
      │
      ▼
4. Plugin Resolution
      │
      ▼
5. Dependency Resolution
      │
      ▼
6. Execution Pipeline Built  (memoized; skipped on reuse)
      │
      ▼
7. Before Hooks
      │
      ▼
8. Execution (through capability steps, outermost → innermost → function → unwinding)
      │
      ▼
9. After Hooks
      │
      ▼
10. Cleanup
      │
      ▼
11. Metrics Flush
      │
      ▼
12. Trace Finish
      │
      ▼
13. Result Returned / Exception Raised
```

### 2.1 Stage-by-stage specification

**Stage 1 — Context Created.** The Context Manager allocates a Context (RFC-006). IDs are assigned
or inherited from the propagation carrier. This happens once per invocation and MUST complete
before any capability runs.

**Stage 2 — Global Config Loaded.** The Config Store provides the merged global view for the
runtime (RFC-009). This is a read of the already-resolved global layer; it does not re-read files.

**Stage 3 — Function Config Merged.** Function-level options are merged over the global view with
the precedence rules of RFC-009 §6. The merged, validated configuration is bound to the Context as
`ctx.config`.

**Stage 4 — Plugin Resolution.** Plugins whose capabilities are referenced by the pipeline are
resolved through the plugin registry (RFC-011). This stage is a no-op when the pipeline is
already memoized.

**Stage 5 — Dependency Resolution.** The Service Container resolves each capability instance's
declared dependencies (RFC-010). Failures raise `DependencyResolutionError`.

**Stage 6 — Pipeline Built (memoized).** The Pipeline Builder produces or retrieves the compiled
pipeline (RFC-004 §4.2). On reuse, this stage is skipped; the Context references the existing
pipeline.

**Stage 7 — Before Hooks.** The engine dispatches `before_execution` and capability-specific
before hooks (RFC-007 §3). A hook may short-circuit (return a result without invoking the
function) or raise.

**Stage 8 — Execution.** The engine runs the capability steps (see §3). This is the only stage
that invokes the user function.

**Stage 9 — After Hooks.** `after_execution` and capability-specific after hooks run, in the
reverse order of before hooks (RFC-007). They may transform the result or raise.

**Stage 10 — Cleanup.** The engine guarantees cleanup of every entered scope (context enter/exit,
transactions, locks, temporary resources) in strict LIFO order regardless of success or failure.
Cleanup failures are collected and observed, never substituted for the invocation's outcome,
except by explicit strict-mode configuration.

**Stage 11 — Metrics Flush.** Buffered metrics for the invocation are flushed to metric backends
(RFC-019). Flush failures degrade per fail-safe rules (RFC-002 §8.2).

**Stage 12 — Trace Finish.** The invocation span is ended; child spans are ended first (RFC-019).

**Stage 13 — Result/Exception.** The decorated callable returns the result or re-raises the
exception, exactly preserving the wrapped callable's contract (RFC-003 §9).

## 3. Execution Through the Pipeline

### 3.1 Nesting model

Capabilities compose as nested scopes. With the chained form:

```python
@use.retry(...)      # outermost scope
@use.cache(...)      # middle scope
@use.trace()         # innermost scope
def f(): ...
```

Execution enters scopes outermost-first and exits innermost-first:

```
enter retry
  enter cache
    enter trace
      f()                      # the wrapped callable
    exit trace  (after / finally)
  exit cache
exit retry
```

The outermost decorator is the one physically written highest (Python applies it last, so it
wraps everything). The **wrapped callable runs once** inside all scopes. A capability may retry
(loop) around the inner execution, but the inner execution is a single nested run, not a copy of
the pipeline.

### 3.2 Step abstraction

A compiled step wraps a "next" step. Formally:

```python
class Step(Protocol):
    def __call__(self, ctx: Context, next_step: Callable[[Context], Any]) -> Any:
        ...
```

`pipeline.steps` is ordered outermost-first. The engine executes:

```python
steps[0](ctx, lambda c: steps[1](c, ... (fn) ...))
```

This uniform shape means the sync and async engines share one step list; only the way `next_step`
is awaited differs (RFC-024). Capabilities are implemented against `run(ctx, call_next)` — they do
not manually wrap functions, so composition, introspection, and reentrancy are uniform.

## 4. Ordering

### 4.1 Sources of order

1. **Explicit chaining** (RFC-003 §3) — the physical decorator order a user wrote. This always
   wins for the capabilities the user named.
2. **Declared priority** — the composite form and any capability not explicitly ordered uses each
   capability's registered priority.
3. **Dependency constraints** — a capability declaring "must run before X" / "must run after Y"
   is honored after priority, adjusting relative order without violating explicit chaining.
4. **Registry tie-break** — for otherwise-equal priority, the manifest's declared `base_priority`
   then plugin load order (lexicographic by package name) breaks ties deterministically.

### 4.2 Default priority table (base capabilities)

Ordered outermost (first executed on the way in) to innermost (last before the function):

| Priority | Capability | Rationale |
| -------- | ---------- | --------- |
| 1000 | `auth` | Gate the call before anything else runs (RFC-021). |
| 900 | `validate` | Reject bad input before spending work (RFC-022). |
| 850 | `rate_limit` / `throttle` / `debounce` | Admission control (RFC-018). |
| 800 | `circuit_breaker` | Fail fast when the dependency is unhealthy (RFC-018). |
| 750 | `cache` | Satisfy from cache before computing (RFC-016). |
| 700 | `retry` | Wrap the fragile execution (RFC-017). |
| 650 | `timeout` | Bound the execution (RFC-018). |
| 600 | `trace` | Instrument (RFC-019). |
| 500 | `metrics` | Measure (RFC-019). |
| 0 | (function) | The wrapped callable. |

Wait — the table shows cache above retry (cache first), matching the intuition "check cache
before retrying." But many users write `@use.retry()` above `@use.cache()` to retry cache misses
too. Resolution: **explicit chaining wins** (rule 1). In the composite form, the default is
`cache` before `retry`. When both are chained explicitly, the written order governs. RFC-017 and
RFC-016 define the two canonical configurations and their trade-offs.

### 4.3 Deterministic ordering contract

For any set of capabilities, the pipeline order MUST be:

1. Fully determined by the four sources in §4.1.
2. Reproducible across processes (no dependence on hash order, random seeds, or dict ordering of
   plugin discovery beyond the deterministic tie-break).
3. Inspectable: `fn.__capio__.capabilities` lists instances outermost-first, and `capio graph`
   renders the same order (RFC-028).

### 4.4 Conflict resolution rules

When two capabilities claim an ordering constraint that contradicts an explicit chaining, the
resolution is:

1. **Explicit chaining is always preserved.** The engine never reorders what the user physically
   wrote.
2. **Priority overrides manifest constraints.** If capability A declares `before=B` but B has a
   higher priority (runs earlier), A's declaration is downgraded to a *soft hint* unless A's
   manifest marks the constraint `hard`. A hard constraint that cannot be satisfied raises
   `PipelineConflictError` at build time.
3. **Cycles in declared constraints** raise `PipelineConflictError` (graph cycle) at build time,
   never at runtime.

## 5. Capability Dependencies

A capability declares dependencies in its manifest and in code (RFC-012):

```python
class SemanticCache(Capability):
    depends_on = ["llm_cache", "serializer"]
    requires_backends = ["cache"]
```

### 5.1 Dependency semantics

- **Runtime dependency:** the named capability instance must exist in the pipeline (or be
  auto-added). Auto-add places the dependency at its own priority, outermost to the dependent.
- **Backend dependency:** a backend of the declared kind must be resolvable from the backend
  registry; the capability receives the backend handle via the container.
- **Graph dependency:** the capability graph (RFC-002 §2.3) must remain acyclic; additions are
  ordered by the rules of §4.

### 5.2 Dependency graph example

```
Semantic Cache
      ↓
  LLM Cache
      ↓
  Serializer
      ↓
  Hash Generator
      ↓
  Cache Backend
```

Each arrow is a declared dependency. If `SemanticCache` is applied, the builder auto-adds
`llm_cache`, `serializer`, and ensures a `cache` backend resolves. Missing providers raise
`DependencyResolutionError`.

## 6. Dynamic & Conditional Capabilities

### 6.1 Conditional capabilities

A capability may carry an `enable` predicate (RFC-003 §8.1):

```python
@use.retry(enable=lambda ctx: ctx.env != "dev", max_attempts=5)
```

- The predicate is evaluated per invocation against the Context at stage 7 (before hooks).
- When False, the step is a transparent pass-through: it records itself in the Context as
  `disabled` but performs no behavior and adds no measurable overhead beyond the predicate call.
- Disabled steps still appear in the pipeline (so ordering and introspection are stable).

### 6.2 Dynamic capabilities

Runtime-added capabilities (via `with_capabilities`, RFC-003 §8.2) are merged into the pipeline
at build time. If the pipeline was already built, a rebuild is triggered with a versioned
fingerprint; the rebuild is atomic — concurrent in-flight invocations keep the old pipeline until
they finish.

### 6.3 Per-invocation overrides

A limited, explicit mechanism lets callers tweak options per invocation without re-decoration:

```python
def f(ctx: CapioContext): ...   # not typical
f.with_capio(retry={"max_attempts": 2}).call(...)  # explicit escape hatch
```

Only capabilities declared overridable in their manifest accept overrides; others raise
`ConfigurationError`. This is a deliberate, narrow surface — most users never use it.

## 7. Fail-Safe Degradation in the Pipeline

### 7.1 Default (non-strict) mode

When a capability or backend fails (e.g., cache backend unreachable, metrics sink down):

1. The failure is captured as a typed exception (RFC-025).
2. An event is emitted (`capability.failed`) and the failure is recorded on the Context.
3. The capability applies its declared degradation policy (from its manifest):
   - `bypass` (default for cache/metrics/trace/log): behave as if the capability were absent for
     this invocation.
   - `retry-later` (default for retry/timeout internals): the failure itself is subject to retry.
   - `propagate` (auth, validate, circuit_breaker open): raise the typed exception.
4. The invocation continues with the inner steps or fails per the chosen policy.

### 7.2 Strict mode

With strict mode enabled (RFC-009 profile), rule 3's `bypass` becomes `propagate` for every
capability. Strict mode is opt-in per runtime or per decorated callable.

### 7.3 Contract

- Degradation must never change the wrapped callable's signature or return type.
- Degradation must be observable (event + context record) so operators can detect silent
  behavior changes.
- A capability MUST declare its degradation policy in its manifest; an undeclared policy defaults
  to `propagate` (safe choice).

## 8. Lifecycle of the Pipeline Itself

1. **built** (memoized) — created on demand at stage 6.
2. **invalidated** — on config change or dynamic capability change (RFC-009 §7); rebuilt lazily on
   next invocation with the new fingerprint.
3. **released** — on runtime stop; held pipelines are dropped and their scoped services released
   in reverse order.

A pipeline holds references only to capability instances and the memoized config; it holds no
per-invocation state, so it is safe to share across threads and tasks (RFC-004 §5.2).

## 9. Examples

### 9.1 Canonical web endpoint

```python
@use.auth(provider="oidc", scopes=["read"])
@use.validate(schema=SearchSchema)
@use.rate_limit(limit=100, window="1m")
@use.circuit_breaker(failure_threshold=5, reset_timeout="30s")
@use.cache(ttl="5m")
@use.retry(max_attempts=3, backoff="exponential")
@use.timeout(seconds=2)
@use.trace()
@use.metrics(name="search")
def search(query: str) -> list[str]:
    ...
```

Execution: auth → validate → rate_limit → circuit_breaker → cache (hit? return) → retry →
timeout → trace → metrics → function.

### 9.2 Cache-then-retry vs retry-then-cache

- `cache` outside `retry` (default composite order): cache miss computes once; retry is *inside*
  the cache scope, so a transient failure is not cached. Bad responses may be cached after
  success.
- `retry` outside `cache` (user chained `@use.retry()` above `@use.cache()`): a retried call may
  re-check cache each attempt (useful when another worker populates it).

Both are valid; the choice is explicit and documented per RFC-016/017.

## 10. Document Dependencies

- API forms: RFC-003; architecture: RFC-004; context: RFC-006; hooks: RFC-007; config: RFC-009;
  DI: RFC-010; plugin lifecycle: RFC-011; capability interface: RFC-012; errors: RFC-025;
  concurrency: RFC-024.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
