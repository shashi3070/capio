# RFC-012: Plugin SDK & Capability Interface

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies the **Plugin SDK** (`capio.sdk`) and the **Capability interface**: the base
class, its abstract methods, sync/async/streaming/generator support, class and method
capabilities, config schema declaration, hooks declaration, and contract-test integration. A
plugin author reads this document (plus RFC-013) and can implement a capability without touching
runtime internals.

## 2. The Capability base class

```python
from capio import Capability

class MyCapability(Capability):
    name = "my_capability"                 # registered id (namespaced on load)
    version = "1.0.0"
    description = "..."

    # config schema (RFC-009 §5)
    schema: ConfigSchema = {
        "mode": {"type": "str", "default": "fast", "enum": ["fast", "safe"]},
        "threshold": {"type": "int", "default": 3, "min": 0},
    }

    # pipeline ordering (RFC-005 §4)
    priority = 500

    # execution kinds supported (RFC-003 §3.3)
    supports = ("sync", "async", "sync_gen", "async_gen")

    # dependencies (RFC-005 §5, RFC-010)
    depends_on: tuple[str, ...] = ()
    requires_backends: tuple[str, ...] = ()

    # lifecycle (RFC-011 §7) — optional overrides
    def configure(self, config: FrozenConfig) -> None: ...
    def initialize(self, services: ServiceResolver) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def destroy(self) -> None: ...

    # THE core contract — the execution step (RFC-005 §3.2)
    def run(self, ctx: Context, call_next: Callable[[Context], Any]) -> Any: ...
```

### 2.1 What the SDK does for you

- Validates your class against the Capability protocol at class definition (or at registration).
- Generates the decorator factory `use.my_capability` (RFC-003).
- Registers your config schema with the config system.
- Runs your `configure/initialize/start/stop/destroy` through the runtime lifecycle.
- Provides contract-test helpers (RFC-029) for your capability.

## 3. The core contract: `run`

`run(ctx, call_next)` is the single required method. The SDK supplies `call_next`, which invokes
the next inner step and finally the wrapped function (RFC-005 §3.2). The capability controls what
happens before, around, and after `call_next(ctx)`.

### 3.1 Canonical examples

**Retry (sync+async agnostic):**

```python
def run(self, ctx, call_next):
    attempts = 0
    while True:
        attempts += 1
        try:
            return call_next(ctx)
        except Exception as exc:
            if attempts >= self.cfg.max_attempts or not self._should_retry(exc):
                raise
            ctx.emit(Event("retry.attempt", payload={"attempt": attempts, "exc": str(exc)}))
            self._sleep(self.cfg.delay * attempts)
```

**Auth:**

```python
def run(self, ctx, call_next):
    principal = self._authenticate(ctx.carrier)
    ctx.auth = principal
    self._authorize(principal, ctx.fn)
    return call_next(ctx)
```

**Cache:**

```python
def run(self, ctx, call_next):
    key = self._build_key(ctx)
    hit = self.backend.get(key)
    if hit is not None:
        return self._deserialize(hit)
    result = call_next(ctx)
    self.backend.set(key, self._serialize(result), ttl=self.cfg.ttl)
    return result
```

### 3.2 Rule: write once, run both

The SDK **encourages** writing `run` in an execution-kind-agnostic way (as above). The engine
adapts it to the sync or async path (RFC-024): a sync `run` on an async function is executed
inline if it performs no blocking I/O, else the SDK flags it. Backend I/O that is genuinely
blocking MUST be routed through the backend's async-aware path (RFC-015 §5) so the event loop is
not starved.

If a capability genuinely cannot be kind-agnostic, it may override the four kind hooks:

```python
def run_sync(self, ctx, call_next): ...
def run_async(self, ctx, call_next): ...        # awaits call_next
def run_sync_gen(self, ctx, call_next): ...     # iterates call_next(ctx)
def run_async_gen(self, ctx, call_next): ...    # aiterates call_next(ctx)
```

The default implementations delegate to `run`. `supports` (base class) declares which kinds are
implemented; applying to an unsupported kind raises `UnsupportedExecutionKindError` at decoration
time (RFC-003 §3.3).

## 4. Streaming & generators

### 4.1 Contract

When the wrapped callable is a generator/async generator, `call_next(ctx)` returns an iterator;
the capability's streaming hooks iterate it. The **streaming contract**:

1. A generator capability wraps **lazily**: nothing is consumed until the caller iterates.
2. The capability controls the iterator: it may consume eagerly (fully realize — e.g. cache
   whole result) or lazily (pass through item-by-item — e.g. trace spans per item).
3. Cleanup (`finally`) of the underlying generator MUST run even when the consumer stops early
   (backpressure/`break`) — the engine wraps `GeneratorExit` correctly.
4. Retry over a partially-consumed stream is **not allowed** (state is lost); retry applies to
   establishing/opening the stream, not mid-stream. Retry + streaming capability MUST declare
   this in docs and the contract test enforces it.
5. Cache + streaming: only `cache_when`-declared modes allow caching the full realization; a
   lazily-streamed result is marked uncacheable unless a `realize` policy is set.

### 4.2 Streaming kinds

| Kind | `call_next(ctx)` returns | Capability iterates via |
| ---- | ------------------------ | ----------------------- |
| `sync_gen` | `Iterator[T]` | `for item in ...` |
| `async_gen` | `AsyncIterator[T]` | `async for item in ...` |

## 5. Class & method capabilities

### 5.1 Applying to a class

```python
@use.retry(max_attempts=3)
class Service:
    def call(self, payload): ...
    def _helper(self): ...       # not decorated by default
```

- By default the capability applies to all **public** instance/class/static methods except dunders
  and methods the class marks `__capio_skip__ = True` or the config `exclude` list.
- The capability config may restrict: `methods=["call"]`, `include_dunders=false`,
  `include_private=false`.
- Each method gets its own pipeline with its own resolved config (RFC-009 module/class merge
  adds a class config layer).
- Class-level application respects the capability's `supports` (methods are functions).

### 5.2 Applying inside a class body

```python
class Service:
    @use.retry(max_attempts=3)
    def call(self, payload): ...
```

- Bound/class/static methods are supported identically; `ctx.self_or_cls` and `ctx.cls` are set
  (RFC-006 §2.1).
- Classmethod/staticmethod wrapping preserves descriptor semantics; the SDK's decorator uses
  `__wrapped__` so `functools` and the class machinery see the original method.

## 6. Capability instance state

- `self.cfg` is the frozen, validated per-application configuration (RFC-009), set by the SDK at
  `configure`.
- `self.services` is a `ServiceResolver` for DI lookups (RFC-010), available after `initialize`.
- **Per-invocation state MUST live on the Context**, not on the instance
  (`ctx.capability(self.instance_id).state`), to preserve reentrancy (RFC-004 §3.5, RFC-006 §2.3).
- The SDK provides `ctx.capability(self.instance_id)` returning a typed, isolated state slot.

## 7. Hooks & observability from a capability

- Declare hooks in the class: `hooks = {"before_cache_lookup": "on_before_lookup", ...}` mapping
  hook names to method names (RFC-007). The SDK registers them at `configure`.
- Emit events with `ctx.emit(...)`; the SDK attaches the invocation snapshot automatically
  (RFC-008 §2.1).
- Measure with `ctx.metrics.*` and trace with `ctx.tracer.*`; never construct sinks directly
  (RFC-019).
- A capability that adds a *new* hook name must declare it in its manifest; unknown hook names
  fail registration (RFC-007 §2.2).

## 8. Config schema

- `schema` is a plain-dict schema (RFC-009 §5.1). The SDK validates resolved config against it
  and exposes `self.cfg` as an immutable, typed accessor (attribute access, e.g.
  `self.cfg.max_attempts`).
- Optional Pydantic interop: if `pydantic` is installed, a capability may declare a Pydantic
  model instead; the SDK uses it for validation and typing without requiring pydantic as a core
  dependency.
- `enable` (conditional, RFC-005 §6.1) is a schema key the SDK treats specially.

## 9. SDK surface (`capio.sdk`)

| Member | Purpose |
| ------ | ------- |
| `Capability` | base class (this RFC). |
| `Backend` | backend base class (RFC-015). |
| `HookRegistration` / `register_hook` | hook registration helpers (RFC-007). |
| `contract` module | `@contract(kind)` decorators + `capio_test(...)` helpers (RFC-029). |
| `TestingServer` / fake backends | test doubles (RFC-029). |
| `plugin_entrypoint` | registration helper used by `capio create-plugin` (RFC-028). |

## 10. Contract-test integration

Every capability published through the SDK MUST ship and pass:

1. **Kind matrix tests** — sync/async/generator behavior (RFC-029 §4).
2. **Reentrancy test** — concurrent + nested invocation correctness.
3. **Lifecycle test** — configure/initialize/start/stop/destroy ordering.
4. **Config tests** — schema validation, precedence, overrides.
5. **Backend agnostic tests** — for capabilities with backends, run against every registered
   backend (RFC-015 §6).
6. **Degradation test** — fail-safe behavior when a backend fails (RFC-005 §7).

`capio test` runs these against a plugin automatically (RFC-028, RFC-029).

## 11. Document Dependencies

- Concepts: RFC-002; API contract: RFC-003; pipeline steps: RFC-005; context/capability state:
  RFC-006; hooks: RFC-007; config schemas: RFC-009; DI: RFC-010; plugin lifecycle: RFC-011;
  manifest/packaging: RFC-013; backends: RFC-015; errors: RFC-025; concurrency: RFC-024; tests:
  RFC-029.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
