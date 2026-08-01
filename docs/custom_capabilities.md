# Custom capabilities guide

Write your own Capio capabilities: from a one-method wrapper to a stateful
capability with validated configuration, backends, and lifecycle hooks.

This is the companion to the
[README examples](https://github.com/shashi3070/capio/blob/main/README.md#custom-capabilities).
The normative contract is RFC-012 (plugin SDK) and RFC-011 (lifecycle).

---

## 1. The contract in one screen

A capability is a class that wraps the "next step" of the pipeline:

```python
class MyCapability(Capability):
    name = "mycapability"                        # registry key -> use.mycapability
    version = "1.0.0"
    description = "what it does"
    schema = {}                                  # validated options (RFC-009)
    priority = 500                               # higher = runs earlier
    supports = ("sync", "async")                 # execution kinds it can wrap
    depends_on = ()
    requires_backends = ()                       # backend names it needs
    degradation = "propagate"                    # "bypass" | "propagate" | "retry-later"

    def run(self, ctx, call_next):
        # must call call_next(ctx) exactly once (or loop) and return its result
        return call_next(ctx)
```

That's it. `use.mycapability(...)` works as soon as you register the class.

### Class attributes

| Attribute | Default | Meaning |
|---|---|---|
| `name` | `""` | Registry key; becomes `use.<name>` |
| `version` | `"1.0.0"` | Shown in `capio inspect` |
| `description` | `""` | Human-readable summary |
| `schema` | `{}` | Config schema; unknown options raise `ConfigurationError` |
| `priority` | `500` | Ordering in the composite form (higher runs first) |
| `supports` | `("sync", "async")` | Kinds it can wrap; else `UnsupportedExecutionKindError` |
| `depends_on` | `()` | Capability names that must also be present |
| `requires_backends` | `()` | Backend service names resolved via `self.backend(name)` |
| `degradation` | `"propagate"` | Failure policy: `"bypass"` (skip), `"propagate"` (re-raise), `"retry-later"` |

---

## 2. The three execution paths

The engine dispatches by function kind: `sync`, `async`, `sync_gen`, `async_gen`.

- **`run(ctx, call_next)`** — the default. Called for every kind via
  `run_sync`. Return the inner result.
- **`run_sync(ctx, call_next)`** — the sync entry point; default delegates to
  `run`.
- **`run_async(ctx, call_next)`** — the async entry point. Default delegates to
  `run`. **Override this** whenever your capability must `await call_next(ctx)`
  or do its own I/O. A transparent `run` that returns `call_next(ctx)` works on
  async functions automatically because the async engine awaits any awaitable a
  step returns.

The base class in `src/capio/sdk/capability.py`:

```python
def run_sync(self, ctx, call_next):
    return self.run(ctx, call_next)

async def run_async(self, ctx, call_next):
    return self.run(ctx, call_next)
```

### Sync + async capability

```python
import time
from capio import Capability

class Timing(Capability):
    name = "timing"
    priority = 500

    def run(self, ctx, call_next):
        start = time.perf_counter()
        try:
            return call_next(ctx)
        finally:
            self._record(ctx, time.perf_counter() - start)

    async def run_async(self, ctx, call_next):
        start = time.perf_counter()
        try:
            return await call_next(ctx)
        finally:
            self._record(ctx, time.perf_counter() - start)

    def _record(self, ctx, elapsed):
        ctx.emit(Event("timing.record", {"fn": ctx.fn_name, "seconds": elapsed}))
```

---

## 3. Configuration with `schema`

Options are validated and defaulted at decoration/first-call time by
`validate_config` (RFC-009). Unknown option keys raise `ConfigurationError` —
typos fail loudly, not silently.

```python
class Notify(Capability):
    name = "notify"
    schema = {
        "channel": {"type": "str", "default": "console"},
        "max_attempts": {"type": "int", "default": 3, "min": 1},
        "label": {"type": "any", "default": None},
        "enable": {"type": "any", "default": None},
    }

    def run(self, ctx, call_next):
        result = call_next(ctx)
        label = self.cfg.label or ctx.fn_name          # typed attribute access
        # self.cfg["label"] also works
        return result
```

Schema rules:

- `type`: `"any"`, `"str"`, `"int"`, `"number"`, `"bool"` (checked with
  `isinstance`).
- `default`: used when the option is omitted or `None`.
- `enum`: a list of allowed values.
- `min` / `max`: numeric bounds.

### The `enable` gate

Every capability should accept the conventional `enable` option — a
`(ctx) -> bool` predicate evaluated per call:

```python
@use.notify(enable=lambda ctx: ctx.env == "prod")
def pay() -> None:
    ...
```

When it returns `False` the capability is bypassed for that call. The engine
handles this internally, so you just declare `"enable": {"type": "any",
"default": None}` in your schema.

---

## 4. The lifecycle

Lifecycle hooks run once when the pipeline is built (on the first call, lazily):

```python
class FeatureFlagged(Capability):
    name = "feature_flagged"
    requires_backends = ("config.store",)

    def configure(self, config):
        # config: FrozenConfig (validated, defaulted, immutable)
        super().configure(config)
        # e.g. pre-parse durations here
        self._ttl = parse_duration(self.cfg.ttl) if self.cfg.ttl else None

    def initialize(self, services):
        # services: ServiceContainer — resolve backends you require
        super().initialize(services)
        self._store = self.backend("config.store")

    def start(self):
        # e.g. open connections; failure here -> CapabilityRuntimeError
        self._store.connect()

    def stop(self):
        self._store.close()

    def destroy(self):
        pass

    def run(self, ctx, call_next):
        if not self._store.flag_for(ctx.env):
            return call_next(ctx)
        return call_next(ctx)
```

Order: `configure → initialize → start` at build; `stop → destroy` at runtime
teardown. Instances are **per-pipeline** (per decorated function), so state like
a counter or circuit-breaker state is isolated.

---

## 5. Accessing the Context

`ctx` gives you the full per-invocation state (RFC-006):

| Field | Meaning |
|---|---|
| `ctx.args`, `ctx.kwargs` | The call arguments |
| `ctx.fn_name`, `ctx.fn_module` | The wrapped function |
| `ctx.self_or_cls` | Instance/class when a method was decorated (else `None`) |
| `ctx.env`, `ctx.profile`, `ctx.strict` | Runtime config |
| `ctx.invocation_id`, `ctx.request_id`, `ctx.correlation_id` | IDs (lazily generated) |
| `ctx.trace_id`, `ctx.span_id` | Trace ids |
| `ctx.deadline` | Monotonic deadline set by `timeout` |
| `ctx.cancel` | The invocation's `CancellationToken` |
| `ctx.loop` | The running event loop (async only) |
| `ctx.has_result()`, `ctx.result()` | The inner call's result, when set |
| `ctx.set_result(value)` | Record the result |
| `ctx.emit(event)` | Publish an event to the runtime event bus |

---

## 6. Emitting events

```python
from capio.events import Event

class Audit(Capability):
    name = "audit"

    def run(self, ctx, call_next):
        ctx.emit(Event("audit.before", {"fn": ctx.fn_name}))
        result = call_next(ctx)
        ctx.emit(Event("audit.after", {"fn": ctx.fn_name, "result": repr(result)}))
        return result
```

Subscribers can listen to the exact name or `"*"`:

```python
from capio import default_runtime

default_runtime().event_bus.subscribe("audit.after", lambda e: print(e.data))
```

Handler failures are caught and logged; instrumentation can never break the
invocation.

---

## 7. Backends

Capabilities don't own infrastructure — they resolve backends from the runtime's
service container:

```python
class Publisher(Capability):
    name = "publisher"
    requires_backends = ("bus.publish",)

    def run(self, ctx, call_next):
        result = call_next(ctx)
        backend = self.backend("bus.publish")
        if backend is None:
            ctx.emit(Event("publisher.degraded", {"reason": "backend_missing"}))
            return result                     # fail-safe: proceed
        backend.publish({"fn": ctx.fn_name, "result": repr(result)})
        return result
```

Register a backend in your runtime:

```python
from capio import CapioRuntime

runtime = CapioRuntime("my")
runtime.services.bind("bus.publish", MyPubSubBackend())
```

Built-in backend names: `cache.memory`, `trace.console`, `metrics.null`,
`log.stdio`.

---

## 8. Registration and ordering

```python
from capio.registry import registry

registry.register(Audit)         # idempotent for the same class
registry.unregister("audit")
registry.contains("audit")       # True/False
registry.names()                 # all registered names
```

- Registering a **different** class under a taken name raises
  `NameCollisionError`.
- **Composite order** (`@use("a", "b")`) sorts by `priority` descending —
  higher priority runs first (outermost).
- **Chained order** (`@use.a() @use.b()`) keeps the physical order you write,
  regardless of priority (RFC-005 rule 1).
- Priority of the built-ins, for reference:

  | Capability | Priority |
  |---|---|
  | rate_limit | 850 |
  | circuit_breaker | 800 |
  | cache | 750 |
  | retry | 700 |
  | timeout | 650 |
  | trace | 600 |
  | log | 550 |
  | metrics | 500 |

---

## 9. Decorating classes

A registered capability can decorate a whole class; every matching method gets
the capability:

```python
@use.audit(methods=["handle", "retry_handle"], exclude=[])
class Controller:
    def handle(self): ...
    def retry_handle(self): ...

@use.audit(methods=None, exclude=["_private"], include_private=False, include_dunders=False)
class Wide:
    ...
```

Class options: `methods` (only these), `exclude` (skip these), `include_private`,
`include_dunders`. Methods marked `__capio_skip__ = True` are always skipped.

---

## 10. Errors in a custom capability

- Raise `ConfigurationError` in `configure` for invalid combos.
- Let unexpected backend errors propagate (they surface as
  `CapabilityRuntimeError` and are wrapped), or degrade explicitly per your
  `degradation` policy.
- Your `run` exceptions flow out of the pipeline normally — outer capabilities
  (retry, breaker) see them and can react. To prevent a capability's exception
  from ever being retried, raise a `CapabilityException` subclass listed in
  `NON_RETRYABLE_CAPIO`, or simply don't list your error type in `retry_on`.

---

## 11. End-to-end example

A configurable, fail-safe "webhook notifier" that also records a metric and
respects the environment:

```python
import json
import time
from capio import Capability
from capio.events import Event

class WebhookNotify(Capability):
    name = "webhook_notify"
    version = "1.0.0"
    priority = 600
    requires_backends = ("webhook.client",)
    schema = {
        "url": {"type": "any", "default": None},          # static endpoint
        "include_result": {"type": "bool", "default": True},
        "max_attempts": {"type": "int", "default": 3, "min": 1},
        "enable": {"type": "any", "default": None},
    }

    def run(self, ctx, call_next):
        start = time.monotonic()
        result = call_next(ctx)
        payload = {
            "fn": f"{ctx.fn_module}.{ctx.fn_name}",
            "invocation_id": ctx.invocation_id,
            "duration_ms": (time.monotonic() - start) * 1000,
        }
        if self.cfg.include_result:
            payload["result"] = repr(result)
        self._notify(ctx, payload)
        return result

    def _notify(self, ctx, payload):
        client = self.backend("webhook.client")
        url = self.cfg.url
        if client is None or url is None:
            ctx.emit(Event("webhook_notify.degraded", {"reason": "missing_client_or_url"}))
            return                                      # fail-safe: never break the call
        try:
            client.post(url, json.dumps(payload))
        except Exception as exc:
            ctx.emit(Event("webhook_notify.failed", {"error": repr(exc)}))
```

```python
from capio import use
from capio.registry import registry

registry.register(WebhookNotify)

@use.retry(max_attempts=2)
@use.webhook_notify(url="https://hooks.example.com/events")
def charge(customer_id: str) -> dict:
    ...
```

Every `charge()` call posts a JSON event; if the webhook client is missing or
errors, the charge itself still succeeds.
