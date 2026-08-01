# Capio Integrations

Capio is dependency-free and runs entirely in-process by default: the eight
built-in backends (`cache.memory`, `trace.console`, `metrics.null`, `log.stdio`,
`audit.memory`, `store.memory`, `broker.memory`, `queue.memory`) are in-memory
implementations of well-defined protocols.

Everything external — Redis, OpenTelemetry/Grafana/Loki, identity providers, or
a database — plugs in through the **same two extension points**:

1. **Backends** — objects bound by name in the runtime's service container,
   resolved by capabilities via `self.backend(name)` (see `docs/usage.md` §5).
   Implement the small protocol a capability expects and register it:
   `runtime.bind_backend("cache.redis", MyRedisCache())`.
2. **Providers** — plain callables passed as options (`use.auth(provider=...)`,
   `use.llm(provider=...)`, embedders, policies). The library calls them; you
   supply the logic.

This directory documents how to connect real infrastructure using those points.
The code samples use popular third-party drivers as **optional dependencies** —
Capio never requires them; only the integration you enable does.

## Guide index

| Topic | File | What you get |
|---|---|---|
| Cache backends | [cache-redis.md](cache-redis.md) | Redis (and Memcached) as the `cache`/`dedup`/LLM-cache backend, with serialization handled for you |
| Observability | [observability-otlp-grafana-loki.md](observability-otlp-grafana-loki.md) | Export `trace`/`metrics`/`log`/`audit` to OpenTelemetry (OTLP), Prometheus/Grafana, and Loki |
| Authentication | [auth-jwt-oidc-oauth-rbac.md](auth-jwt-oidc-oauth-rbac.md) | JWT, OIDC, OAuth 2.0, and RBAC providers for `use.auth` |
| Databases | [databases.md](databases.md) | SQLite, PostgreSQL, MySQL, MongoDB as `store`/`audit` backends |
| End-to-end examples | [examples.md](examples.md) | Combined, runnable services that tie several integrations together |

## The one idea that matters

Every backend protocol below is **a handful of methods**. Matching the shape is
all that is required; there is no base class to import, no Capio dependency in
your backend module. For example, any object with `get(key, default)` and
`set(key, value, ttl=None)` is a valid cache backend:

```python
from capio import default_runtime
from capio.runtime import CapioRuntime

class MyCache:
    def get(self, key, default=None): ...
    def set(self, key, value, ttl=None): ...

runtime = default_runtime()
runtime.bind_backend("cache.my", MyCache())

# later, anywhere in the app:
@use.cache(backend="cache.my", ttl="1m")
def expensive(x): ...
```

If your runtime is a fresh `CapioRuntime(...)` instead of the default, create the
facade bound to it before decorating:

```python
runtime = CapioRuntime("prod")
use_rt = use.__class__(runtime)
```

## Backend protocols at a glance

| Backend | Capabilities that use it | Methods to implement |
|---|---|---|
| `cache.*` | `cache`, `dedup`, `llm_cache`, `semantic_cache`, `prompt_cache` | `get(key, default)` · `set(key, value, ttl=None)` · `delete(key)` · `clear()` · `flush(prefix)` |
| `store.*` | `idempotent`, `memory`, `rag`, `ingest`, `cron`, `publish` (outbox) | `put(ns, key, value, ttl=None)` · `get(ns, key, default)` · `delete(ns, key)` · `items(ns)` · `scan(prefix)` · `sequence(ns)` · `clear(ns=None)` |
| `trace.*` | `trace` | `emit(span: dict)` |
| `metrics.*` | `metrics` | `record(metric: dict)` |
| `log.*` | `log` | `log(level: int, message: str, **fields)` |
| `audit.*` | `audit` | `append(record: dict)` · `query(*, actor, action, limit)` · `verify()` |
| `broker.*` | `publish`, `consume` | `publish(topic, payload)` · `consume(topic, group)` · `peek` · `size` · `clear` |
| `queue.*` | `queue` | `put(envelope)` · `get()` · `task_done()` · `start_workers` · `stop_workers` |

Read the matching guide for a full walkthrough of each protocol and a concrete
driver-backed implementation.
