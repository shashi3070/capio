# RFC-003: The `use` API Contract

- **Status:** Accepted
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies the **public decorator API** of Capio: the `use` object, the chained
`@use.<capability>(...)` family, the composite `@use(...)` form, ordering and composition
semantics, introspection, and the typing contract. It is the document a user reads to *write
decorated code*, and the contract a type checker can verify against.

## 2. The `use` Object

### 2.1 Import surface

```python
from capio import use
from capio import Capability  # base class for custom capabilities
from capio import with_capabilities  # optional lower-level helper
```

`use` is a module-level singleton of type `Use`. It is immutable from the user's perspective:
users do not construct it, extend it, or configure it directly at import time. Configuration
arrives through decorator arguments, environment, and the runtime configuration system (RFC-009).

### 2.2 Two primary forms

**Form A — chained (primary, RECOMMENDED).** Each capability is its own decorator:

```python
@use.retry(max_attempts=3, backoff="exponential")
@use.cache(ttl="5m")
@use.trace()
def search(query: str) -> list[str]:
    ...
```

**Form B — composite.** One decorator, capabilities as keywords or a list:

```python
@use(retry={"max_attempts": 3}, cache={"ttl": "5m"}, trace=True)
def search(query: str) -> list[str]:
    ...
```

Both forms MUST compose the exact same pipeline for equivalent arguments. The chained form is the
primary documented API; the composite form exists for ergonomics and dynamic composition. **Order
semantics differ and are specified in §4.**

### 2.3 What `use.retry` etc. are

Each capability name is exposed on the `use` object. `use.retry` is a callable factory returning
a decorator. Calling it with no arguments MUST be valid (`@use.retry()`). Passing `None`
explicitly is equivalent to omitting the argument (use the default from RFC-009).

Capabilities that ship in the base package and their decorator names:

| Decorator | Capability | RFC |
| --------- | ---------- | --- |
| `use.retry` | Retry | RFC-017 |
| `use.cache` | Cache | RFC-016 |
| `use.timeout` | Timeout | RFC-018 |
| `use.circuit_breaker` | Circuit Breaker | RFC-018 |
| `use.rate_limit` | Rate Limit | RFC-018 |
| `use.throttle` | Throttle | RFC-018 |
| `use.debounce` | Debounce | RFC-018 |
| `use.trace` | Trace | RFC-019 |
| `use.metrics` | Metrics | RFC-019 |
| `use.log` / `use.audit` | Logging / Audit | RFC-020 |
| `use.auth` | Authentication | RFC-021 |
| `use.validate` | Validation | RFC-022 |

Third-party capabilities register their decorator name onto the runtime's `use` facade at plugin
load time (RFC-012, RFC-013). A plugin named `capio-openai` may expose `use.llm_cache(...)`.
The mechanism for exposing these is the **capability registry** (RFC-014); `use` is a facade over
the registry, and unknown names raise `UnknownCapabilityError` (RFC-025) at import/decoration
time, not silently.

## 3. The Chained Form — exact contract

### 3.1 Decorator signature

Every capability decorator has the shape:

```
Decorator = Callable[[WrappedCallable], DecoratedCallable]
```

`use.retry(...)` returns a `CapabilityDecorator` — a function that accepts a callable and returns
the wrapped callable. The decorator MUST:

1. Accept `def`, `async def`, generators, async generators, classes, and instance/class methods
   (RFC-012 defines the four execution kinds).
2. Perform **no I/O** and **no backend access** at decoration time. It records intent only.
3. Preserve `__name__`, `__doc__`, `__qualname__`, `__module__`, and `__wrapped__` per PEP 318
   conventions, so `inspect.signature`, `help()`, and `functools.wraps` behave.
4. Not raise `TypeError` at decoration time for genuinely valid configurations; configuration
   validation MAY run eagerly (fail-fast) but MUST NOT require backend availability.

### 3.2 Normative rules for the chained form

1. Decorators apply bottom-up per Python semantics. **Capability order is therefore written
   top-first for outermost behavior.** The decorator closest to the function runs first/innermost.
2. Default ordering rules (RFC-005 §4) apply for capabilities whose relative position is
   unspecified or conflicting. Explicit chaining always wins over defaults for the capabilities
   the user named.
3. Applying the same capability name twice on one function MUST raise `DuplicateCapabilityError`
   at decoration time unless the capability is declared repeatable in its manifest. Retry, cache,
   trace, metrics are single-application by default; hooks and validators are repeatable.
4. Chaining is flattened: `@use.cache() @use.cache()` is an error (rule 3), but a decorator may
   internally compose capabilities; composition is always flattened to a single pipeline, never
   nested copies of the runtime.

### 3.3 Supported callable kinds

| Kind | Example | Supported by all capabilities? |
| ---- | ------- | ------------------------------ |
| Plain function | `def f(...)` | Yes |
| Async function | `async def f(...)` | Yes (RFC-024) |
| Generator | `def gen()` with `yield` | Yes; streaming semantics (RFC-012) |
| Async generator | `async def gen()` with `yield` | Yes; streaming semantics |
| Class | `@use.retry() class X:` | Yes; applies to specified methods per config |
| Bound/class/static method | decorator inside class body | Yes |

A capability that supports only a subset MUST declare the subset in its manifest and MUST raise a
typed `UnsupportedExecutionKindError` at decoration time when applied to a disallowed kind —
never at invocation time.

## 4. The Composite Form — exact contract

### 4.1 Signature

```python
use(*capability_names: str, **capability_options: CapabilityOptions)
```

- Positional strings name capabilities to enable with defaults: `@use("retry", "cache")`.
- Keyword arguments map capability names to their options: `@use(retry=True, cache={...})`.
- `True` enables with defaults; a `Mapping` is the options dict; `False` or `None` disables
  (useful when composing decorators dynamically).
- Unknown capability names MUST raise `UnknownCapabilityError`.
- Both positional and keyword forms MAY be mixed; a capability named both ways MUST be specified
  consistently or raise `ConfigurationError` (RFC-025).

### 4.2 Order semantics in the composite form

In the composite form the user does not write physical decorator order, so Capio uses **declared
capability priority** (RFC-005 §4). Default priority for base capabilities:

```
auth         (outermost: gate first)
validation
rate_limit / throttle / debounce
circuit_breaker
cache (lookup)
retry
timeout
trace
metrics
function (innermost)
```

The composite form resolves the ordering from this fixed priority table; plugins register their
own priority in the manifest. Any capability may override its relative position via explicit
`priority` in options; conflicts are resolved per RFC-005 §4.4.

### 4.3 Equivalence guarantee

`@use(retry={"max_attempts": 3}, cache={"ttl": "5m"}, trace=True)` MUST produce the same pipeline
as the chained form written in priority order. This equivalence is enforced by contract tests
(RFC-029).

## 5. Composition & introspection

### 5.1 Applying `use` to already-decorated functions

If a wrapped callable already carries Capio metadata (attribute `__capio__`, §5.3), applying
another Capio decorator MUST either merge into the existing pipeline (in the chained form) or
raise `ConflictingPipelineError` when the resulting ordering is ambiguous. Merging is preferred
and MUST preserve the original pipeline's capability instances, not rebuild them from scratch.

### 5.2 Unwrapping

`use` respects `__wrapped__`: introspection utilities MUST be able to reach the original callable.
Capio provides `capio.unwrap(fn)` which returns the innermost original callable and
`capio.pipeline(fn)` which returns the built (or built-on-demand) pipeline for introspection.

### 5.3 Metadata attribute

Every decorated callable carries:

```
fn.__capio__ = CapioMeta(
    version=<runtime version>,
    capabilities=[<CapabilityInstanceInfo>, ...],   # in pipeline order, outermost first
    mode="chained" | "composite",
)
```

`CapioMeta` is immutable, picklable, and JSON-serializable (serializer-registered). `capio
inspect f` (RFC-028) reads it. Decorating a function MUST NOT overwrite an existing
`__capio__` — it merges or raises per §5.1.

### 5.4 Context argument

Capabilities receive the **Context** (RFC-006). The wrapped callable itself does NOT receive the
context as an argument unless the user opts in via `use.context()`:

```python
@use.context()
def search(query: str, ctx: CapioContext) -> list[str]:
    ...
```

`use.context()` is a lightweight capability that injects the current Context as the final
positional or a named parameter; it is not part of the default pipeline. This keeps business
signatures clean while offering escape-hatch access.

## 6. Async contract

1. Decorating an `async def` with a sync-only capability MUST raise
   `UnsupportedExecutionKindError` at decoration time (per manifest declaration).
2. Decorating a `def` with an async-only capability is permitted: the runtime adapts the plain
   function into the async pipeline (RFC-024). The reverse (async-only capability on sync
   function) is not permitted.
3. The decorated async callable remains awaitable and MAY be awaited; `await f(...)` and
   `asyncio.run(...)` behave identically.
4. Sync callables decorated only with sync-capable capabilities return plain values; they are
   never turned into coroutines. Mixed pipelines run the whole invocation in the sync or async
   engine as a unit — a sync invocation NEVER internally enters the event loop and an async
   invocation NEVER blocks the loop (RFC-024).

## 7. Typing contract

### 7.1 Goal

`use` decorators must be transparent to static type checkers: the decorated callable's signature,
argument types, and return type are preserved, and sync/async kind is preserved.

### 7.2 Mechanism

All decorators are generic over `P` (ParamSpec) and `R` (return type) and are typed to return the
exact wrapped callable type:

```python
from typing import Callable, ParamSpec, TypeVar
P = ParamSpec("P")
R = TypeVar("R")
F = TypeVar("F", bound=Callable[..., object])
```

A `CapabilityDecorator` is typed as:

```python
class CapabilityDecorator(Protocol[P, R]):
    def __call__(self, fn: Callable[P, R]) -> Callable[P, R]: ...
```

For generators, the return type is `Iterator[R]` / `AsyncIterator[R]`; for classes, the class
type is preserved. The runtime ships `py.typed` and a type-checker test suite (RFC-029) that runs
against mypy and pyright.

### 7.3 Options typing

Each `use.<capability>(...)` factory has fully typed options (dataclasses or TypedDicts) so
checkers validate arguments. Example options types for retry:

```python
class RetryOptions(TypedDict, total=False):
    max_attempts: int
    delay: float | str            # e.g. 0.1 or "100ms"
    backoff: Literal["fixed", "linear", "exponential"]
    jitter: bool | tuple[float, float]
    retry_on: type[BaseException] | tuple[type[BaseException], ...]
    max_delay: float | str
```

Invalid option types MUST be rejected by the checker and by runtime validation.

## 8. Dynamic composition

### 8.1 Conditional capabilities

Capabilities may be applied conditionally without re-decorating:

```python
@use.retry(enable=lambda ctx: ctx.env == "prod", max_attempts=5)
```

`enable` is a predicate over the Context; when it returns False the capability is a transparent
pass-through for that invocation (still measured, still present in the pipeline). This is the
**conditional capability** mechanism (RFC-005 §6).

### 8.2 Programmatic decoration

`with_capabilities(fn, retry={...}, cache={...})` applies a composite set at runtime, returning a
new decorated callable. It is equivalent to the composite decorator form and follows the same
rules, including dedup and ordering.

## 9. Error contract (decoration time)

| Condition | Exception (RFC-025) |
| --------- | -------------------- |
| Unknown capability name | `UnknownCapabilityError` |
| Duplicate non-repeatable capability | `DuplicateCapabilityError` |
| Capability applied to unsupported kind | `UnsupportedExecutionKindError` |
| Conflicting pipeline merge | `ConflictingPipelineError` |
| Invalid/conflicting composite options | `ConfigurationError` |
| Missing dependency at build | `DependencyResolutionError` |
| Any plugin/capability runtime failure | `CapabilityException` subtypes |

No capability failure at decoration time may be swallowed silently; all must be typed and
catchable.

## 10. Stability guarantees

1. The behavior of `use.<name>` for the base capability names is governed by semver (RFC-032):
   adding a new base capability is minor; removing or reordering defaults is major.
2. Default priority order (RFC-005 §4) is part of the public contract and changes only via a
   deprecation RFC.
3. `fn.__capio__` shape is stable across the 1.x line; additions are append-only.
4. The type surface (`py.typed`) is part of the public contract.

## 11. Examples

```python
from capio import use

# Chained, primary form
@use.retry(max_attempts=3, backoff="exponential", jitter=True)
@use.cache(ttl="5m", tags={"search"})
@use.timeout(seconds=2)
@use.trace()
def search(query: str) -> list[str]:
    ...

# Composite form, equivalent pipeline
@use(
    retry={"max_attempts": 3, "backoff": "exponential", "jitter": True},
    cache={"ttl": "5m", "tags": {"search"}},
    timeout={"seconds": 2},
    trace=True,
)
def search2(query: str) -> list[str]:
    ...

# Async
@use.retry(max_attempts=3)
@use.cache(ttl="30s")
async def fetch(url: str) -> bytes:
    ...

# Generator / streaming
@use.retry(max_attempts=3)
@use.cache(ttl="1m")
def stream_rows(query: str) -> Iterator[Row]:
    ...

# Method
class Service:
    @use.retry(max_attempts=2)
    def call(self, payload: dict) -> dict:
        ...
```

## 12. Document Dependencies

- Definitions: RFC-002.
- Pipeline/ordering/conditional rules: RFC-005.
- Context injection: RFC-006.
- Capability interface & execution kinds: RFC-012.
- Config resolution feeding defaults: RFC-009.
- Errors: RFC-025.
- Type testing: RFC-029.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 1.0     | 2026-08-01 | Initial publication. |
