# Capio Usage Guide

The complete user manual for Capio v0.1.0. For how the library is built, see
[`architecture.md`](architecture.md).

---

## 1. Installation

```bash
pip install -e ".[dev]"
```

Capio has **no third-party runtime dependencies**. The `dev` extra adds `pytest`,
`ruff`, `typer` (CLI), `mypy`, and `types-setuptools`.

If your OS applies an Application Control policy that blocks pip-generated
console scripts, use the CLI via `python -m capio.cli ...` instead of `capio ...`.

---

## 2. Quick start

```python
from capio import use

@use.retry(max_attempts=3, backoff="exponential", jitter=True)
@use.cache(ttl="5m")
@use.timeout(seconds=2)
@use.trace()
def search(query: str) -> list[str]:
    ...
```

`use.<capability>(...)` is the **chained form**. Each decorator adds one
capability to the function; the decorator written highest runs **outermost**.

Async functions work with the identical API:

```python
@use.retry(max_attempts=3)
@use.cache(ttl="30s")
@use.circuit_breaker(failure_threshold=5, reset_timeout="30s")
async def fetch(url: str) -> bytes:
    ...
```

The **composite form** is equivalent for the common case and sorts capabilities
by priority automatically:

```python
@use(
    retry={"max_attempts": 3, "backoff": "exponential", "jitter": True},
    cache={"ttl": "5m"},
    timeout={"seconds": 2},
    trace=True,
)
def search2(query: str) -> list[str]:
    ...
```

Run it:

```bash
python -m capio.cli version
python -m capio.cli doctor
```

---

## 3. The `use` API

### 3.1 Chained form — `use.<name>(**options)`

One capability per decorator. Resolution is dynamic: `use.<anything>` works as
long as a capability with that name is registered.

```python
@use.rate_limit(limit=100, window="1m", strategy="sliding")
@use.log(include_args=True)
def ping() -> bool:
    return True
```

Rules (RFC-003 §3.2):

- Applying the **same capability twice** to one function raises
  `DuplicateCapabilityError` at decoration time.
- An unknown name raises `UnknownCapabilityError` immediately (the raised error
  lists the registered names).
- Order follows **physical order** (RFC-005 rule 1): the top decorator wraps
  everything below it. This *overrides* priority ordering, so write the chained
  form in the order you actually want.

### 3.2 Composite form — `use(*names, **options)`

One decorator, many capabilities:

```python
@use("retry", "cache", timeout={"seconds": 1}, trace=True)
def read(id: int) -> dict:
    ...
```

Option handling:

- `use("retry", "cache", ...)` — bare name, all defaults
- `use(retry={"max_attempts": 5}, ...)` — name = options mapping
- `use(cache=True, ...)` — shorthand for `{}`
- `use(cache=False, ...)` or `use(cache=None, ...)` — explicitly exclude

Semantics:

- Capabilities are ordered by **priority descending** (highest priority runs
  outermost). The priority table:

  | Capability | Priority | Outer position |
  |---|---|---|
  | rate_limit | 850 | 1st (outermost) |
  | circuit_breaker | 800 | 2nd |
  | cache | 750 | 3rd |
  | retry | 700 | 4th |
  | timeout | 650 | 5th |
  | trace | 600 | 6th |
  | log | 550 | 7th |
  | metrics | 500 | 8th (innermost) |

- The composite form **cannot** decorate a function that already has `__capio__`
  metadata — that raises `ConflictingPipelineError`. Chaining an extra decorator
  on top of a composite is also rejected.

### 3.3 Shared option — `enable`

Every capability accepts `enable`, a `Callable[[Context], bool]` that gates the
capability **per call** (evaluated inside the pipeline):

```python
@use.cache(ttl="30s", enable=lambda ctx: ctx.env == "prod")
@use.retry(max_attempts=3, enable=lambda ctx: not ctx.strict)
def db_query(sql: str) -> list:
    ...
```

When `enable` returns `False`, the capability is bypassed for that invocation
(the wrapped callable still runs). Useful for feature flags, debug mode, and
benchmarking pass-through pipelines.

### 3.4 Injecting the Context — `use.context()`

Any decorator/wrapper can receive the live per-invocation `Context` via keyword:

```python
from capio import use
from capio.context import current_context

@use.context()
def handler(ctx):
    # ctx.invocation_id, ctx.trace_id, ctx.request_id, ctx.correlation_id,
    # ctx.env, ctx.profile, ctx.strict, ctx.fn_name, ctx.fn_module, ...
    return ctx.invocation_id

@use.cache(ttl="1m")
@use.context()
async def handler_async(ctx) -> str:
    return ctx.request_id
```

`current_context()` returns the same `Context` outside decorators while inside a
pipeline (e.g. inside a custom capability).

### 3.5 Introspection — `unwrap`, `pipeline`

```python
from capio import unwrap, pipeline

original = unwrap(decorated_fn)   # the innermost user function
pipe = pipeline(decorated_fn)     # the built ExecutionPipeline (lazy: builds now)
pipe.steps                         # [(name, capability, cfg), ...] outermost-first
pipe.kind                          # "sync" | "async" | "sync_gen" | "async_gen"
```

`unwrap` follows `__wrapped__` and stops at capio context-injection wrappers so
the injected function stays inside the executed pipeline.

### 3.6 One-off decoration — `with_capabilities`

```python
from capio import with_capabilities

fn = with_capabilities(my_func, retry={"max_attempts": 2}, timeout=True)
```

Equivalent to `use(retry={...}, timeout=True)(my_func)`.

---

## 4. Capability reference (every option)

Durations accept a float (seconds) **or** a string with a unit: `"100ms"`,
`"2s"`, `"5m"`, `"1.5h"`.

### 4.1 `use.retry` — RFC-017

Retries the wrapped callable on failure with backoff and jitter.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `max_attempts` | int ≥ 1 | `3` | Total attempts (including the first) |
| `delay` | duration | `"100ms"` | Base delay between attempts |
| `max_delay` | duration \| None | `None` | Cap on the computed delay (`None` = uncapped) |
| `backoff` | `"fixed" \| "linear" \| "exponential"` | `"exponential"` | Growth curve: `delay`, `delay*n*mult`, `delay*mult^(n-1)` |
| `multiplier` | number ≥ 1 | `2.0` | Growth factor for linear/exponential |
| `jitter` | bool \| tuple `(a, b)` | `True` | `True` = full jitter `U(0, base)`; `(a, b)` = `U(a, b) * base`; `False` = none |
| `retry_on` | type \| tuple[type] \| None | `None` | Exception types to retry (default: `Exception`) |
| `retry_if` | `(ctx, exc) -> bool` \| None | `None` | Predicate; when set it decides, overriding `retry_on` |
| `on_final` | `"wrap" \| "reraise_original"` | `"wrap"` | `"wrap"` raises `RetryExhaustedError` (chained); `"reraise_original"` re-raises the first failure |
| `max_elapsed` | duration \| None | `None` | Global time budget; stop retrying past it |
| `log_every` | int ≥ 1 | `1` | Emit `retry.attempt` every N attempts |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.retry(
    max_attempts=4,
    delay="200ms",
    backoff="exponential",
    multiplier=2.0,
    jitter=(0.5, 1.5),
    retry_on=(ConnectionError, TimeoutError),
    on_final="reraise_original",
    max_elapsed="10s",
    log_every=2,
)
def fetch_data() -> bytes:
    ...
```

Never retried (hardcoded): `KeyboardInterrupt`, `SystemExit`,
`asyncio.CancelledError`, and capio cancellations. **Explicitly** listing a
non-retryable type in `retry_on` overrides the hardcoded table.

### 4.2 `use.cache` — RFC-016

Caches results with TTL and stampede protection. Default backend `cache.memory`.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `ttl` | duration \| None | `None` | Expiry; `None` = never expires |
| `key` | `"auto"` \| str \| callable \| None | `"auto"` | Key builder: named registered builder, or `(ctx, args, kwargs) -> str` |
| `key_prefix` | str \| None | `None` | Prefix when no `namespace` is given |
| `namespace` | str \| callable \| None | `None` | Namespace segment; defaults to `key_prefix` or `f"{module}.{name}"` |
| `key_scope` | `"class" \| "instance"` | `"class"` | `"instance"` appends `@<id>` so instances don't share keys |
| `backend` | str | `"cache.memory"` | Backend service name |
| `tags` | dict \| None | `None` | Static tags stored with the entry |
| `cache_when` | `(ctx, result) -> bool` \| None | `None` | Predicate deciding whether to store a result |
| `cache_on_error` | bool | `False` | Store a raised exception and replay it (default: errors never cached) |
| `stampede` | `"none" \| "singleflight" \| "probabilistic"` | `"probabilistic"` | `"probabilistic"` randomizes TTL × `U(0.8, 1.0)`; `"singleflight"` coalesces concurrent misses |
| `maxsize` | int ≥ 1 \| None | `None` | Backend eviction cap (in-process memory backend) |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

`None` results, `False`, `0`, etc. are cached correctly — a stored `None` is
distinguishable from a miss.

```python
from capio.capabilities.cache import register_key_builder

@register_key_builder("tenant")
def tenant_key(ctx, args, kwargs) -> str:
    return f"tenant:{ctx.env}:{args[0]}"

@use.cache(key="tenant", ttl="5m", stampede="singleflight", cache_when=lambda ctx, r: r is not None)
def get_profile(user_id: str) -> dict | None:
    ...
```

`register_key_builder(name, fn)` also works as a plain call (not just a
decorator).

**Cache behavior on backend failure is fail-safe**: the invocation proceeds
un-cached (`cache.failed` event). Under `strict` mode the same failure raises
`BackendUnavailableError`.

### 4.3 `use.timeout` — RFC-018 §3

Bounds execution time.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `seconds` | duration | `"2s"` | Time budget |
| `hard` | bool | `False` | Async: always real (`asyncio.wait_for`). Sync: not available — emits `timeout.warning` and falls back to cooperative |
| `raise_on` | bool | `True` | Raise `CapioTimeoutError` on expiry |
| `return_on` | any \| None | `None` | When set, return this sentinel instead of raising |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

Rules:

- `return_on` + `raise_on=True` is rejected at pipeline build
  (`ConfigurationError`, RFC-018 §3.2).
- **Sync** is *cooperative*: `ctx.deadline` is set, and expiry is only observed
  after the callable returns. It cannot interrupt a blocking call.
- **Async** is a hard timeout and cancels the underlying task.

```python
@use.timeout(seconds="500ms", raise_on=True)
def sync_call() -> int: ...            # cooperative deadline

@use.timeout(seconds=2, return_on=None)
async def async_call() -> int: ...     # hard timeout, returns None on expiry
```

### 4.4 `use.circuit_breaker` — RFC-018 §2

Fails fast when a dependency is unhealthy. State machine
`closed → open → half_open → closed`, **state is per-decorated-function**.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `failure_threshold` | int ≥ 1 | `5` | Failures within `window` that open the circuit |
| `reset_timeout` | duration | `"30s"` | How long the circuit stays open before probing |
| `success_threshold` | int ≥ 1 | `1` | Successes in half-open that close the circuit |
| `window` | duration | `"60s"` | Sliding window for counting failures |
| `only_on` | type \| tuple[type] \| None | `None` | Only count these exception types (default: any `Exception`) |
| `exclude` | type \| tuple[type] \| None | `None` | Never count these exception types |
| `record_timeouts` | bool | `True` | Count `CapioTimeoutError` as failures |
| `half_open_max` | int ≥ 1 | `1` | Max concurrent probes while half-open |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.circuit_breaker(
    failure_threshold=5,
    reset_timeout="30s",
    window="60s",
    only_on=requests.exceptions.RequestException,
    half_open_max=2,
)
def call_api() -> dict:
    ...
```

When the circuit is open, calls raise `CircuitOpenError` immediately
(`circuit.rejected` event). Cancellations never count as failures.

### 4.5 `use.rate_limit` — RFC-018 §4

Bounds call frequency per key.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `limit` | int ≥ 1 | `100` | Max calls per `window` (or bucket capacity) |
| `window` | duration | `"1m"` | Fixed/sliding window length |
| `strategy` | `"fixed" \| "sliding" \| "token_bucket"` | `"sliding"` | Windowing algorithm |
| `bucket_capacity` | int ≥ 1 \| None | `None` | Token bucket max tokens (defaults to `limit`) |
| `refill_rate` | number \| `"N/s"` \| `"N/1m"` | `"100/s"` | Token bucket refill rate |
| `key` | str \| `(ctx) -> str` \| None | `None` | Per-key limiter; default keys by `module.name` |
| `on_exceeded` | `"raise" \| "wait" \| "return"` | `"raise"` | Action when the limit is exceeded |
| `max_wait` | duration \| None | `"5s"` | For `"wait"`: give up after this (then raise) |
| `fallback` | any \| None | `None` | For `"return"`: value to return |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.rate_limit(limit=10, window="1s", strategy="token_bucket", refill_rate="10/s",
                on_exceeded="wait", max_wait="2s")
def tick() -> None:
    ...

@use.rate_limit(limit=5, window="1m", on_exceeded="return", fallback=None,
                key=lambda ctx: ctx.request_id)
def throttled() -> dict | None:
    ...
```

`"wait"` retries after `retry_after` (bounded by `max_wait`); exceeding the wait
raises `RateLimitExceededError` (carrying `retry_after`).

### 4.6 `use.trace` — RFC-019 §2

Records a span per invocation. Always best-effort — exporter failures never
propagate.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `name` | str \| `"auto"` \| None | `"auto"` | Span name; auto = `module.fn` |
| `attributes` | dict \| None | `None` | Static span attributes |
| `attributes_from` | `(ctx) -> dict` \| None | `None` | Dynamic attributes from context |
| `capture_args` | bool | `False` | Include arg count / kwarg keys |
| `capture_result` | bool | `False` | Include result type |
| `backend` | str | `"trace.console"` | Backend service name |
| `span_kind` | str | `"internal"` | OpenTelemetry-style kind |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.trace(name="search", attributes={"service": "api"}, capture_result=True)
def search(q: str) -> list[str]:
    ...
```

Spans carry `trace_id`, `span_id`, `parent_span_id` (so nested decorated calls
form a tree), `fn`, `kind`, `status` (`ok`/`error`), `error`, `duration_ms`,
and `attributes`. The console backend prints one JSON object per span.

### 4.7 `use.metrics` — RFC-019 §3

Records a call counter and a duration histogram per invocation. Fail-safe.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `name` | str \| `"auto"` \| None | `"auto"` | Metric name prefix; auto = `module.fn` |
| `counter` | bool | `True` | Emit `<prefix>.calls_total` counter |
| `tags` | dict \| None | `None` | Static metric tags |
| `tags_from` | `(ctx) -> dict` \| None | `None` | Dynamic tags |
| `record_duration` | bool | `True` | Emit `<prefix>.duration_ms` histogram |
| `record_result` | bool | `True` | Tag the counter with `outcome` (reserved) |
| `backend` | str | `"metrics.null"` | Backend service name |
| `per_instance` | bool | `False` | Tag with `instance=<id>` when on a class instance |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.metrics(name="orders.create", tags={"team": "checkout"}, per_instance=True)
def create_order(...) -> ...:
    ...
```

The default `metrics.null` backend keeps records in memory and exposes
`.records` / `.snapshot()` — useful for tests and smoke checks.

### 4.8 `use.log` — RFC-020 §2

Emits a structured invocation record through a backend. Fail-safe (backend
errors become `log.failed` events).

| Option | Type | Default | Meaning |
|---|---|---|---|
| `logger_name` | str \| `"auto"` \| None | `"auto"` | Backend logger/channel name |
| `level` | str | `"INFO"` | Backend record level (DEBUG/INFO/WARN/WARNING/ERROR/CRITICAL) |
| `on_success` | str | `"INFO"` | Level for successful invocations |
| `on_error` | str | `"WARNING"` | Level for failed invocations |
| `include_args` | bool | `False` | Include arg count / kwarg keys |
| `include_result` | bool | `False` | Include result type |
| `include_duration` | bool | `True` | Include duration in ms |
| `backend` | str | `"log.stdio"` | Backend service name |
| `message` | str \| None | `None` | Custom message (default `"capio invocation"`) |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.log(include_args=True, on_success="INFO", on_error="ERROR")
def process(job_id: str) -> dict:
    ...
```

Records include `invocation_id`, `request_id`, `correlation_id`, `fn`, `outcome`,
`duration_ms`, optional arg/result metadata, and `error` on failure.

---

## 5. Backends

Backends are services bound in the runtime's container by name. Capabilities
look them up via their `backend` option. Bound by default:

| Name | Backend | Used by |
|---|---|---|
| `cache.memory` | `MemoryCacheBackend` — thread-safe dict with TTL, `get(key, default)`, `set(key, value, ttl)`, size eviction | `cache` |
| `trace.console` | `ConsoleTraceBackend` — prints one JSON object per span | `trace` |
| `metrics.null` | `NullMetricsBackend` — in-memory records + `snapshot()` | `metrics` |
| `log.stdio` | `StdioLogBackend` — structured records via the `logging` facade | `log` |

Swap or add a backend:

```python
from capio import CapioRuntime
from capio.backends.memory_cache import MemoryCacheBackend

class MyCache(MemoryCacheBackend):
    def set(self, key, value, ttl=None):
        ...  # hook into Redis etc.

runtime = CapioRuntime("my")
runtime.bind_backend("cache.memory", MyCache())   # rebind is an error
# use bind_replace to override:
runtime.services.bind_replace("cache.memory", MyCache())

@use.cache(ttl="1m", backend="cache.memory")     # uses MyCache on this runtime
```

To pin decorators to a custom runtime (not the module-level `default_runtime`),
create the runtime first, decorate after:

```python
runtime = CapioRuntime("prod", config={"env": "prod", "strict": True})
from capio import use
use_rt = use.__class__(runtime)                   # new facade bound to this runtime
```

---

## 6. Runtime configuration

```python
from capio import CapioRuntime, default_runtime

runtime = CapioRuntime(
    name="api",
    config={
        "env": "prod",
        "profile": "default",
        "strict": False,
    },
)
```

| Key | Default | Meaning |
|---|---|---|
| `env` | `"dev"` | Environment label, readable via `ctx.env` |
| `profile` | `"default"` | Configuration profile, `ctx.profile` |
| `strict` | `False` | `True` turns fail-safe degradation (cache/trace/metrics/log) into hard errors |

Environment variables (overlay on top of `config`):

- `CAPIO_ENV`
- `CAPIO_PROFILE`
- `CAPIO_STRICT` (`"1"`/`"true"` → `True`)

Lifecycle:

```python
runtime.start()      # start all built pipelines
runtime.stop()       # reverse-order stop
runtime.shutdown()   # stop + clear the pipeline cache
```

Each runtime is independent: config, backends, event bus, and memoized
pipelines.

---

## 7. Custom capabilities

Extend `Capability`, implement `run`, register it, and use it like a built-in.

```python
from capio import Capability, use
from capio.registry import registry

class Audit(Capability):
    name = "audit"
    version = "1.0.0"
    description = "Logs every invocation"
    schema = {
        "include": {"type": "any", "default": True},
        "enable": {"type": "any", "default": None},
    }
    priority = 550

    def run(self, ctx, call_next):
        result = call_next(ctx)
        if self.cfg.include:
            print("audit:", ctx.fn_name, result)
        return result

registry.register(Audit)

@use.audit(include=True)
def my_fn() -> int:
    return 1
```

For async-aware capabilities (you `await` the inner call), override
`run_async`. The contract (RFC-012 §2–3):

- `run(ctx, call_next)` must call `call_next(ctx)` exactly once (or loop) and
  return the inner result.
- `configure(config)` receives the validated `FrozenConfig` (unknown option keys
  raise `ConfigurationError`).
- `initialize(services)` gives access to backends via `self.backend(name)`.
- `start()` / `stop()` / `destroy()` hook lifecycle.
- `supports` limits which execution kinds can use the capability
  (`("sync", "async", "sync_gen", "async_gen")`).
- `degradation = "bypass" | "propagate" | "retry-later"` declares the
  failure-safety contract.

---

## 8. Error model

`capio.exceptions` provides a full tree rooted at `CapabilityException`, with
structured attributes (`capability`, `code`, `extra`).

| Error | Raised when |
|---|---|
| `ConfigurationError` | Bad/inconsistent options (e.g. `return_on` + `raise_on`) |
| `UnknownCapabilityError` | `use.<name>` for an unregistered name |
| `DuplicateCapabilityError` | Same capability applied twice in a chain |
| `ConflictingPipelineError` | Composite form applied to a decorated function |
| `UnsupportedExecutionKindError` | Capability can't handle `async`/generator kind |
| `RetryExhaustedError` | Retries exhausted (`on_final="wrap"`) |
| `CapioTimeoutError` | Timeout expired (`raise_on=True`) |
| `CircuitOpenError` | Circuit open / probe limit reached |
| `RateLimitExceededError` | Rate limit exceeded (with `retry_after`) |
| `CacheKeyError` | Key builder unknown / returned a non-str |
| `BackendUnavailableError` | Strict mode + backend missing/failed |
| `ServiceAlreadyBound` | Rebinding a backend without `bind_replace` |
| `NameCollisionError` | Registering a different class under a taken name |
| `CapioCancelledBase` | Cancellation (`CapioTimeoutError` parent) |

**Cancellations are `BaseException`** — `except Exception` in your code will not
swallow timeouts, and retry/breaker never retry them.

---

## 9. Events

Subscribe through the runtime event bus:

```python
from capio import default_runtime
from capio.events import Event

rt = default_runtime()

def on_cache_hit(event: Event):
    print("cache hit", event.data.get("key"))

rt.event_bus.subscribe("cache.hit", on_cache_hit)   # or "*" for everything
```

Emitted names: `retry.attempt/succeeded/exhausted`, `cache.hit/miss/stored/
failed`, `circuit.open/half_open/closed/rejected`, `timeout.warning/fired/
handled`, `rate.limited`, `trace.exporter_failed`, `metrics.exporter_failed`,
`log.failed`.

---

## 10. CLI

Use `python -m capio.cli ...` if the `capio` console script is blocked.

```bash
python -m capio.cli version
python -m capio.cli doctor            # env, backends, capabilities, smoke invocation
python -m capio.cli inspect MODULE.FN # pipeline metadata (priority/version/options)
python -m capio.cli graph MODULE.FN   # render order: name -> name -> ... -> function
python -m capio.cli benchmark         # RFC-027 micro-benchmarks
python -m capio.cli benchmark --enforce   # exit non-zero if any budget FAILs
```

`MODULE.FN` may be a dotted path (e.g. `examples.quickstart.fetch_user`). Note
the benchmark budgets target RFC-027 reference CI hardware; a `FAIL` on a
1-capability pipeline on a slower machine is expected and does not affect
`--enforce` unless you deliberately require it.

---

## 11. Sync + async + generators

The engine classifies the callable and dispatches automatically:

- `def f(...)` → `"sync"`
- `async def f(...)` → `"async"`
- `def gen(...)` with `yield` → `"sync_gen"`
- `async def agen(...)` with `yield` → `"async_gen"`

Most capabilities are generators-safe (they wrap the iterator). A capability
that doesn't support a given kind raises `UnsupportedExecutionKindError` at
decoration time — not at call time.

---

## 12. Full example

```python
from capio import use
from capio import default_runtime

@use.rate_limit(limit=50, window="1m", on_exceeded="raise")
@use.circuit_breaker(failure_threshold=5, reset_timeout="30s")
@use.cache(ttl="1m", stampede="singleflight")
@use.retry(max_attempts=3, backoff="exponential", jitter=True)
@use.timeout(seconds=2)
@use.trace(name="users.get")
@use.metrics(name="users.get")
@use.log(include_args=True)
def get_user(user_id: int) -> dict:
    return {...}

get_user(1)   # rate_limit -> breaker -> cache -> retry -> timeout -> trace -> metrics -> log -> fn
```

See `examples/quickstart.py` for a runnable demo (verified with
`capio inspect` / `capio graph`).
