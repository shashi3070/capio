# RFC-016: Cache Capability

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies the **Cache capability**: its API, cache-key contract, backends, TTL, tags,
stampede protection, compression, encryption, serialization, invalidation, and its interaction
with other capabilities (retry, streaming, conditional). It implements the `CacheBackend`
interface (RFC-015 §3.1) and is consumed via `@use.cache(...)` (RFC-003).

## 2. API

```python
@use.cache(
    ttl="5m",                          # duration (RFC-009 §5.3); None = no expiry
    key="cache_key",                   # key builder name; default "auto"
    key_prefix="service:search",       # static namespace
    namespace=None,                    # dynamic namespace builder (ctx) -> str
    backend="memory",                  # backend name (RFC-015 §4)
    serialize="json",                  # serializer name (RFC-014)
    tags={"search"},                   # tags for group invalidation
    compress="zlib",                   # None | "zlib" | "lz4" | "snappy"
    encrypt=False,                     # symmetric encryption (RFC-026)
    cache_when=None,                   # (ctx, result) -> bool  store predicate
    cache_on_error=False,              # cache exceptions (RFC-025) too
    stampede="probabilistic",          # None | "singleflight" | "probabilistic"
    stale_ttl="30s",                   # serve-stale window after ttl expires
)
def search(query: str) -> list[str]: ...
```

All options are optional; `@use.cache()` is valid (uses defaults).

## 3. The cache-key contract

### 3.1 Auto key builder

The default `key="auto"` builds a deterministic key from:

```
namespace = key_prefix or f"{fn_module}.{fn_name}"
key = hash(namespace
           + "|" + canonical_args(args, kwargs)
           + "|" + method_identity)          # self/cls identity when configured
```

- **Canonicalization**: arguments are normalized (sorted kwargs, stable reprs, dataclasses →
  dicts, objects → id-based when unhashable but identity-relevant) so that semantically equal
  calls map to the same key and None-safe.
- **Serialization of arguments**: `json`-safe types canonicalize to JSON; others use the
  registered serializer's canonical form; un-representable arguments raise `CacheKeyError`
  (RFC-025) rather than silently producing a wrong key.
- **Sensitivity**: by default, secrets and values matching `*password*` etc. are **excluded**
  from key building (they produce a constant) so keys never embed secrets (RFC-026).

### 3.2 Custom key builders

`key="my_builder"` resolves a registered key builder (RFC-014 validator registry). A builder
signature: `(ctx, args, kwargs) -> str`. Custom builders MUST return a stable string and MUST NOT
raise unless they are intentionally aborting caching (then raise `CacheKeyError` to bypass
caching for that invocation, which is observed, not fatal).

### 3.3 Method identity

For methods, key includes `self` identity only when `key_scope="instance"` (default is
`key_scope="class"` — cache shared across instances unless instance scoping is requested). Opt-in
`key_scope="instance"` keys include the instance id (`id(self)`), making per-instance caches.

## 4. Behavior

### 4.1 Hit path

```
cache step (RFC-005 §4.2, before retry in default order):
  key = build_key(ctx)
  hit = backend.get(key)                     # fail-safe: on error → miss (RFC-005 §7)
  if hit is not None:
      emit cache.hit
      value = serializer.decode(decrypt(decompress(hit)))
      return value                            # bypasses the inner steps entirely
  emit cache.miss
  → call_next(ctx)
```

- Hits return without executing the wrapped function (short-circuit at the cache step).
- A stored `None` is indistinguishable from "no entry" — capabilities that return `None` MUST be
  wrapped by a value sentinel (the SDK stores a marker) or use `cache_on_error`/cache-when
  config; documented in the capability reference.

### 4.2 Miss path

```
result = call_next(ctx)
if cache_when is None or cache_when(ctx, result):
    if cache_on_error and result is an exception: store the exception type+args
    else: store serialize(encrypt(compress(result)))
    emit cache.stored
return result
```

- Serialization of non-serializable results: if no registered serializer can represent the
  result type, the SDK raises `SerializationError` (RFC-025) — but **after** the function has
  returned, so the caller still gets the correct result; the failure is observed and the value is
  not cached. Caching must never alter the returned value.
- Generator results (RFC-012 §4): with `realize="list"` the stream is fully realized and cached;
  with `realize="none"` (default) the stream is passed through uncached (lazily) and `cache.stored`
  is not emitted.

## 5. TTL, staleness, and eviction

- `ttl=None`: no expiry (store forever). Eviction then depends on backend policy (e.g. LRU/max
  size for memory backend, RFC-015).
- `stale_ttl`: after `ttl` elapses, a hit within the stale window is served while a background
  refresh recomputes the value (serve-stale). Refresh is singleflight to avoid stampede (§6).
- Backend eviction events (`cache.evicted`) are emitted by the backend when it performs LRU/max
  evictions (memory backend) so observers can track cache health (RFC-008).
- Tag-based invalidation: `cache.invalidate(tags=...)` and `use.cache.invalidate` helpers remove
  every entry tagged. The backend stores a tag→keys index; `flush(prefix)` clears namespace
  prefixes.

## 6. Stampede protection

A **cache stampede** is many concurrent requests computing the same key after expiry. Three modes:

| Mode | Behavior |
| ---- | -------- |
| `singleflight` | One invocation computes; others wait on the same in-flight promise (in-process, per-key). Correct across sync/async via a per-key waiter table. |
| `probabilistic` (default) | Randomized early expiry: entries expire a random fraction early proportional to load, smoothing recompute bursts. `XFetch`-style (RFC-027 benchmarks compare). |
| `none` | No protection; recompute every miss. |

- `singleflight` needs cross-process coordination for multi-worker deployments; the backend may
  provide it (Redis locks) via the `lock` interface (RFC-015 §3.9); otherwise it is
  per-process only (documented).

## 7. Backends

Backends implement `CacheBackend` (RFC-015 §3.1). Canonical set:

| Backend | kind.id | Notes |
| ------- | ------- | ----- |
| Memory | `cache.memory` | LRU + max size + TTL sweep; built-in. |
| Redis | `cache.redis` | TTL, tags, bulk, distributed locks (plugin `capio-redis`). |
| SQLite | `cache.sqlite` | durable local cache (plugin). |
| DiskCache | `cache.diskcache` | file-backed (plugin). |
| Memcached | `cache.memcached` | (plugin). |
| Valkey / Dragonfly | `cache.valkey` / `cache.dragonfly` | redis-protocol (plugin). |

Switching backend = changing config (`cache: {backend: redis}`), no code change (RFC-015 §4).

## 8. Compression & encryption

- `compress`: applied **before** encryption, after serialization: `raw → serialize → compress →
  encrypt → store`. Decode reverses: `decrypt → decompress → deserialize`.
- `encrypt=True` requires a key from the `secret` backend (RFC-026); the capability never holds
  the key in plaintext config. Non-secret caches MUST NOT silently encrypt (config error).
- Compressed/encrypted entries carry metadata headers (algorithm, serializer version) so the
  decoder can be version-tolerant (RFC-032).

## 9. Interaction with other capabilities

- **Cache + retry** (RFC-017): default composite order is cache outside retry — cache miss
  computes once under retry; transient failures are not cached. Users may chain retry outside
  cache to re-check the cache per attempt (RFC-005 §9.2).
- **Cache + conditional** (`enable`): a disabled cache is a transparent pass-through.
- **Cache + auth/validation**: cache sits inside auth and validation (they run before the cache
  step in default order, RFC-005 §4.2), so unauthorized/invalid calls never hit the cache.
- **Cache + streaming**: RFC-012 §4.1 — lazy streams are uncached by default.
- **Cache + LLM/semantic**: see RFC-030 (llm_cache, semantic cache build on this capability).

## 10. Events, metrics, hooks

- Events: `cache.hit`, `cache.miss`, `cache.stored`, `cache.evicted`, `cache.invalidated`,
  `cache.failed` (RFC-008 §2.5).
- Metrics: `cache.hit_rate`, `cache.get_latency_ms`, `cache.set_latency_ms`,
  `cache.backend_errors_total` (RFC-019).
- Hooks: `before_cache_lookup`, `after_cache_lookup`, `before_cache_store` (RFC-007 §3.2).

## 11. Document Dependencies

- API: RFC-003; pipeline order: RFC-005; context/carrier: RFC-006; config: RFC-009; backends:
  RFC-015; serialization/encryption: RFC-022, RFC-026; observability: RFC-019; performance:
  RFC-027; AI cache: RFC-030.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
