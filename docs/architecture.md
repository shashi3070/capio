# Capio Architecture & Code Walkthrough

How the Capio v1.0.0 reference implementation is built, module by module — with
code snippets and the invocation flow. This is the companion to
[`usage.md`](usage.md) (the user-facing manual).

- **Specification**: 34 RFCs in [`docs/rfcs/`](rfcs/); this document implements RFC-031.
- **Runtime**: Python >= 3.9, no third-party runtime dependencies (the CLI uses `typer`).
- **Layout**: `src/` setuptools layout with `py.typed`.

---

## 1. Directory map

```
capio/
├── pyproject.toml               # metadata, entry point capio.cli:app
├── src/capio/
│   ├── __init__.py              # public surface; imports capabilities (registration)
│   ├── config.py                # durations, FrozenConfig, schema validation
│   ├── exceptions.py            # full RFC-025 exception tree
│   ├── events.py                # Event + EventBus
│   ├── di.py                    # ServiceContainer
│   ├── registry.py              # capability registry (name -> class)
│   ├── context.py               # Context, CancellationToken, ContextScope
│   ├── pipeline.py              # detect_kind, ExecutionPipeline, build_pipeline
│   ├── runtime.py               # CapioRuntime + default_runtime() singleton
│   ├── use.py                   # the `use` facade (chained/composite/introspection)
│   ├── serialize.py             # serializer registry (json/pickle + custom codecs)
│   ├── cli.py                   # typer CLI: doctor/inspect/graph/benchmark/version
│   ├── sdk/
│   │   └── capability.py        # the Capability base class (the plugin contract)
│   ├── engine/
│   │   ├── sync.py              # sync execution engine
│   │   └── async_engine.py      # async execution engine
│   ├── backends/
│   │   ├── memory_cache.py      # thread-safe TTL dict cache
│   │   ├── console_trace.py     # JSON-lines span sink
│   │   ├── null_metrics.py      # in-memory metric records
│   │   ├── stdio_log.py         # logging-facade structured records
│   │   ├── audit_log.py         # append-only SHA-256 hash-chained audit trail
│   │   ├── memory_store.py      # namespaced KV with TTL + sequence
│   │   ├── memory_broker.py     # pub/sub with per-group cursors
│   │   └── task_queue.py        # FIFO task queue with worker threads
│   └── capabilities/
│       ├── resilience: retry  cache  timeout  circuit_breaker  rate_limit
│       │               throttle  debounce
│       ├── data/auth:  audit  auth  validate  serialize  encrypt  mask  dedup
│       ├── messaging:  publish  consume  queue  transaction  workflow  cron
│       │               compensate  idempotent
│       ├── ai:         llm  llm_cache  semantic_cache  prompt_cache  memory
│       │               rag  ingest  tool  agent  guardrails  token_budget
│       │               model_router  (_ai shared helpers)
│       └── __init__.py          # imports each module -> auto-registration
├── tests/                       # 126 pytest tests
└── examples/quickstart.py
```

---

## 2. The invocation flow (TL;DR)

Decorating a function **does no work** — it only attaches metadata
(`fn.__capio__`) and a thin wrapper. Real work happens on first call:

```
decorated_fn(*args)
        │  (1) wrapper -> runtime.call(fn, meta, args, kwargs)
        ▼
get_pipeline(fn, meta)                 # memoized by (id(fn), id(meta))
        │  first time: build_pipeline()
        ▼
build_pipeline:                         # per capability, outermost-first
  - registry.get(name) -> class
  - kind = detect_kind(fn)             # sync | async | sync_gen | async_gen
  - check kind in capability.supports  # else UnsupportedExecutionKindError
  - instance.configure(cfg)            # validated, defaulted, frozen
  - instance.initialize(services)
  - instance.start()                   # lifecycle at build time
        │
        ▼
pipeline.execute(*args, **kwargs)
        │  dispatches to engine by kind
        ▼
engine.execute_sync / execute_async:
  1. ctx = pipeline.build_context(args, kwargs)
  2. ctx.scope.enter()                 # ContextVar "capio_ctx" now readable
  3. recursive _invoke(pipeline, idx=0, ctx)
        for each step: step.run(ctx, lambda: _invoke(idx+1))
        at the leaf: pipeline.fn(*ctx.args, **ctx.kwargs)
  4. ctx.scope.exit()
```

Every capability step is a middleware-style wrapper around the next one, so the
pipeline is a literal onion:

```
circuit_breaker -> cache -> retry -> timeout -> trace -> metrics -> fn
  (outermost, high priority)                        (innermost, low priority)
```

---

## 3. Configuration primitives — `config.py`

Three pieces: duration parsing, an immutable config view, and schema validation.

### 3.1 `parse_duration`

```python
_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h)?\s*$")
_DURATION_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}

def parse_duration(value):
    if isinstance(value, (int, float)):
        return float(value)               # numbers are seconds
    if isinstance(value, str):
        match = _DURATION_RE.match(value)
        number, unit = float(match.group(1)), match.group(2) or "s"
        return number * _DURATION_UNITS[unit]
```

All "duration" options in every capability accept either a float (seconds) or a
string like `"100ms"`, `"5m"`, `"1.5h"`. This single helper keeps the surface
consistent.

### 3.2 `FrozenConfig`

An immutable, attribute-accessible mapping built on `MappingProxyType`:

```python
class FrozenConfig(Mapping[str, Any]):
    __slots__ = ("_data",)
    def __init__(self, data=None):
        object.__setattr__(self, "_data", MappingProxyType(dict(data or {})))
    def __getattr__(self, name):
        return self._data[name]      # cfg.max_attempts
    def __getitem__(self, key):      # cfg["max_attempts"]
        return self._data[key]
```

`__slots__` + `mappingproxy` means capability configs are cheap to construct and
cannot be mutated after `configure()`.

### 3.3 `validate_config`

Given a schema (from the capability class) and user options, it returns a
**fully-defaulted** dict. Rules implemented (RFC-009):

- schema shape: `{key: {"type", "default", "enum", "min", "max"}}`
- `None` option values fall back to the default
- `type != "any"` is checked with `isinstance`
- `enum`, `min`, `max` constraints enforced
- **unknown option keys raise `ConfigurationError`** (typo protection)

```python
resolved = validate_config(capability_cls.schema, {"max_attempts": 2})
# -> {"max_attempts": 2, "delay": "100ms", "backoff": "exponential", ...}
```

---

## 4. The error tree — `exceptions.py`

A single root exception carries structured context:

```python
class CapabilityException(Exception):
    code: ClassVar[str] = "capio.error"
    def __init__(self, message="", *, code=None, runtime=None,
                 capability=None, plugin=None, extra=None):
        ...
```

Notable design decisions:

- **Cancellation is not `Exception`.** `CapioCancelledBase(BaseException)` and
  `CapioTimeoutError` live under `BaseException` so retry loops never swallow a
  cancellation, and `except Exception` in user code can't hide a timeout.
- Retry/breaker helpers consult `NON_RETRYABLE_ALWAYS` and `NON_RETRYABLE_CAPIO`
  tuples to decide what must never be retried.
- Each subsystem has a group: runtime, config, registry, execution, data,
  backend, auth, bus, integration — e.g. `RetryExhaustedError`,
  `CircuitOpenError`, `RateLimitExceededError(retry_after=...)`,
  `BackendUnavailableError`.

---

## 5. Events — `events.py`

A tiny in-process pub/sub bus. Capabilities emit via `ctx.emit(event)`; the bus
calls exact-name subscribers plus a `"*"` wildcard. Subscriber failures are
logged and swallowed so instrumentation can never break the invocation:

```python
def publish(self, event):
    for name in (event.name, "*"):
        for handler in list(self._subscribers.get(name, ())):
            try:
                handler(event)
            except Exception:
                _log.exception("event handler for %r failed", name)
```

Standard event names emitted by the base capabilities:

| Capability | Events |
|---|---|
| retry | `retry.attempt`, `retry.succeeded`, `retry.exhausted` |
| cache | `cache.hit`, `cache.miss`, `cache.stored`, `cache.failed` |
| timeout | `timeout.warning`, `timeout.fired`, `timeout.handled` |
| circuit_breaker | `circuit.open`, `circuit.half_open`, `circuit.closed`, `circuit.rejected` |
| rate_limit | `rate.limited` |
| trace | `trace.exporter_failed` |
| metrics | `metrics.exporter_failed` |
| log | `log.failed` |
| throttle | `throttle.rejected` |
| debounce | `debounce.scheduled`, `debounce.dropped`, `debounce.executed`, `debounce.error` |
| audit | `audit.missing`, `audit.failed` |
| auth | `auth.authenticated`, `auth.denied` |
| validate | `validate.failed` |
| serialize | `serialize.input`, `serialize.output` |
| encrypt | `encrypt.missing_key` |
| mask | `mask.applied` |
| dedup | `dedup.hit`, `dedup.waiting`, `dedup.miss` |
| publish | `publish.sent`, `publish.failed`, `publish.missing`, `publish.outboxed` |
| consume | `consume.missing`, `consume.empty`, `consume.delivered` |
| queue | `queue.missing`, `queue.enqueued`, `queue.empty`, `queue.dequeued` |
| transaction | `transaction.started`, `transaction.committed`, `transaction.rolled_back`, `transaction.rollback_failed` |
| workflow | `workflow.started`, `workflow.step`, `workflow.step_failed`, `workflow.completed` |
| cron | `cron.fired`, `cron.skipped`, `cron.invalid` |
| compensate | `compensate.started`, `compensate.executed`, `compensate.failed` |
| idempotent | `idempotent.replay`, `idempotent.conflict`, `idempotent.stored` |
| llm | `llm.started`, `llm.completed`, `llm.failed` |
| llm_cache | `llm_cache.hit`, `llm_cache.miss` |
| semantic_cache | `semantic_cache.hit`, `semantic_cache.miss` |
| prompt_cache | `prompt_cache.hit`, `prompt_cache.miss` |
| memory | `memory.retrieved`, `memory.stored` |
| rag | `rag.retrieved` |
| ingest | `ingest.stored`, `ingest.missing` |
| tool | `tool.registered` |
| agent | `agent.started`, `agent.step`, `agent.tool_call`, `agent.tool_missing`, `agent.finished` |
| guardrails | `guardrails.violated` |
| token_budget | `token_budget.exceeded` |
| model_router | `model_router.selected` |

---

## 6. Services & registry — `di.py`, `registry.py`

### 6.1 ServiceContainer

Simple name→service map used for **backends**. `bind` raises
`ServiceAlreadyBound` on duplicates; `bind_replace` overrides (used by tests and
users swapping backends).

### 6.2 Registry

Capabilities register by name → class. Re-registering the **same class** is
idempotent (which is why importing `capio.capabilities` multiple times is safe);
registering a different class under a taken name raises `NameCollisionError`.

```python
registry.register(MyCapability)          # class
registry.unregister("mycapability")
registry.get("cache")                    # -> Cache class
```

---

## 7. Context — `context.py`

`Context` is the per-invocation state object passed to every capability. It has
`__slots__` and follows RFC-006 (args, kwargs, fn metadata, profile/env/strict,
capability state slots, cancellation token, deadline, trace/span ids, result and
error fields, event bus, scope).

**Performance-critical decision (RFC-027 §2.3): lazy IDs.** IDs are generated on
first access, not at construction:

```python
@property
def invocation_id(self):
    if self._invocation_id is None:
        self._invocation_id = _new_id("inv")
    return self._invocation_id
```

Before this change, every invocation paid two `uuid4()` calls (~4.7 µs); after,
an invocation that never touches IDs pays zero. A monotonic counter + a
process-random suffix keeps IDs unique across processes.

Propagation uses a `ContextVar` so sync, async, and threaded code all get the
right context:

```python
_scope_var: "ContextVar[Optional[Context]]" = ContextVar("capio_ctx", default=None)

class ContextScope:
    def enter(self): self._token = _scope_var.set(self._ctx)
    def exit(self):  _scope_var.reset(self._token)

def current_context(): return _scope_var.get()
```

---

## 8. The capability contract — `sdk/capability.py`

This is the single class plugins implement (RFC-012):

```python
class Capability:
    name = ""                     # registry key (used as use.<name>)
    version = "1.0.0"
    description = ""
    schema = {}                   # config schema for validate_config
    priority = 500                # higher = runs earlier (outermost)
    supports = ("sync", "async")
    depends_on = ()
    requires_backends = ()
    degradation = "propagate"     # "bypass" | "propagate" | "retry-later"

    def run(self, ctx, call_next): ...   # REQUIRED - the core contract
    def run_sync(self, ctx, call_next): return self.run(ctx, call_next)
    async def run_async(self, ctx, call_next): return self.run(ctx, call_next)
```

`run(ctx, call_next)` must call `call_next(ctx)` exactly once (or loop over it),
decide what happens before/after, and return the inner result. **Transparent
sync capabilities** (that return `call_next(ctx)` untouched) work on async
functions automatically because the async engine awaits any awaitable a step
returns:

```python
result = step.run_async(ctx, lambda c: _invoke(pipeline, idx + 1, c))
while inspect.isawaitable(result):
    result = await result
```

A capability that *consumes* the inner result synchronously and is used on an
`async def` must override `run_async` (see `cache`, `metrics`, `trace`, `log`).

### Lifecycle (RFC-011 §7)

`configure -> initialize -> start` run once at **pipeline build**; `stop` /
`destroy` at teardown. Instances are per-pipeline, so a capability's state (e.g.
circuit breaker state machine) is isolated per decorated function.

---

## 9. Backends — `backends/`

Backends are plain classes bound into the runtime's service container by name;
capabilities resolve them lazily via `self.backend(name)`.

```python
# runtime.py
self.services.bind("cache.memory",  MemoryCacheBackend())
self.services.bind("trace.console", ConsoleTraceBackend())
self.services.bind("metrics.null",  NullMetricsBackend())
self.services.bind("log.stdio",     StdioLogBackend())
self.services.bind("audit.memory",  InMemoryAuditBackend())
self.services.bind("store.memory",  InMemoryStore())
self.services.bind("broker.memory", InMemoryBroker())
self.services.bind("queue.memory",  InMemoryTaskQueue())
```

**The `_MISSING` sentinel** (`memory_cache.py`) is the key trick that lets a
stored `None` be distinguished from a cache miss:

```python
_MISSING = object()

def get(self, key, default=_MISSING):
    item = self._store.get(key)
    if item is None:
        return default
    expires, value = item
    if expires is not None and expires <= time.monotonic():
        del self._store[key]
        return default
    return value
```

The cache backend is thread-safe (`RLock`), uses a monotonic clock for TTL, and
evicts by max-size + expired keys.

---

## 10. Capabilities — patterns

Every capability follows the same shape: a `schema`, a `run` method, and (where
needed) a `run_async`. Highlights:

The 28 capabilities added in 1.0.0 follow four recurring patterns:

- **Execution guards** (`throttle`, `debounce`) — a `threading.BoundedSemaphore`
  / `asyncio.Semaphore` for admission control, and a per-key pending table with
  `threading.Timer` / `loop.call_later` for coalescing.
- **Data/auth** (`audit`, `auth`, `validate`, `serialize`, `encrypt`, `mask`,
  `dedup`) —
  mutate `ctx.kwargs` (replacing the mapping) before `call_next` and/or rewrite
  the result after; `serialize` encodes inputs/decodes outputs through the
  `capio.serialize` registry (safe `json` default, `pickle` opt-in via `trust`);
  `encrypt` derives a key with `pbkdf2_hmac` and XORs an
  HMAC-SHA256 keystream (no third-party crypto).
- **Messaging/orchestration** (`publish`, `consume`, `queue`, `transaction`,
  `workflow`, `cron`, `compensate`, `idempotent`) — resolve a backend via
  `self.backend(name)` and either produce (publish/enqueue/commit) or dispatch
  (consume/dequeue/rollback); `cron` is a *gate*, not a clock — it decides
  whether an arriving call is due.
- **AI** (`llm`, `llm_cache`, `semantic_cache`, `prompt_cache`, `memory`, `rag`,
  `ingest`, `tool`, `agent`, `guardrails`, `token_budget`, `model_router`) —
  share `_ai.py` helpers (`query_text`, `messages`, `result_text`,
  `request_signature`, `cosine`, `count_tokens`, `chunk_text`). They read/write
  `ctx.kwargs`, store the model response in
  `ctx.capability("llm")["state"]["response"]`, and degrade fail-safe when the
  store backend or embedder is missing.

### 10.1 Retry (`retry.py`, priority 700)

```python
class Retry(Capability):
    schema = {
        "max_attempts": {"type": "int", "default": 3, "min": 1},
        "delay":        {"type": "any", "default": "100ms"},
        "backoff":      {"type": "str", "default": "exponential",
                         "enum": ["fixed", "linear", "exponential"]},
        "jitter":       {"type": "any", "default": True},
        ...
    }

    def run(self, ctx, call_next):
        while True:
            try:
                return call_next(ctx)
            except (CapioCancelledBase, KeyboardInterrupt, SystemExit,
                    asyncio.CancelledError):
                raise                        # never retry these
            except BaseException as exc:
                if not self._should_retry(ctx, exc): break
                delay = self._compute_delay(attempts)
                time.sleep(delay)            # async path uses asyncio.sleep
        raise RetryExhaustedError(...) from last_exc
```

Key semantics:
- `_should_retry` honors `retry_if` (predicate) over `retry_on` (types); default
  retries on `Exception`.
- **Escape hatch**: if the user explicitly lists a non-retryable type in
  `retry_on`, it *is* retried — explicit configuration wins over the hardcoded
  never-retry table.
- `on_final="reraise_original"` re-raises the *first* exception; otherwise a
  `RetryExhaustedError` chained to the last one.

### 10.2 Cache (`cache.py`, priority 750)

Canonical key building hashes args/kwargs deterministically:

```python
def _canonicalize(value):
    if isinstance(value, bool):  return "b" + str(int(value))
    if isinstance(value, str):   return "s" + value
    if isinstance(value, dict):  return "{" + sorted(...) + "}"
    ...
def _stable_hash(args, kwargs):
    return hashlib.sha256("|".join(parts).encode(...)).hexdigest()
```

Fail-safe degradation is explicit: if the backend is missing and the runtime is
not strict, the capability **bypasses** the cache (calls through); in strict
mode it raises `BackendUnavailableError`.

**Singleflight** differs by execution model: the sync path uses threads
(`_SingleFlight`), the async path uses an in-loop `asyncio.Future`:

```python
async def _run_singleflight_async(self, ctx, call_next, backend, key):
    existing = self._async_inflight.get(key)
    if existing is not None and not existing.done():
        return _unwrap(await existing)      # share the in-flight computation
    fut = loop.create_future()
    self._async_inflight[key] = fut
    ...
```

Stored values are wrapped (`_Ok` / `_Err` / `_StoredExc`) so stored exceptions
(`cache_on_error=True`) and `None` results survive round-trips through the
backend and singleflight.

### 10.3 Timeout (`timeout.py`, priority 650)

The sync path is **cooperative by design** (RFC-018 §3.3): it sets
`ctx.deadline` and checks elapsed time when the callable returns. The async path
is a real hard timeout:

```python
async def run_async(self, ctx, call_next):
    seconds = parse_duration(self.cfg.seconds)
    ctx.deadline = time.monotonic() + seconds
    try:
        return await asyncio.wait_for(call_next(ctx), timeout=seconds)
    except (asyncio.TimeoutError, TimeoutError):
        ctx.cancel.cancel()
        return self._on_expiry(ctx, seconds)
```

`_on_expiry` either raises `CapioTimeoutError` (`raise_on=True`) or returns the
`return_on` sentinel (emitting `timeout.fired` / `timeout.handled`).
`raise_on=True` + `return_on` set is rejected at configure time.

### 10.4 Circuit breaker (`circuit_breaker.py`, priority 800)

A per-instance state machine `closed → open → half_open → closed` with a
sliding-window failure deque:

```python
def _record_failure(self, ctx, exc):
    if not self._counts(exc): return        # only_on / exclude / timeouts
    now = time.monotonic()
    self._failures.append(now)
    while self._failures and self._failures[0] <= now - window:
        self._failures.popleft()
    if self.state != "open" and len(self._failures) >= self.cfg.failure_threshold:
        self.state = "open" ...
```

Details worth noting:
- `CapioCancelledBase` never counts as a failure; `CapioTimeoutError` counts
  only when `record_timeouts=True` (default).
- Half-open state admits at most `half_open_max` probes; a successful probe
  closes the circuit (per `success_threshold`).

### 10.5 Rate limit (`rate_limit.py`, priority 850)

Three strategies share a `_check(ctx, now) -> (ok, retry_after)` interface:

- `fixed`: bucketed by `int(now // window)`
- `sliding`: a `deque` of timestamps pruned to the window
- `token_bucket`: tokens refilled by `_parse_rate` (accepts `"10/s"`, `"100/1m"`)

```python
def _check_token(self, key, now):
    capacity = self.cfg.bucket_capacity or self.cfg.limit
    rate = _parse_rate(self.cfg.refill_rate)
    tokens = min(float(capacity), tokens + (now - last) * rate)
    if tokens >= 1.0: state[0] = tokens - 1.0; return True, 0.0
    return False, (1.0 - tokens) / rate
```

`on_exceeded` is `raise` | `wait` | `return`; `wait` is bounded by `max_wait`.

### 10.6 Observability — trace / metrics / log

All three are **fail-safe**: any backend exception is captured as an event and
never propagates.

```python
def _emit_span(self, ctx, span):
    backend = self.backend(self.cfg.backend)
    if backend is None:
        ctx.emit(Event("trace.exporter_failed", {"reason": "backend_missing"}))
        return
    try:
        backend.emit(span)
    except Exception as exc:
        ctx.emit(Event("trace.exporter_failed", {"error": repr(exc)}))
```

- **Trace** sets `ctx.trace_id` / `ctx.span_id` (nesting works because it reads
  `parent_span = ctx.span_id` before assigning a new span id) and emits one JSON
  span dict.
- **Metrics** emits a `*.calls_total` counter (tagged with `outcome`) and a
  `*.duration_ms` histogram on every call.
- **Log** emits a structured record (`invocation_id`, `fn`, `outcome`,
  `duration_ms`, optional args/result) through the `log.stdio` backend.

---

## 11. Pipeline — `pipeline.py`

`detect_kind` classifies the callable:

```python
def detect_kind(fn):
    if inspect.isasyncgenfunction(fn):  return "async_gen"
    if inspect.iscoroutinefunction(fn): return "async"
    if inspect.isgeneratorfunction(fn): return "sync_gen"
    return "sync"
```

`build_pipeline` (called lazily and memoized) enforces the ordering contract:

- capability list is **outermost-first**
- duplicate names → `DuplicateCapabilityError`
- unsupported execution kind → `UnsupportedExecutionKindError`
- lifecycle (`configure → initialize → start`) runs per instance; `start`
  failure → `CapabilityRuntimeError`

```python
key = (id(fn), id(meta))     # memo key in runtime.get_pipeline
pipeline = self._pipelines.get(key)          # lock-free fast path
if pipeline is None:
    with self._lock:                         # lock only on miss
        pipeline = build_pipeline(...)
```

`build_context` wires the runtime config, profile/env/strict flags, and event
bus into each new Context without copying the config dict (RFC-027 §2.3).

---

## 12. Engines — `engine/sync.py`, `engine/async_engine.py`

The two engines share the identical recursion. Sync:

```python
def _invoke(pipeline, idx, ctx):
    if idx >= len(pipeline.steps):
        return pipeline.fn(*ctx.args, **ctx.kwargs)
    step = pipeline.steps[idx]
    enable = step.cfg.get("enable")
    if enable is not None and not enable(ctx):
        return _invoke(pipeline, idx + 1, ctx)   # dynamic per-call gate
    return step.run_sync(ctx, lambda c: _invoke(pipeline, idx + 1, c))
```

Every capability's schema includes `enable: None | (ctx) -> bool`. When set to
a callable, each step is evaluated at call time — the mechanism behind the
benchmark's "pass-through" pipelines and runtime-toggled capabilities.

The async engine mirrors this with `await`, sets `ctx.loop`, and awaits any
awaitable returned by a step (the loop shown in §8).

---

## 13. Runtime — `runtime.py`

`CapioRuntime` owns config, the service container, the event bus, the global
registry, and the pipeline memo:

```python
class CapioRuntime:
    def __init__(self, name="default", config=None):
        base = {"env": "dev", "profile": "default", "strict": False}
        self.config = FrozenConfig(merge_config(merge_config(base, config or {}), env_from_os()))
        self.services = ServiceContainer()
        self.event_bus = EventBus()
        self.registry = registry
        self.services.bind("cache.memory", MemoryCacheBackend())
        ...
        import capio.capabilities          # idempotent auto-registration
        self._pipelines = {}
```

`default_runtime()` is a lazily-created, lock-guarded singleton that backs the
module-level `use`. Environment knobs:

- `CAPIO_ENV` / `CAPIO_PROFILE` / `CAPIO_STRICT` → merged into config at
  construction (`env_from_os`).

---

## 14. The `use` facade — `use.py`

The public API. Chained form dispatches dynamically to registered capabilities:

```python
def __getattr__(self, name):
    if not registry.contains(name):
        raise UnknownCapabilityError(...)
    def factory(**options):
        return self._decorate_with(name, options)
    return factory
```

**Chained merge** prepends the newest spec (outermost = applied last):

```python
def _merge_meta(existing, spec):
    for capability in existing.capabilities:
        if capability.name == spec.name:
            raise DuplicateCapabilityError(...)     # same name twice = error
    return CapioMeta(capabilities=(spec,) + existing.capabilities, ...)
```

**Composite** sorts by priority descending (RFC-005 §4) and refuses to decorate
an already-decorated function (`ConflictingPipelineError`), because the physical
order a user wrote in chained form can override priorities (RFC-005 rule 1) and
cannot be reconstructed from a bare set of capabilities:

```python
ordered = tuple(sorted(specs.values(), key=lambda s: s.priority, reverse=True))
```

**Context injection** is implemented as a `__capio_leaf__`-marked wrapper. This
matters because `unwrap` normally follows `__wrapped__` all the way down; the
marker makes the engine stop at the injection wrapper so it stays inside the
executed pipeline (where `current_context()` is live):

```python
def unwrap(fn):
    while (fn is not None and hasattr(fn, "__wrapped__")
           and not getattr(fn, "__capio_leaf__", False)):
        fn = getattr(fn, "__wrapped__")
    return fn
```

---

## 15. CLI — `cli.py`

A Typer app exposing four commands plus `version`:

| Command | Purpose |
|---|---|
| `capio doctor` | prints runtime/backends/capabilities, runs a smoke invocation |
| `capio inspect MODULE.FN` | dumps `__capio__` metadata |
| `capio graph MODULE.FN` | renders the pipeline order |
| `capio benchmark [--enforce]` | measures decoration/build/invocation vs RFC-027 budgets |
| `capio version` | prints the version |

Note: `_load_object` imports the full dotted module path
(`rpartition(".")`) so targets like `examples.quickstart.fetch_user` work.

---

## 16. Key design decisions (recap)

1. **Decoration is metadata-only** — no I/O until first call, pipeline built
   lazily and memoized by `(id(fn), id(meta))`.
2. **Fail-safe by default** — cache/trace/metrics/log degrade to pass-through;
   `strict` mode flips them to raise.
3. **Cancellation is `BaseException`** — retry/breaker never swallow it.
4. **Lazy context IDs** — zero-cost-if-unused (RFC-027 §2.3).
5. **Chained order wins over priority** (RFC-005 rule 1); composite uses the
   priority table, so equivalence holds when the chained form is written in
   priority order.
6. **Per-pipeline capability instances** — state like circuit-breaker state is
   isolated per decorated function.
7. **`enable` dynamic gate** — every capability supports a per-call predicate.

## 17. Performance

Measured on this machine (`capio benchmark`):

| Measure | Measured | RFC-027 budget |
|---|---|---|
| Decoration | ~31 µs | < 100 µs |
| Pipeline build (cached path) | ~0.7 µs | < 5 ms |
| 5-capability pass-through pipeline | ~5.3 µs | < 20 µs |
| 1-capability pass-through pipeline | ~4.3 µs | < 2 µs |

The 1-cap budget targets reference CI hardware (RFC-031); the remaining cost is
mandatory Context creation + ContextVar scope, which lazy IDs already cut from
~7.6 µs to ~4.3 µs.
