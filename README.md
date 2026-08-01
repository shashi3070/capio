# capio

Composable capabilities for Python: resilience, caching, observability, and AI behavior.

A **capability runtime**, not a decorator library.

[![PyPI version](https://img.shields.io/pypi/v/capio.svg)](https://pypi.org/project/capio/)
[![Python versions](https://img.shields.io/pypi/pyversions/capio.svg)](https://pypi.org/project/capio/)
[![Wheel](https://img.shields.io/pypi/wheel/capio.svg)](https://pypi.org/project/capio/)
[![License](https://img.shields.io/pypi/l/capio.svg)](https://github.com/shashi3070/capio/blob/main/LICENSE)

> Capio is the composable capability layer for Python applications. Apply
> cross-cutting behavior to functions and methods — retries, caching, timeouts,
> circuit breaking, rate limiting, tracing, metrics, logging, and more — with one
> uniform, typed API.
>
> Designed for sync and async Python, generator-friendly, backend-agnostic, and
> fail-safe by default. The architecture is specified in the [RFC documents](https://github.com/shashi3070/capio/tree/main/docs/rfcs) (RFC-000…033).

---

## Table of contents

- [Features](#features)
- [Installation](#installation)
- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Capabilities](#capabilities)
- [Custom capabilities](#custom-capabilities)
- [Context & events](#context--events)
- [CLI](#cli)
- [Documentation](#documentation)
- [Development](#development)
- [License](#license)

## Features

- **8 batteries-included capabilities** — retry, cache, timeout, circuit
  breaker, rate limit, trace, metrics, log — plus a plugin SDK for your own.
- **One uniform API** — `@use.<name>(...)` chained form and the equivalent
  `@use(...)` composite form.
- **Sync, async, and generators** — one decorator works for `def`,
  `async def`, generator, and async-generator functions.
- **Lazy pipelines** — decorating costs microseconds and does no I/O; the
  pipeline is built on the first call and memoized.
- **Fail-safe by default** — cache, trace, metrics, and log degrade gracefully
  when their backend fails; opt in to hard errors with `strict` mode.
- **Cancellation-safe** — timeouts and cancellations are `BaseException`s, so
  retry and circuit-breaker never swallow them.
- **Event bus** — subscribe to structured events (`cache.hit`, `retry.attempt`,
  `circuit.open`, …) without touching your functions.
- **Context injection** — read invocation IDs, environment, and deadlines via
  `use.context()`.
- **CLI** — `capio doctor`, `inspect`, `graph`, and `benchmark`.

## Installation

```bash
pip install capio
```

For development, clone the repo and install with the dev extra:

```bash
git clone https://github.com/shashi3070/capio.git
cd capio
pip install -e ".[dev]"
```

Capio has **no third-party runtime dependencies** beyond the CLI (Typer).

## Quick start

```python
from capio import use

@use.retry(max_attempts=3, backoff="exponential", jitter=True)
@use.cache(ttl="5m")
@use.timeout(seconds=2)
@use.trace()
def search(query: str) -> list[str]:
    ...
```

Capabilities compose as nested scopes; the decorator written highest runs
outermost. The composite form is equivalent (and sorts by priority):

```python
from capio import use

@use(
    retry={"max_attempts": 3, "backoff": "exponential", "jitter": True},
    cache={"ttl": "5m"},
    timeout={"seconds": 2},
    trace=True,
)
def search2(query: str) -> list[str]:
    ...
```

Async works with the same API:

```python
@use.retry(max_attempts=3)
@use.cache(ttl="30s")
@use.circuit_breaker(failure_threshold=5, reset_timeout="30s")
async def fetch(url: str) -> bytes:
    ...
```

Every capability and every option is documented in the
[usage guide](https://github.com/shashi3070/capio/blob/main/docs/usage.md).

## How it works

Decorating a function attaches metadata (`fn.__capio__`) and a thin wrapper —
nothing else. On the **first call**, Capio builds the execution pipeline
(validating configuration, running the capability lifecycle, resolving
backends) and memoizes it. Every call then runs the wrapped function through
that pipeline.

![Capio architecture](https://raw.githubusercontent.com/shashi3070/capio/main/docs/images/architecture.png)

Inside the pipeline, capabilities wrap each other like an onion — each runs,
delegates to the next via `call_next(ctx)`, and resumes on the way back out.
Ordering is outermost-first; the composite form sorts by priority:

![Capio pipeline ordering](https://raw.githubusercontent.com/shashi3070/capio/main/docs/images/pipeline.png)

Capabilities are **fail-safe by default**: if the cache, trace, metrics, or log
backend fails, the invocation proceeds untouched (the failure is emitted as an
event). Under `strict` mode the same failures raise.

A deep, module-by-module code walkthrough lives in the
[architecture document](https://github.com/shashi3070/capio/blob/main/docs/architecture.md).

## Error handling

Capio raises from the `capio.exceptions` module. Two rules to remember:

1. **Timeouts and cancellations are `BaseException` subclasses** — `except
   Exception` will *not* catch them. Catch `CapioTimeoutError` or the base
   `CapioCancelledBase` explicitly.
2. **Everything else derives from `CapabilityException`** (an `Exception`), with
   structured attributes `capability`, `code`, and `extra`.

### Timeout

A timed-out invocation raises `CapioTimeoutError`. It subclasses
`CapioCancelledBase`, so **catch it explicitly** — `except Exception` will not:

```python
import time
from capio import use
from capio.exceptions import CapioTimeoutError, CapioCancelledBase

@use(timeout={"seconds": 2})
def call():
    print("call def is called!!")
    time.sleep(3)

try:
    call()
except CapioTimeoutError as exc:
    print(f"timed out after {exc.seconds}s")   # exc.seconds == 2.0
except CapioCancelledBase:
    print("cancelled")                         # covers any other capio cancellation
```

Two things worth knowing about sync timeouts:

1. The call runs to completion first — `time.sleep(3)` cannot be interrupted at
   2s, so `CapioTimeoutError` is raised *after* the function returns (3s in).
   This is the documented cooperative behavior for sync functions (RFC-018 §3.3).
2. For a **hard** timeout that interrupts at 2s, use an async function — the
   async path uses `asyncio.wait_for` and cancels the underlying task:

```python
import asyncio
from capio import use
from capio.exceptions import CapioTimeoutError

@use(timeout={"seconds": 2})
async def call():
    await asyncio.sleep(3)

async def main():
    try:
        await call()
    except CapioTimeoutError:
        print("hard timeout at 2s")   # fired at 2s; the task is cancelled

asyncio.run(main())
```

Prefer returning a sentinel over raising? Set `return_on`:

```python
@use.timeout(seconds=1, return_on="timeout")   # returns "timeout" instead of raising
def slow2() -> str:
    ...
```

`return_on` and `raise_on=True` are mutually exclusive (config error).

### Retry exhaustion

```python
from capio import use
from capio.exceptions import RetryExhaustedError

@use.retry(max_attempts=3)
def flaky() -> None:
    raise ValueError("boom")

try:
    flaky()
except RetryExhaustedError as exc:
    last_error = exc.__cause__      # the final ValueError
    print(exc.capability)           # "retry"
    print(exc.code)                 # "capio.retry.exhausted"
```

Use `on_final="reraise_original"` to re-raise the first failure instead of
wrapping it.

### Circuit breaker and rate limit

```python
from capio.exceptions import CircuitOpenError, RateLimitExceededError

@use.circuit_breaker(failure_threshold=3)
def call_api() -> dict: ...

@use.rate_limit(limit=1, window="1s")
def tick() -> None: ...

try:
    call_api()
except CircuitOpenError:
    ...  # dependency is unhealthy; fail fast or serve a fallback

try:
    tick()
except RateLimitExceededError as exc:
    print("retry after", exc.retry_after)   # seconds
```

With `use.retry`, these two are **not retried by default** (they are
non-retryable unless you explicitly list them in `retry_on`).

### Configuration errors

These are raised at decoration / first-call time and are all
`CapabilityException`s: `ConfigurationError`, `UnknownCapabilityError`,
`DuplicateCapabilityError`, `ConflictingPipelineError`,
`UnsupportedExecutionKindError`, `CacheKeyError`, `BackendUnavailableError`
(strict mode).

## Capabilities

| Decorator | Capability | Purpose | RFC |
| --------- | ---------- | ------- | --- |
| `use.retry` | Retry | Retry failures with backoff + jitter | [RFC-017](https://github.com/shashi3070/capio/blob/main/docs/rfcs/RFC-017-retry.md) |
| `use.cache` | Cache | In-memory cache with TTL | [RFC-016](https://github.com/shashi3070/capio/blob/main/docs/rfcs/RFC-016-cache.md) |
| `use.timeout` | Timeout | Bound execution time | [RFC-018](https://github.com/shashi3070/capio/blob/main/docs/rfcs/RFC-018-breaker-timeout-ratelimit.md) |
| `use.circuit_breaker` | Circuit Breaker | Fail fast when a dependency is unhealthy | [RFC-018](https://github.com/shashi3070/capio/blob/main/docs/rfcs/RFC-018-breaker-timeout-ratelimit.md) |
| `use.rate_limit` | Rate Limit | Admission control | [RFC-018](https://github.com/shashi3070/capio/blob/main/docs/rfcs/RFC-018-breaker-timeout-ratelimit.md) |
| `use.trace` | Trace | Span recording | [RFC-019](https://github.com/shashi3070/capio/blob/main/docs/rfcs/RFC-019-trace-metrics.md) |
| `use.metrics` | Metrics | Counters + histograms | [RFC-019](https://github.com/shashi3070/capio/blob/main/docs/rfcs/RFC-019-trace-metrics.md) |
| `use.log` | Log | Structured invocation logging | [RFC-020](https://github.com/shashi3070/capio/blob/main/docs/rfcs/RFC-020-logging-audit.md) |

See the [usage guide](https://github.com/shashi3070/capio/blob/main/docs/usage.md)
for every option of every capability (types, defaults, examples).

## Custom capabilities

```python
from capio import Capability, use
from capio.registry import registry

class Audit(Capability):
    name = "audit"
    priority = 550

    def run(self, ctx, call_next):
        result = call_next(ctx)
        print("audit:", ctx.fn_name, result)
        return result

registry.register(Audit)

@use.audit()
def handler(x: int) -> int:
    return x * 2
```

For async-aware capabilities, override `run_async` and `await call_next(ctx)`.

## Context & events

Inject the per-invocation `Context` into any decorated function:

```python
from capio import use

@use.context()
def handler(ctx):
    return ctx.invocation_id, ctx.env, ctx.strict
```

Subscribe to capability events:

```python
from capio import default_runtime

default_runtime().event_bus.subscribe("cache.hit", lambda e: print("hit", e.data))
```

## CLI

```bash
capio doctor              # environment + plugin smoke check
capio inspect mod.fn      # show a decorated function's pipeline
capio graph mod.fn        # render pipeline order
capio benchmark           # run micro-benchmarks against RFC-027 budgets
capio version             # print version
```

If your OS blocks pip-generated console scripts, use `python -m capio.cli ...`.

## Documentation

- **[Architecture guide](https://github.com/shashi3070/capio/blob/main/docs/architecture.md)** — how each part is built: code walkthrough, snippets, and the invocation flow
- **[Usage guide](https://github.com/shashi3070/capio/blob/main/docs/usage.md)** — the full manual: every capability and configuration option
- **[RFCs](https://github.com/shashi3070/capio/tree/main/docs/rfcs)** — the normative architecture: RFC-000 index, RFC-001 vision, RFC-002 core concepts, RFC-003 `use` API, RFC-004…024 architecture, RFC-025 errors, RFC-026 security, RFC-027 performance, RFC-028 CLI, RFC-029 testing, RFC-030 AI/agents/LLM/MCP, RFC-031 reference implementation, RFC-032 roadmap, RFC-033 migration/FAQ
- **[Changelog](https://github.com/shashi3070/capio/blob/main/CHANGELOG.md)**

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
ruff check .
```

Status: **v0.1.0 — MVP reference implementation** (RFC-031). Core capabilities
implemented: `retry`, `cache`, `timeout`, `circuit_breaker`, `rate_limit`,
`trace`, `metrics`, `log`. 67 tests, ruff clean.

## License

MIT — see [LICENSE](https://github.com/shashi3070/capio/blob/main/LICENSE).
