# RFC-017: Retry Capability

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies the **Retry capability**: retry policy, backoff strategies, jitter, retry
predicates, delay scheduling, and its interaction with other capabilities (cache, circuit
breaker, timeout, LLM). It is consumed via `@use.retry(...)` (RFC-003) and runs as the `retry`
step in the pipeline (RFC-005 §4.2).

## 2. API

```python
@use.retry(
    max_attempts=3,                    # total attempts (1 = no retry)
    delay="100ms",                     # base delay between attempts
    max_delay="10s",                   # ceiling for backoff
    backoff="exponential",             # "fixed" | "linear" | "exponential"
    multiplier=2.0,                    # growth factor for exponential/linear
    jitter=True,                       # bool | (min, max) uniform jitter
    retry_on=Exception,                # exc type(s) considered retryable
    retry_if=None,                     # (ctx, exc) -> bool predicate; overrides retry_on
    on_final=False,                    # re-raise original vs last exception (RFC-025)
    deadline=None,                     # absolute overall retry window
    max_elapsed=None,                  # max total time spent retrying
    log_every=1,                       # emit retry.attempt event every N attempts
)
def fetch(url: str) -> bytes: ...
```

Defaults mirror RFC-001's "production ready by default": exponential backoff with jitter, up to
3 attempts, retrying any `Exception` except `CapioCancelledError`-family cancellations and
`KeyboardInterrupt`/`SystemExit`.

## 3. Retry policy

### 3.1 Retryability

- `retry_on`: exception type(s) or a tuple. Default `Exception` but **always excludes**:
  `KeyboardInterrupt`, `SystemExit`, `MemoryError`, and Capio's own `CancellationError`
  (RFC-025) unless explicitly listed.
- `retry_if`: a predicate `(ctx, exc) -> bool`; when provided it fully determines
  retryability (overrides `retry_on`).
- A capability may emit `retry.forever_disable(exc)` via a hook (`before_retry`, RFC-007) to
  exclude a currently-observed exception class for the remainder of the invocation (used by
  circuit breaker integration, §6).

### 3.2 Attempt semantics

- `max_attempts=1` means: execute once, never retry (still runs through the retry step so
  metrics/events are consistent).
- Each attempt is a fresh inner execution: `call_next(ctx)` is invoked again, including
  re-entering the inner steps (cache re-check, timeout, trace).
- State across attempts: the Context is the SAME invocation (RFC-006); per-capability state on
  `ctx.capability("retry").state` persists across attempts (attempt counter, last exception);
  other capabilities' per-invocation state is NOT reset unless their manifest declares
  `resets_on_retry: true` (e.g. an idempotency guard). This is the default contract; it makes
  "the retry step wraps inner steps" observable.

### 3.3 Delay & backoff

| Strategy | Delay on attempt n (1-based) |
| -------- | ---------------------------- |
| fixed | `delay` |
| linear | `delay * n * multiplier` |
| exponential | `delay * multiplier^(n-1)` |

All capped at `max_delay`. `jitter`:
- `True` → full jitter: `random.uniform(0, computed)`.
- `(a, b)` → uniform jitter within `[a, b]` scaled to the computed delay.
- `False` → no jitter.

Jitter MUST be applied to break synchronized retry storms (thundering herd). The RNG is
runtime-scoped and seeded deterministically in the `test` profile (`delay=0`, `jitter=False`) so
tests are reproducible (RFC-009 §7).

### 3.4 Sleep mechanics

- **Sync path:** `time.sleep(delay)` on the calling thread; the thread is yielded, so this is
  cooperative with other threads.
- **Async path:** `asyncio.sleep(delay)` — never blocks the loop. If the delay is zero, the SDK
  MAY `await asyncio.sleep(0)` to yield once for fairness (config `yield_zero: true` default).
- **Sleep in sync-inside-async (blocking backend adapter):** the executor thread sleeps; the
  loop is unaffected (RFC-024 §5).

## 4. Overall deadline and max elapsed

- `deadline` (monotonic absolute) and `max_elapsed` bound the entire retry loop. When exceeded,
  retry stops and the current exception propagates (`RetryExhaustedError` wrapping the last
  exception, RFC-025).
- The timeout capability (RFC-018) bounds a single attempt; the retry deadline bounds the whole
  sequence. Interaction rule: an attempt that times out is retryable unless the timeout exception
  is excluded (default: timeout exceptions ARE retryable, so `retry` + `timeout` retries
  timeouts — configurable via `retry_on`).

## 5. Events, metrics, hooks

- Events: `retry.attempt` (payload: attempt, exc type/message, delay scheduled),
  `retry.scheduled` (a retry was scheduled), `retry.exhausted` (final failure),
  `retry.succeeded` (success after ≥2 attempts) (RFC-008 §2.5).
- Metrics: `retry.attempts_total`, `retry.failures_total`, `retry.delay_ms`,
  `retry.exhausted_total` (RFC-019).
- Hooks: `before_retry` (may mutate policy via ctx.capability("retry").state),
  `after_retry` (RFC-007 §3.2).

## 6. Interaction with other capabilities

- **Retry + circuit breaker** (RFC-018): the circuit breaker step sits OUTSIDE retry (higher
  priority). When the breaker is open it raises `CircuitOpenError` (non-retryable) BEFORE the
  retry step runs; when half-open it admits a probe attempt; the retry step's attempts each go
  through the breaker's closed path. The breaker observes retry exhaustions via `after_retry`
  hook to record failures.
- **Retry + timeout** (RFC-018): see §4. Timeout is INSIDE retry (default order: retry wraps
  timeout), so each attempt has its own timeout.
- **Retry + cache** (RFC-016): default composite order = cache outside retry (miss computes once
  under retry); explicit chaining may place retry outside cache (RFC-005 §9.2).
- **Retry + auth/validate**: retry sits inside auth/validate, so re-auth is not repeated per
  attempt (default). Users who want re-auth per attempt chain retry outermost explicitly.
- **Retry + idempotency**: retrying non-idempotent operations is dangerous; the SDK exposes
  `idempotent=True` option that emits `retry.attempt` with a warning when the wrapped function is
  not decorated with `use.idempotent()` (RFC-023), and supports `before_retry` hooks for
  compensation.
- **Retry + streaming** (RFC-012 §4): retry applies to opening the stream; a failure mid-stream
  is NOT retried (state lost). Retry over `Iterator` results requires `retry_scope="open"` (the
  default) — attempts re-call `call_next` to (re)create the iterator.

## 7. Retry + LLM

For LLM call sites (RFC-030), retry defaults should consider:
- Token/rate-limit errors (`429`, quota) retry with longer backoff and jitter.
- Content-safety/steering errors (`400`) are NOT retryable by default (deterministic).
- Network/5xx/timeouts ARE retryable.
`capio-openai` (RFC-030) publishes a `retry_if` preset (`retry_on_llm_errors=True`) encoding
these rules, demonstrable via contract tests (RFC-029).

## 8. Document Dependencies

- API: RFC-003; pipeline order: RFC-005; context: RFC-006; hooks: RFC-007; config: RFC-009;
  cache: RFC-016; breaker/timeout: RFC-018; errors: RFC-025; concurrency: RFC-024; LLM: RFC-030.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
