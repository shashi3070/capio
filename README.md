# capio

Composable capabilities for Python: resilience, caching, observability, and AI behavior.
A **capability runtime**, not a decorator library.

> Capio is the composable capability layer for Python applications. Apply cross-cutting
> behavior to functions and methods — retries, caching, timeouts, circuit breaking, rate
> limiting, tracing, metrics, logging, and more — with one uniform, typed API.
>
> Designed for sync and async Python, generator-friendly, backend-agnostic, and
> fail-safe by default. The architecture is specified in `docs/rfcs/` (RFC-000…033).

## Status

**v0.1.0 — MVP reference implementation** (RFC-031). Core capabilities implemented:
`retry`, `cache`, `timeout`, `circuit_breaker`, `rate_limit`, `trace`, `metrics`, `log`.

## Install

```bash
pip install -e ".[dev]"
```

## Quick Start

```python
from capio import use

@use.retry(max_attempts=3, backoff="exponential", jitter=True)
@use.cache(ttl="5m")
@use.timeout(seconds=2)
@use.trace()
def search(query: str) -> list[str]:
    ...
```

Capabilities compose as nested scopes; the decorator written highest runs outermost.
The composite form is equivalent:

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
async def fetch(url: str) -> bytes:
    ...
```

## Capabilities

| Decorator | Capability | Purpose | RFC |
| --------- | ---------- | ------- | --- |
| `use.retry` | Retry | Retry failures with backoff + jitter | RFC-017 |
| `use.cache` | Cache | In-memory cache with TTL | RFC-016 |
| `use.timeout` | Timeout | Bound execution time | RFC-018 |
| `use.circuit_breaker` | Circuit Breaker | Fail fast when a dependency is unhealthy | RFC-018 |
| `use.rate_limit` | Rate Limit | Admission control | RFC-018 |
| `use.trace` | Trace | Span recording | RFC-019 |
| `use.metrics` | Metrics | Counters + histograms | RFC-019 |
| `use.log` | Log | Structured invocation logging | RFC-020 |

## Custom capabilities

```python
from capio import Capability

class Audit(Capability):
    name = "audit"
    priority = 550

    def run(self, ctx, call_next):
        result = call_next(ctx)
        print("audit:", ctx.fn_name, result)
        return result

# register and use
from capio.registry import registry
registry.register(Audit)
# from capio import use
# @use.audit()
```

## CLI

```bash
capio doctor              # environment + plugin smoke check
capio inspect mod.fn      # show a decorated function's pipeline
capio graph mod.fn        # render pipeline order
capio benchmark           # run micro-benchmarks against RFC-027 budgets
capio version             # print version
```

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
ruff check .
```

## Documentation

- **[`docs/architecture.md`](docs/architecture.md)** — how Capio is built, code walkthrough and invocation flow
- **[`docs/usage.md`](docs/usage.md)** — the full manual: every capability and configuration option

The normative architecture lives in `docs/rfcs/`:

- **RFC-000** index and reading order
- **RFC-001** vision, **RFC-002** core concepts, **RFC-003** `use` API
- **RFC-004…024** architecture (runtime, lifecycle, context, config, DI, plugins, backends)
- **RFC-025** errors, **RFC-026** security, **RFC-027** performance, **RFC-028** CLI,
  **RFC-029** testing, **RFC-030** AI/agents/LLM/MCP, **RFC-031** reference implementation,
  **RFC-032** roadmap, **RFC-033** migration/FAQ

## License

MIT
