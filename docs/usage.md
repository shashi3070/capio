# Capio Usage Guide

The complete user manual for Capio v1.0.0. For how the library is built, see
[`architecture.md`](architecture.md). For a runnable example per capability, see
the [capability cookbook](cookbook.md).

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
  outermost). Priority of all 37 built-ins:

  | Capability | Priority |
  |---|---|
  | rate_limit | 850 |
  | throttle | 840 |
  | debounce | 830 |
  | circuit_breaker | 800 |
  | audit | 760 |
  | cache | 750 |
  | auth | 710 |
  | retry | 700 |
  | validate | 700 |
  | encrypt | 690 |
  | serialize | 685 |
  | mask | 680 |
  | dedup | 670 |
  | publish | 660 |
  | consume | 650 |
  | timeout | 650 |
  | queue | 640 |
  | transaction | 630 |
  | workflow | 620 |
  | cron | 610 |
  | compensate | 600 |
  | trace | 600 |
  | idempotent | 590 |
  | log | 550 |
  | metrics | 500 |
  | guardrails | 480 |
  | token_budget | 470 |
  | model_router | 460 |
  | prompt_cache | 450 |
  | semantic_cache | 440 |
  | llm_cache | 430 |
  | memory | 420 |
  | rag | 410 |
  | ingest | 405 |
  | tool | 402 |
  | agent | 401 |
  | llm | 400 |

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

### 4.9 `use.throttle` — RFC-018 §5

Bounds the number of **concurrent in-flight** calls (admission control).

| Option | Type | Default | Meaning |
|---|---|---|---|
| `limit` | int ≥ 1 | `100` | Max simultaneous in-flight calls |
| `strategy` | `"block" \| "reject"` | `"block"` | `"block"` waits (bounded by `timeout`); `"reject"` raises immediately |
| `timeout` | duration \| None | `None` | Max wait for a slot when `strategy="block"` |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.throttle(limit=10, strategy="block", timeout="5s")
def process_batch(items: list) -> int:
    ...
```

When no slot can be acquired the call raises `ConcurrencyLimitError`
(`throttle.rejected` event).

### 4.10 `use.debounce` — RFC-018 §6

Coalesces rapid calls within a quiet window into a single execution.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `window` | duration | `"200ms"` | Quiet window between resets |
| `leading` | bool | `False` | Execute immediately on the first call in a burst |
| `trailing` | bool | `True` | Execute once the quiet window elapses (as a timer) |
| `key` | str \| `(ctx) -> str` \| None | `None` | Per-key debouncer; default `module.name` |
| `drop_value` | any \| None | `None` | Value returned for coalesced calls |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.debounce(window="300ms", leading=False, trailing=True)
def persist(changes: list) -> None:
    ...
```

Coalesced calls return `drop_value` immediately (`debounce.scheduled` /
`debounce.dropped`); the real call runs once per quiet window
(`debounce.executed`). Pending timers are cancelled on runtime stop.

### 4.11 `use.audit` — RFC-020 §4

Records auditable actions to an append-only, tamper-evident trail.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `backend` | str | `"audit.memory"` | Audit backend service name |
| `action` | str \| `(ctx) -> str` \| None | `None` | Action label (default `fn_name`) |
| `resource` | str \| `(ctx) -> str` \| None | `None` | Target resource (default `module.name`) |
| `actor` | str \| `(ctx) -> str` \| None | `None` | Actor performing the action |
| `include_payload` | bool | `False` | Include arg count / kwarg keys |
| `strict` | bool | `False` | Raise if the backend is missing |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.audit(action="order.cancel", actor=lambda ctx: ctx.carrier.get("user"))
def cancel_order(order_id: str) -> dict:
    ...
```

Records carry `invocation_id`, `request_id`, `trace_id`, `actor`, `action`,
`resource`, `outcome`, and `duration_ms`. The `audit.memory` backend exposes
`query(actor=..., action=..., limit=...)` and `verify()` (recomputes the SHA-256
hash chain). Write failures degrade to `audit.failed` events; `strict` makes
them raise.

### 4.12 `use.auth` — RFC-020 §3

Authenticates the caller and enforces scopes and policy.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `provider` | `(ctx) -> dict \| None` \| None | `None` | Resolves the identity (subject + scopes) |
| `required` | bool | `True` | `False` allows anonymous calls |
| `scopes` | str \| list \| `(ctx) -> list` \| None | `None` | Required scopes |
| `policy` | `(identity, ctx) -> bool` \| None | `None` | Extra authorization predicate |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.auth(
    provider=lambda ctx: {"subject": "alice", "scopes": ["orders:read"]},
    scopes=["orders:read"],
)
def get_order(order_id: str) -> dict:
    ...
```

The resolved identity is written to `ctx.auth` so inner capabilities and the
callable can read it. Failures raise `AuthenticationError`,
`AuthorizationError`, or `PolicyEvaluationError` (`auth.denied` event).

### 4.13 `use.validate` — RFC-022 §3

Schema-based input/output validation with compact field specs.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `input` | dict \| None | `None` | `{field: spec}` checked against args/kwargs |
| `output` | any \| None | `None` | Spec checked against the result |
| `strict` | bool | `True` | (reserved for future policy tightening) |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

A field spec supports `type` (`"string"/"int"/"float"/"number"/"bool"/"list"/"dict"`),
`required`, `min`/`max`, `min_length`/`max_length`, `email`, `regex`, `enum`,
`one_of`, or a `(value) -> bool` callable.

```python
@use.validate(
    input={"user_id": {"type": "string", "min_length": 3}, "age": {"type": "int", "min": 0}},
    output={"type": "dict"},
)
def profile(user_id: str, age: int) -> dict:
    ...
```

Violations raise `ValidationError` (`validate.failed` event).

### 4.14 `use.encrypt` — RFC-022 §4

Encrypts sensitive string fields before the call (and can decrypt result fields).

| Option | Type | Default | Meaning |
|---|---|---|---|
| `key` | str \| bytes \| `(ctx) -> str` \| None | `None` | Secret; derived via `pbkdf2_hmac` |
| `fields` | str \| list \| None | `None` | Kwarg names to encrypt |
| `envelope` | str \| list \| None | `None` | Kwarg names whose dict values to encrypt |
| `decrypt_fields` | str \| list \| None | `None` | Result dict keys to decrypt |
| `strict` | bool | `True` | Raise `EncryptionKeyError` when no key is configured |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.encrypt(fields=["ssn"], envelope=["card"])
def submit(ssn: str, card: dict) -> dict:
    ...
```

Dependency-free stream cipher: an HMAC-SHA256 keystream XORs the UTF-8 payload
(12-byte random nonce + ciphertext, base64). Helpers `encrypt_string` /
`decrypt_string` are exported from `capio.capabilities.encrypt`. If no `key`
is configured, an `env.encryption_key` backend (`CAPIO_ENCRYPTION_KEY`) is
consulted; in non-strict mode a deterministic ephemeral key is used.

### 4.15 `use.mask` — RFC-022 §5

Redacts sensitive fields from calls, results, and nested lists.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `fields` | str \| list \| None | `None` | Field names to mask |
| `mask` | str | `"*"` | Mask character |
| `keep` | int ≥ 0 | `0` | Leading characters to keep unmasked |
| `mode` | `"args" \| "result" \| "both"` | `"both"` | Where to apply |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.mask(fields=["password", "token"], keep=2, mode="args")
def login(username: str, password: str) -> dict:
    ...
```

### 4.16 `use.dedup` — RFC-022 §6

Returns a single result for identical concurrent or recent calls.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `key` | str \| `(ctx) -> str` \| None | `None` | Identity; default hashes `module.name + args` |
| `backend` | str | `"cache.memory"` | Backend used to store completed results |
| `ttl` | duration \| None | `None` | How long completed results are reused |
| `wait_timeout` | duration \| None | `"10s"` | How long a waiter waits for the in-flight call |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.dedup(ttl="5s")
def expensive(query: str) -> dict:
    ...
```

Identical in-flight calls wait for the leader; identical completed calls within
`ttl` return the stored result. Events: `dedup.hit`, `dedup.waiting`,
`dedup.miss`. Fail-safe: a missing backend degrades to plain invocation.

### 4.17 `use.publish` — RFC-023 §2

Publishes the invocation payload to a topic after the call.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `topic` | str \| `(ctx) -> str` \| None | `None` | Topic name (default `fn_name`) |
| `broker` | str | `"broker.memory"` | Broker backend service name |
| `outbox` | str \| None | `None` | Outbox backend to fall back to on broker failure |
| `group` | str | `"default"` | Consumer group label |
| `include_result` | bool | `False` | Include the call result in the payload |
| `strict` | bool | `False` | Raise if no broker is bound |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.publish(topic="orders.created", include_result=True)
def create_order(items: list) -> dict:
    ...
```

On broker failure the payload is written to the outbox (`publish.outboxed`); if
no outbox is configured it falls back to the `store.memory` outbox namespace.
Missing broker + non-strict = fail-safe pass-through (`publish.missing`).

### 4.18 `use.consume` — RFC-023 §3

Dispatches the next message for a topic to the wrapped handler.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `topic` | str \| `(ctx) -> str` \| None | `None` | Topic to consume (default `fn_name`) |
| `broker` | str | `"broker.memory"` | Broker backend service name |
| `group` | str | `"default"` | Consumer group |
| `arg` | str | `"message"` | Kwarg key the message is injected as |
| `block` | bool | `False` | (reserved) |
| `timeout` | duration \| None | `None` | (reserved for blocking waits) |
| `skip_value` | any \| None | `None` | Returned when no message is available |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.consume(topic="orders.new", group="workers")
def handle_order(message, ctx):
    return process(message["payload"])
```

Messages are consumed per (topic, group) cursor and injected as
`kwargs[arg]`. Empty or missing broker returns `skip_value`.

### 4.19 `use.queue` — RFC-023 §4

Enqueues tasks onto a queue or processes them as a worker.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `mode` | `"enqueue" \| "worker"` | `"enqueue"` | Enqueue the call, or dequeue and process |
| `queue` | str | `"default"` | Queue/task name |
| `backend` | str | `"queue.memory"` | Queue backend service name |
| `task` | str \| `(ctx) -> str` \| None | `None` | Task name (default `queue`) |
| `wait` | duration \| None | `"200ms"` | Worker poll timeout |
| `skip_value` | any \| None | `None` | Returned when the queue is empty |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.queue(mode="enqueue", queue="emails")
def send_email(to: str, body: str) -> dict:
    ...                        # returns the envelope {"id", "task", ...}

@use.queue(mode="worker", queue="emails")
def process_email(task, ctx) -> None:
    ...                        # "task" is the envelope
```

### 4.20 `use.transaction` — RFC-023 §5

Runs participants with commit/rollback semantics.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `actions` | dict \| callable \| None | `None` | `{name: {"commit": fn, "rollback": fn}}` participants |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.transaction(actions={
    "ledger": {"commit": lambda ctx: write_ledger(), "rollback": lambda ctx: undo_ledger()},
    "notify": {"commit": lambda ctx: send_notification()},
})
def transfer(amount: int) -> None:
    ...
```

On success, commits run in declaration order; on failure, rollbacks run in
reverse order (best-effort — a failing rollback only emits
`transaction.rollback_failed`). Failures raise `TransactionError`
(`transaction.rolled_back` event).

### 4.21 `use.workflow` — RFC-023 §6

Runs ordered steps against a shared state dict, with per-step retry and recovery.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `steps` | callable \| list[callable] \| None | `None` | Steps invoked as `step(ctx, state)` |
| `state` | dict \| None | `None` | Initial shared state |
| `max_attempts` | int ≥ 1 | `1` | Retries per step |
| `recover` | `(ctx, state, exc) -> any` \| None | `None` | Recovery hook when a step exhausts retries |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.workflow(
    steps=[prepare, stage, publish],
    max_attempts=3,
    recover=lambda ctx, state, exc: mark_failed(state),
)
def deploy() -> dict:
    ...
```

Returns the final `state` dict. A step that fails past `max_attempts` raises
`WorkflowError` (after `recover` runs). Events: `workflow.started`,
`workflow.step`, `workflow.step_failed`, `workflow.completed`.

### 4.22 `use.cron` — RFC-023 §7

Runs the invocation only when the cron schedule is due.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `schedule` | str | `"* * * * *"` | 5-field cron, 6-field cron (with seconds), or `"every <n>[smhd]"` |
| `backend` | str | `"store.memory"` | (reserved) last-run storage |
| `skip_value` | any \| None | `None` | Returned when the schedule is not due |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.cron(schedule="*/5 * * * *", skip_value=None)
def ping() -> None:
    ...
```

Not a clock — it gates calls: when a call arrives outside the schedule it
returns `skip_value` (`cron.skipped`); when due it runs (`cron.fired`). The
`every <n>[smhd]` form enforces a minimum interval between runs.

### 4.23 `use.compensate` — RFC-023 §8

Runs best-effort compensation and finalization actions.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `actions` | callable \| list[callable] \| None | `None` | Compensation handlers `(ctx, exc)` |
| `finalize` | callable \| list[callable] \| None | `None` | Always-run handlers `(ctx, exc)` |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.compensate(
    actions=[lambda ctx, exc: refund(ctx)],
    finalize=[lambda ctx, exc: close_session(ctx)],
)
def purchase(item_id: str) -> dict:
    ...
```

On failure, `actions` run (best-effort — each failure becomes a
`compensate.failed` event), then the original error is re-raised; `finalize`
actions always run. Events: `compensate.started`, `compensate.executed`.

### 4.24 `use.idempotent` — RFC-023 §9

Enforces idempotency keys so replays return the stored result.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `header` | str | `"Idempotency-Key"` | Carrier header read when `key` is not set |
| `key` | str \| `(ctx) -> str` \| None | `None` | Explicit idempotency key |
| `backend` | str | `"store.memory"` | KV store backend (`store.memory`) |
| `ttl` | duration | `"24h"` | How long the stored result is valid |
| `replay` | `"return" \| "error"` | `"return"` | `"error"` raises on a replay of the same request |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.idempotent(key=lambda ctx: ctx.carrier.get("Idempotency-Key"))
def create_payment(amount: int) -> dict:
    ...
```

Each request is hashed (`module.name + args`); a stored entry with the **same**
hash returns the stored result (`idempotent.replay`), a **different** hash
raises `IdempotencyConflictError` (`idempotent.conflict`). Per-key locking
coalesces concurrent calls.

### 4.25 `use.llm` — RFC-030 §2

Structured boundary around a model provider call.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `provider` | `(request) -> response` \| None | `None` | In-process provider; without it the wrapped callable is the provider |
| `model` | str | `"auto"` | Model name injected into kwargs when not already present |
| `temperature` | number \| None | `None` | Injected into kwargs when not present |
| `max_tokens` | int \| None | `None` | Injected into kwargs when not present |
| `fallback` | any \| `(ctx) -> any` \| None | `None` | Value returned on provider failure |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.llm(model="demo", temperature=0.0)
@use.context()
def chat(messages, model, ctx):
    # your model call goes here; ctx.capability("llm")["state"]["response"]
    # holds the response for inner capabilities (agent, llm_cache, ...)
    return {"text": "hello"}

@use.llm(provider=lambda req: {"text": "echo"}, model="demo")
def echo(messages): ...
```

Provider failures emit `llm.failed` and raise `ProviderError` unless `fallback`
is set (callables receive `ctx`). The response is stored in
`ctx.capability("llm")["state"]["response"]` so inner AI capabilities can
inspect it.

### 4.26 `use.llm_cache` — RFC-030 §3

Exact-match cache keyed on the full request signature.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `backend` | str | `"cache.memory"` | Cache backend service name |
| `ttl` | duration \| None | `None` | Result lifetime |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.llm_cache(ttl="10m")
@use.llm(provider=lambda req: {"text": "hello"})
def chat(messages):
    ...
```

Identical requests return the cached response without calling the model
(`llm_cache.hit`). Fail-safe: a missing backend degrades to pass-through.

### 4.27 `use.semantic_cache` — RFC-030 §4

Cache keyed on **embedding similarity** rather than exact input.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `backend` | str | `"cache.memory"` | Cache backend service name |
| `embedder` | `(text) -> list[float]` \| None | `None` | Text → vector; without it the cache is bypassed |
| `threshold` | number 0–1 | `0.9` | Cosine similarity cutoff for a hit |
| `max_entries` | int ≥ 1 | `1000` | Eviction cap for the vector index |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
def embed(text: str) -> list[float]:
    ...   # char-bigram hash embedding works for smoke tests

@use.semantic_cache(embedder=embed, threshold=0.85)
@use.llm(provider=lambda req: {"text": "hi"})
def answer(query):
    ...
```

Near-duplicate queries reuse the stored response (`semantic_cache.hit`). No
embedder → fail-safe pass-through.

### 4.28 `use.prompt_cache` — RFC-030 §5

Adds provider cache-control markers to prompt blocks and tracks reuse.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `backend` | str | `"cache.memory"` | Cache backend used to track seen prefixes |
| `block_last` | bool | `True` | Mark the last message as cacheable |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.prompt_cache()
@use.llm(provider=lambda req: {"text": "hi"})
def chat(messages):
    ...
```

Adds `cache_control: {"type": "ephemeral"}` to the final message block and
emits `prompt_cache.hit` / `prompt_cache.miss` with a prefix hash.

### 4.29 `use.memory` — RFC-030 §6

Retrieves relevant conversational memories and stores each exchange.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `kind` | `"conversation" \| "episodic" \| "semantic"` | `"conversation"` | Memory kind label |
| `top_k` | int ≥ 0 | `5` | Memories injected; `0` = all |
| `store` | str | `"store.memory"` | KV store backend |
| `namespace` | str | `"memory"` | Store namespace |
| `embedder` | `(text) -> list[float]` \| None | `None` | Semantic ranking; without it, most-recent-first |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.memory(top_k=5, embedder=embed)
@use.context()
def chat(input, memories, ctx):
    ...
```

Retrieved memories are injected as `kwargs["memories"]` (`memory.retrieved`);
the input/output pair is stored per call (`memory.stored`).

### 4.30 `use.rag` — RFC-030 §7

Retrieves context documents and injects them into the prompt.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `retriever` | `(query) -> list` \| None | `None` | Custom retriever; default reads the store namespace |
| `top_k` | int ≥ 0 | `4` | Documents injected; `0` = all |
| `store` | str | `"store.memory"` | KV store backend (default retrieval) |
| `namespace` | str | `"rag"` | Store namespace |
| `context_key` | str | `"context"` | Kwarg key the documents are injected as |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.rag(top_k=4)
@use.context()
def answer(query, context, ctx):
    ...
```

Documents are injected as `kwargs["context"]` (`rag.retrieved`). Pair with
`use.ingest` to populate the namespace.

### 4.31 `use.ingest` — RFC-030 §8

Chunks documents and indexes them into the store (populates `rag`).

| Option | Type | Default | Meaning |
|---|---|---|---|
| `chunk_size` | int ≥ 1 | `512` | Characters per chunk |
| `overlap` | int ≥ 0 | `64` | Overlap between consecutive chunks |
| `embedder` | `(text) -> list[float]` \| None | `None` | Optional per-chunk embedding |
| `store` | str | `"store.memory"` | KV store backend |
| `namespace` | str | `"rag"` | Store namespace |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.ingest(chunk_size=256, overlap=32)
def load_documents() -> list[str]:
    return [open(path).read() for path in paths]
```

The wrapped callable must return an iterable of documents (str or
`{"text"|"content"|"document"}` dicts). Returns `{"stored": n}` chunks
(`ingest.stored`).

### 4.32 `use.tool` — RFC-030 §9

Exposes the wrapped function as a callable model tool with a JSON schema.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `name` | str \| None | `None` | Tool name (default `fn_name`) |
| `description` | str | `""` | Tool description |
| `parameters` | dict \| `(ctx) -> dict` \| None | `None` | Explicit JSON schema; default = inferred from the signature |
| `requires_approval` | bool | `False` | Approval flag |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.tool(name="multiply", description="multiply two ints")
@use.context()
def multiply(a: int, b: int, ctx):
    return a * b
```

The registered tool (name + description + inferred JSON schema) is published to
`ctx.capability("tool")["state"]` (`tool.registered`). The `parameters` dict is
used verbatim; a callable receives `ctx`.

### 4.33 `use.agent` — RFC-030 §10

Drives a tool-calling loop around the wrapped model step.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `tools` | dict[str, callable] \| list[callable] \| None | `None` | Tools the agent may invoke |
| `max_steps` | int ≥ 1 | `10` | Max loop iterations |
| `final_detector` | `(response, ctx) -> bool` \| None | `None` | Custom "final answer" detector; default = no `tool_calls` |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.agent(tools={"multiply": multiply_fn}, max_steps=5)
@use.llm(provider=lambda req: respond_to_tool_calls(req))
def run(messages):
    ...
```

The wrapped step must return dicts with `tool_calls` (`{"name", "arguments"}`)
and/or a final text response. Tool results are appended as `tool` messages
(`agent.tool_call`, `agent.tool_missing`). Returns `{"response", "steps"}`.

### 4.34 `use.guardrails` — RFC-030 §11

Applies input/output safety checks around the model call.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `input` | `(text, ctx) -> bool` \| None | `None` | Input check; `False` blocks the call |
| `output` | `(text, ctx) -> bool` \| None | `None` | Output check; `False` raises after the call |
| `message` | str | `"guardrail violation"` | Error message |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.guardrails(
    input=lambda text, ctx: "hack" not in text,
    output=lambda text, ctx: len(text) < 2000,
)
@use.llm(provider=lambda req: {"text": "ok"})
def chat(messages):
    ...
```

Violations raise `GuardrailError` (`guardrails.violated`). `GuardrailError` is
non-retryable by default.

### 4.35 `use.token_budget` — RFC-030 §12

Bounds input tokens for model calls.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `budget` | int ≥ 1 | `1000` | Max input tokens |
| `counter` | `(text) -> int` \| None | `None` | Token counter (default: word/punct heuristic) |
| `on_exceeded` | `"raise" \| "trim"` | `"raise"` | `"raise"` errors; `"trim"` truncates the input |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.token_budget(budget=200, on_exceeded="trim")
@use.llm(provider=lambda req: {"text": "hi"})
def chat(messages):
    ...
```

Over budget raises `TokenBudgetExceededError` (carrying `used` and `budget`,
non-retryable) or rewrites `kwargs["input"]` to a trimmed prefix
(`token_budget.exceeded`).

### 4.36 `use.model_router` — RFC-030 §13

Routes requests to a model based on rules.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `routes` | list[dict] \| dict \| None | `None` | `[{"when": (ctx)->bool, "model": str}]` or `{predicate: model}` |
| `fallback` | str | `"auto"` | Model when no route matches |
| `router` | `(ctx) -> str` \| None | `None` | Custom router, overrides `routes` |
| `key` | str | `"model"` | Kwarg key the chosen model is written to |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.model_router(
    routes=[{"when": lambda ctx: ctx.carrier.get("tier") == "premium", "model": "gpt-4o"}],
    fallback="gpt-4o-mini",
)
@use.llm(model="auto")
def chat(messages):
    ...
```

The selected model is injected into `kwargs["model"]` unless already set
(`model_router.selected`).

### 4.37 `use.serialize` — RFC-022 §3

Encodes call inputs and decodes call outputs at a boundary (cache, queue/events,
RPC, persistence). Safe by default: `json`; the `pickle` codec is registered but
requires `trust=True` (RFC-026 §7).

| Option | Type | Default | Meaning |
|---|---|---|---|
| `serializer` | str | `"json"` | Codec name; built-ins `json` (safe) and `pickle` (unsafe) |
| `mode` | str | `"both"` | `"in"` encode kwargs before the call, `"out"` decode the result, `"both"` |
| `fields` | list[str] \| str \| None | `None` | Kwarg fields to encode (`None` = every kwarg) |
| `trust` | bool | `False` | Allow unsafe serializers such as `pickle` |
| `enable` | `(ctx) -> bool` \| None | `None` | Per-call gate (see §3.3) |

```python
@use.serialize(fields=["payload"])                 # JSON-encode payload before the call
@use.context()
def store(payload, ctx):
    assert isinstance(payload, str)                # already encoded
    return "ok"

@use.serialize(mode="out")                         # JSON-decode the result
def load():
    return '{"ok": true}'
```

The serializer registry is available directly for use inside custom backends and
capabilities:

```python
import capio.serialize

encoded = capio.serialize.encode({"a": 1}, "json")     # '{"a": 1}'
decoded = capio.serialize.decode(encoded, "json")      # {"a": 1}
capio.serialize.register_serializer("msgpack", encode=..., decode=...)
```

Failures raise `SerializationError` (non-retryable). Unsafe codecs raise
`ConfigurationError` at first call unless `trust=True`.

---

## 5. Backends

Backends are services bound in the runtime's container by name. Capabilities
look them up via their `backend` option. Bound by default:

| Name | Backend | Used by |
|---|---|---|
| `cache.memory` | `MemoryCacheBackend` — thread-safe dict with TTL, `get(key, default)`, `set(key, value, ttl)`, size eviction | `cache`, `dedup`, `llm_cache`, `semantic_cache`, `prompt_cache` |
| `trace.console` | `ConsoleTraceBackend` — prints one JSON object per span | `trace` |
| `metrics.null` | `NullMetricsBackend` — in-memory records + `snapshot()` | `metrics` |
| `log.stdio` | `StdioLogBackend` — structured records via the `logging` facade | `log` |
| `audit.memory` | `InMemoryAuditBackend` — append-only, SHA-256 hash-chained trail, `query(actor=, action=, limit=)`, `verify()` | `audit` |
| `store.memory` | `InMemoryStore` — namespaced KV with TTL + per-namespace sequence, `put/get/delete/items/scan/sequence/clear` | `idempotent`, `memory`, `rag`, `ingest`, `cron`, `publish` (outbox) |
| `broker.memory` | `InMemoryBroker` — pub/sub with per-group cursors, `publish/consume/peek/size/clear` | `publish`, `consume` |
| `queue.memory` | `InMemoryTaskQueue` — FIFO with envelopes + optional workers, `put/get/task_done/start_workers/stop_workers` | `queue` |

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
|---|---|---|
| `ConfigurationError` | Bad/inconsistent options (e.g. `return_on` + `raise_on`) |
| `UnknownCapabilityError` | `use.<name>` for an unregistered name |
| `DuplicateCapabilityError` | Same capability applied twice in a chain |
| `ConflictingPipelineError` | Composite form applied to a decorated function |
| `UnsupportedExecutionKindError` | Capability can't handle `async`/generator kind |
| `RetryExhaustedError` | Retries exhausted (`on_final="wrap"`) |
| `CapioTimeoutError` | Timeout expired (`raise_on=True`) |
| `CircuitOpenError` | Circuit open / probe limit reached |
| `RateLimitExceededError` | Rate limit exceeded (with `retry_after`) |
| `ConcurrencyLimitError` | Throttle slot not acquired (with `retry_after`) |
| `CacheKeyError` | Key builder unknown / returned a non-str |
| `BackendUnavailableError` | Strict mode + backend missing/failed |
| `ServiceAlreadyBound` | Rebinding a backend without `bind_replace` |
| `NameCollisionError` | Registering a different class under a taken name |
| `AuthenticationError` | `auth` with no identity and `required=True` |
| `AuthorizationError` | `auth` missing a required scope |
| `PolicyEvaluationError` | `auth` policy predicate returned `False` |
| `ValidationError` | `validate` input/output spec violation |
| `SerializationError` | `serialize` codec failed (unknown/can't encode/decode) |
| `EncryptionKeyError` | `encrypt` with no key in strict mode |
| `TransactionError` | `transaction` participant failed (with rollback) |
| `WorkflowError` | `workflow` step exhausted retries / `recover` failed |
| `IdempotencyConflictError` | Idempotency key replayed with a different request (or `replay="error"`) |
| `ProviderError` | `llm` provider raised (no `fallback`) |
| `GuardrailError` | `guardrails` input/output check failed |
| `TokenBudgetExceededError` | Input tokens over `budget` (carries `used`/`budget`) |
| `CapioCancelledBase` | Cancellation (`CapioTimeoutError` parent) |

**Non-retryable by default**: `KeyboardInterrupt`, `SystemExit`,
`asyncio.CancelledError`, and capio cancellations, plus
`IdempotencyConflictError`, `GuardrailError`, `TokenBudgetExceededError`, and
`SerializationError` — explicitly listing one in `retry_on` overrides this.

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

Execution guards: `throttle.rejected`, `debounce.scheduled/dropped/executed/
error`.

Data & auth: `audit.missing/failed`, `auth.authenticated/denied`,
`validate.failed`, `serialize.input/output`, `encrypt.missing_key`,
`mask.applied`, `dedup.hit/waiting/miss`.

Messaging & orchestration: `publish.sent/failed/missing/outboxed`,
`consume.missing/empty/delivered`, `queue.missing/enqueued/empty/dequeued`,
`transaction.started/committed/rolled_back/rollback_failed`,
`workflow.started/step/step_failed/completed`, `cron.fired/skipped/invalid`,
`compensate.started/executed/failed`, `idempotent.replay/conflict/stored`.

AI: `llm.started/completed/failed`, `llm_cache.hit/miss`,
`semantic_cache.hit/miss`, `prompt_cache.hit/miss`, `memory.retrieved/stored`,
`rag.retrieved`, `ingest.stored/missing`, `tool.registered`,
`agent.started/step/tool_call/tool_missing/finished`, `guardrails.violated`,
`token_budget.exceeded`, `model_router.selected`.

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
