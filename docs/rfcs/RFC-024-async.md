# RFC-024: Async Architecture & Concurrency Model

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies Capio's concurrency model: how sync and async invocations are executed, how
the two engines share one pipeline, how blocking I/O is handled without starving event loops,
thread/process/task placement, cancellation, and the guarantees that make "write once, run both"
true (RFC-001 §3.3, RFC-012 §3.2).

## 2. Two engines, one pipeline

A compiled pipeline (RFC-005 §3.2) is a list of steps of the shape
`step(ctx, next_step)`. Both engines execute the SAME steps; they differ only in how they
advance `next_step`:

- **Sync engine**: calls `next_step(ctx)` directly. Returns a plain value.
- **Async engine**: awaits `call_next(ctx)`, producing an awaitable step chain. Returns a
  coroutine-compatible result (a `Coroutine`).

```python
# sync
def run_sync(steps, ctx):
    fn = ctx.fn
    for step in reversed(steps):
        fn = partial(step, ctx)          # building the chain
    return steps[0](ctx, inner)          # enter outermost
```

The mode of an invocation is decided by **the wrapped callable's kind plus the capability kinds**
(RFC-003 §6): a `def` decorated only with sync-capable capabilities → sync path; an `async def`
or any async-capable capability in the pipeline → async path. A sync invocation NEVER enters the
event loop; an async invocation NEVER blocks it.

## 3. Execution-kind compatibility

| Wrapped kind | Capability kinds in pipeline | Result |
| ------------ | ---------------------------- | ------ |
| `def` | all sync | sync path; plain return |
| `def` | has async-only capability | **rejected at decoration** (RFC-003 §6.2) |
| `async def` | all sync | async path; capability sync `run` executed inline (must not block) |
| `async def` | any async | async path; awaited chain |
| generator | all sync | sync streaming (RFC-012 §4) |
| async generator | any | async streaming |

## 4. ContextVars & propagation in the async model

- **ContextVars (PEP 567)** are the single in-process propagation mechanism (RFC-006 §6).
- `asyncio` tasks created inside a decorated coroutine inherit the parent ContextVars
  automatically; a child invocation creates its own child Context but sees `scope.current`.
- `asyncio.create_task` for fire-and-forget work: child task inherits parent context;
  completion is NOT awaited (RFC-006 §7.1).
- The engine guarantees scope enter/exit symmetry across `await` boundaries, including on
  `CancelledError`.
- **Threads**: spawned threads do NOT inherit the async context; propagation into threads is
  explicit via `capio.propagate.to_thread` (carrier serialization) (RFC-006 §7.1).

## 5. Blocking I/O policy

### 5.1 The rule

A capability or backend that performs blocking I/O MUST declare `blocking=True` in its manifest
(RFC-015 §5). On the async path, the runtime dispatches blocking calls to a **bounded thread-pool
executor** (default size = `min(32, os.cpu_count()+4)`, configurable in RFC-009); the event loop
is never blocked.

### 5.2 Async-on-sync (reverse adapter)

An async-only backend/capability on the sync path is bridged with an event-loop shim
(`asyncio.run` in a worker thread) — the sync caller blocks the calling thread only, and it is
an explicit, documented pattern. `asyncio.run` is NEVER called inside a running loop
(`RuntimeError` guard).

### 5.3 Blocking probe (debug profile)

In the `debug` profile (RFC-009 §7), a watchdog measures how long the event loop is held between
yields; a declared-non-blocking call that holds the loop beyond `blocking_threshold` (default
50ms) triggers `concurrency.loop_blocked` and a plugin violation report (RFC-011 §9.1). This
turns loop-starvation bugs into diagnosable events.

## 6. Threads, processes, tasks — placement rules

| Resource | Placement |
| -------- | --------- |
| Capability instance | per-runtime; MUST be reentrant (RFC-004 §3.5); sync across threads via instance locks or stateless design |
| Invocation state | per-Context; never shared |
| Scoped services | per propagation scope (RFC-010 §6) |
| Blocking backend I/O | executor pool (async) / calling thread (sync) |
| Process work (CPU-bound) | opt-in executor (`process=True` on a capability); worker results serialized (RFC-015 §3.1); no shared state |
| Event delivery | caller context by default; bounded async queue (RFC-008 §2.4) |

### 6.1 Hybrid model

A capability may declare `concurrency="hybrid"` to use threads AND async for different
operations (e.g. a DB client with both sync and async drivers). The SDK adapts per path; the
`blocking` flag governs the async path only.

## 7. Cancellation & timeouts

- `ctx.cancel` (RFC-006 §8.3) is the cooperative token: checked at safe points; async awaits
  are cancellation-aware.
- Timeout (RFC-018 §3.3): async uses `asyncio.wait_for`; sync cooperative uses deadline checks;
  sync hard mode uses SIGALRM (POSIX only).
- The engine guarantees after-hooks + cleanup on ANY exit path — success, exception, or
  cancellation (RFC-005 stage 10) — including running `aclose()` on async generators and
  `close()` on generators (RFC-012 §4.1).
- `asyncio.CancelledError`/`GeneratorExit` are never swallowed by the engine; they propagate
  after cleanup.

## 8. Trio & curio (future)

The core engine is asyncio-native. Trio/curio support is a **transport adapter** concern, not a
second engine: the step list is loop-agnostic, and an adapter would map await semantics.
Explicitly out of scope for v1; listed in RFC-032 roadmap.

## 9. Concurrency contract tests

RFC-029 enforces: reentrancy under concurrent invocation; no event-loop blocking by
declared-non-blocking capabilities; scope enter/exit symmetry under cancellation; async/sync
equivalence of results; executor starvation behavior.

## 10. Document Dependencies

- Principles: RFC-001; execution kinds: RFC-003 §3.3/§6; pipeline steps: RFC-005; context
  propagation: RFC-006; events backpressure: RFC-008; blocking backends: RFC-015 §5; timeout:
  RFC-018 §3; errors: RFC-025; performance: RFC-027; tests: RFC-029.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
