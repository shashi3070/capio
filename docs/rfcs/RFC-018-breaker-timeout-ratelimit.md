# RFC-018: Circuit Breaker, Timeout, Rate Limit, Throttle, Debounce

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies five closely-related execution-guard capabilities: **Circuit Breaker**,
**Timeout**, **Rate Limit**, **Throttle**, and **Debounce**. They share admission-control and
execution-bound concerns and are consumed via `@use.circuit_breaker(...)`,
`@use.timeout(...)`, `@use.rate_limit(...)`, `@use.throttle(...)`, `@use.debounce(...)`
(RFC-003).

## 2. Circuit Breaker

### 2.1 Purpose

Fail fast when the wrapped dependency (an API, a database, an LLM) is unhealthy, instead of
hammering it with requests that will fail.

### 2.2 API

```python
@use.circuit_breaker(
    failure_threshold=5,           # failures in window to open
    reset_timeout="30s",           # time before half-open probe
    success_threshold=1,           # successes to close after half-open
    window="60s",                  # rolling window for counting
    only_on=Exception,             # which exceptions count as failures
    exclude=None,                  # exceptions that never count
    record_timeouts=True,          # timeouts (RFC-018 §3) count as failures
    record_retries=False,          # retried-but-succeeded attempts count as failures
    half_open_max=1,               # concurrent probes allowed in half-open
    on_open=None, on_half_open=None, on_close=None,   # hooks/callbacks
)
def call_api(payload: dict) -> dict: ...
```

### 2.3 State machine

```
   CLOSED ──(failures ≥ threshold in window)──► OPEN
      ▲                                           │
      │            (success_threshold successes)  │ (reset_timeout elapsed)
      │                                           ▼
      └────────────────────────── CLOSED ◄────── HALF_OPEN
                                          (probe attempt)
```

| State | Behavior |
| ----- | -------- |
| CLOSED | Normal: execute; count failures. |
| OPEN | Fail fast: raise `CircuitOpenError` (RFC-025, non-retryable by default). |
| HALF_OPEN | Admit up to `half_open_max` probe attempts; success→CLOSED, failure→OPEN. |

- State is per-instance (per decorated callable) and MUST be thread/task-safe (atomic
  transitions; the SDK uses a per-instance lock/async-semaphore).
- State may be shared across instances via a distributed breaker backend (`lock`/`cache`
  backends, RFC-015) — configurable `scope="local"` (default) or `scope="distributed"`.

### 2.4 Events & metrics

- Events: `circuit.open`, `circuit.closed`, `circuit.half_open`, `circuit.rejected` (each
  rejected call in OPEN), `circuit.probe` (RFC-008 §2.5).
- Metrics: `circuit.state`, `circuit.failures_total`, `circuit.rejected_total`,
  `circuit.probes_total` (RFC-019).
- Hooks: none dedicated; uses `before_execution`/`on_exception` (RFC-007) internally, exposed as
  `on_open`/`on_half_open`/`on_close` callbacks.

### 2.5 Interaction

- With retry (RFC-017 §6): breaker outside retry; `CircuitOpenError` is non-retryable by
  default so retry never thrashes an open breaker.
- With timeout: `record_timeouts` lets timeouts trip the breaker.

## 3. Timeout

### 3.1 Purpose

Bound a single execution so a hung dependency cannot hang the caller.

### 3.2 API

```python
@use.timeout(
    seconds="2s",                  # per-invocation limit (also accepts float)
    hard=False,                    # True = OS-level kill (POSIX only); False = cooperative
    raise_on=True,                 # raise TimeoutError (RFC-025) on expiry
    return_on=None,                # instead of raising, return this sentinel (fail-safe)
)
def fetch(url: str) -> bytes: ...
```

### 3.3 Mechanism by execution kind (normative)

| Path | Mechanism |
| ---- | --------- |
| **async** | `asyncio.wait_for(call_next, timeout)`. Sets `ctx.deadline`; on expiry raises `CapioTimeoutError` and cancels the inner task; `CancelledError` is contained by the timeout step. |
| **sync, cooperative (hard=False)** | `ctx.deadline` checked at cooperative points; works with backends that respect `ctx.cancel`. If the wrapped function never yields, expiry raises only at return — documented limitation. |
| **sync, hard=True (POSIX only)** | `signal.setitimer(SIGALRM, ...)` in a dedicated thread or main thread. **Unavailable on Windows** (RFC-031 CI matrix runs Linux/macOS for hard timeout); on Windows `hard=True` falls back to cooperative and emits a warning event. |

- **Sync-inside-async** (blocking backend, RFC-024 §5): the executor future is
  `future.result(timeout=...)`; the loop is never blocked.
- `return_on` (fail-safe, RFC-005 §7): on expiry, return the sentinel and emit `timeout.handled`.
  Mutually exclusive with `raise_on=True`.
- The wrapped function's resources MUST be cleaned up on timeout: the SDK guarantees the
  cleanup scope (RFC-005 stage 10) runs; for generator callables, `aclose()`/`close()` is called.

### 3.4 Events & metrics

- Events: `timeout.fired`, `timeout.handled` (when `return_on`), `timeout.cancelled_inner`
  (RFC-008).
- Metrics: `timeout.count`, `timeout.duration_ms`, `timeout.handled_count` (RFC-019).
- Hook: `on_timeout` (RFC-007 §3.1).

## 4. Rate Limit

### 4.1 Purpose

Bound call frequency per key (user, tenant, IP, model) — admission control before work.

### 4.2 API

```python
@use.rate_limit(
    limit=100,                     # max events in window
    window="1m",                   # sliding or fixed window
    strategy="sliding",            # "fixed" | "sliding" | "token_bucket"
    bucket_capacity=100,           # token bucket: capacity
    refill_rate="100/s",           # token bucket: refill rate
    key=None,                      # (ctx) -> str key; default per-callable
    burst=False,                   # allow first-call burst in prod default
    on_exceeded="raise",           # "raise" | "wait" | "return"
)
def send_message(user_id: str, text: str) -> None: ...
```

- `key` identifies the limiting scope: default is the decorated callable; typical keys derive
  from `ctx` (e.g. `ctx.auth.principal.user_id`) for per-user limits.
- `on_exceeded`:
  - `raise` → `RateLimitExceededError` (RFC-025) with retry-after info.
  - `wait` → sleep/await until a token is available (async path: `asyncio.sleep`; sync path:
    `time.sleep`) — bounded by `max_wait` (default 5s) then raises.
  - `return` → return the configured `fallback` (fail-safe) and emit `rate.limited`.
- Distributed limits use a `lock`/`cache` backend with atomic increments (RFC-015 §3.1 `incr`);
  in-process limits use a token bucket / sliding window counter (RFC-027 benchmarks).

### 4.3 Events & metrics

- Events: `rate.limited`, `rate.window_opened` (RFC-008).
- Metrics: `rate.requests_total`, `rate.rejected_total`, `rate.current`, `rate.wait_ms` (RFC-019).

## 5. Throttle

### 5.1 Purpose

Serialize or bound concurrent execution of the same callable (concurrency limiter) — distinct
from rate limiting by time.

### 5.2 API

```python
@use.throttle(
    max_concurrent=4,              # max in-flight invocations
    wait=False,                    # True = queue; False = reject when full
    queue_size=100,                # bound on waiting invocations (when wait=True)
    timeout="30s",                 # max time to wait in queue
    on_reject="raise",             # "raise" | "return"
)
async def transcribe(audio: bytes) -> str: ...
```

- Uses a per-instance semaphore (sync `threading.Semaphore` / async `asyncio.Semaphore`).
- `on_reject="raise"` → `ConcurrencyLimitError` (RFC-025).
- Useful for bounding model concurrency (e.g. GPU/LLM provider) — see RFC-030.

## 6. Debounce

### 6.1 Purpose

Coalesce rapid repeated calls into one execution after an idle period (state-change-oriented;
use sparingly — most call sites want rate limit or throttle).

### 6.2 API

```python
@use.debounce(
    wait="200ms",                  # idle period before executing
    max_wait="2s",                 # max delay before forced execution
    leading=True,                  # execute first call immediately?
    trailing=True,                 # execute the trailing call?
    on_drop="observe",             # "observe" (default) | "raise"
)
def notify_change(changed: dict) -> None: ...
```

- Only the last arguments of a coalesced window are executed; dropped calls emit `debounce.drop`
  events with the dropped args (redacted, RFC-006 §9).
- Executes on a worker thread/task in the async engine (never blocks the loop); the result of
  coalesced calls is discarded (debounce is fire-and-forget by design) — use it only for
  side-effecting, idempotent work. The contract test enforces no return-value coupling.

## 7. Combined admission-control matrix

| Capability | Bounds | Scope | Use when |
| ---------- | ------ | ----- | -------- |
| rate_limit | events per time window | per key/callable | API quotas, per-user limits |
| throttle | in-flight concurrency | per callable | GPU/model concurrency, semaphore-style |
| debounce | call coalescing | per callable | save-on-change, UI-style events |
| circuit_breaker | dependency health | per dependency | downstream outages |
| timeout | per-invocation wall time | per callable | hung dependencies |

Ordering in the default pipeline (RFC-005 §4.2): `rate_limit/throttle/debounce` (850) →
`circuit_breaker` (800) → ... → `timeout` (650) inside `retry` (700).

## 8. Document Dependencies

- API: RFC-003; pipeline order: RFC-005; context/cancellation: RFC-006; hooks: RFC-007; config:
  RFC-009; backends (distributed guards): RFC-015; retry: RFC-017; errors: RFC-025; concurrency:
  RFC-024; LLM usage: RFC-030.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
