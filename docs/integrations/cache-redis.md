# Redis (and Memcached) cache backends

The `cache` capability (and `dedup`, `llm_cache`, `semantic_cache`,
`prompt_cache`) stores arbitrary Python values under a string key with an
optional TTL. The built-in `cache.memory` backend is a thread-safe dict; this
guide shows how to swap in **Redis** so the cache is shared across processes,
nodes, and restarts.

## The cache backend contract

Any object with these methods is a valid cache backend (mirroring
`capio.backends.memory_cache.MemoryCacheBackend`):

| Method | Meaning |
|---|---|
| `get(key, default)` | Return the stored value, or **`default`** when missing/expired |
| `set(key, value, ttl=None)` | Store `value` under `key` for `ttl` seconds (or forever) |
| `delete(key)` | Remove a key; return `True` if it existed |
| `clear()` | Remove all keys |
| `flush(prefix)` | Remove every key that starts with `prefix`; return the count |

> **Miss sentinel matters.** The `cache` capability calls `get(key)` with no
> default and compares the result against a module-level sentinel object to tell
> a miss apart from a stored `None`. Return your own sentinel (e.g. `_MISSING =
> object()`) on a miss — never return `None`.

Backend methods are called synchronously even inside async pipelines, so use a
sync client.

## 1. Redis backend (`redis-py`)

```bash
pip install redis
```

```python
# redis_cache.py
import pickle

_MISSING = object()


class RedisCacheBackend:
    """A Cache-backend over Redis: TTL via EX, values serialized with pickle."""

    def __init__(self, url="redis://localhost:6379", prefix="capio:cache:", default_ttl=None):
        import redis
        self._r = redis.Redis.from_url(url)
        self._prefix = prefix
        self._default_ttl = default_ttl

    def _key(self, key):
        return self._prefix + str(key)

    def get(self, key, default=_MISSING):
        raw = self._r.get(self._key(key))
        if raw is None:
            return default
        return pickle.loads(raw)            # stored None round-trips correctly

    def set(self, key, value, ttl=None):
        ttl = ttl if ttl is not None else self._default_ttl
        self._r.set(self._key(key), pickle.dumps(value), ex=ttl)

    def delete(self, key):
        return bool(self._r.delete(self._key(key)))

    def clear(self):
        for key in self._r.scan_iter(match=self._prefix + "*"):
            self._r.delete(key)

    def flush(self, prefix):
        count = 0
        for key in self._r.scan_iter(match=self._prefix + str(prefix) + "*"):
            self._r.delete(key)
            count += 1
        return count
```

Register it and point `use.cache` at it:

```python
from capio import default_runtime
from redis_cache import RedisCacheBackend

runtime = default_runtime()                       # the module-level `use` binds here
runtime.bind_backend("cache.redis", RedisCacheBackend(url="redis://localhost:6379"))

@use.cache(backend="cache.redis", ttl="5m")
def get_user(user_id):
    return query_db(user_id)
```

Every decorated call now reads/writes Redis. A cache hit returns the value
without running your function; `cache.hit`/`cache.miss` events fire on the
runtime event bus exactly as with the memory backend.

### Values that aren't picklable

The example uses `pickle` inside the backend, which handles arbitrary objects.
If your cache values are JSON-safe (dicts, lists, strings, numbers) you can use
Capio's registry instead and keep the same trust model everywhere:

```python
import capio.serialize

def set(self, key, value, ttl=None):
    ttl = ttl if ttl is not None else self._default_ttl
    self._r.set(self._key(key), capio.serialize.encode(value, "json"), ex=ttl)

def get(self, key, default=_MISSING):
    raw = self._r.get(self._key(key))
    return default if raw is None else capio.serialize.decode(raw, "json")
```

> Security note (RFC-026 §7): `pickle` is only safe when every process that can
> write to the Redis keys is trusted. For cache data this is usually fine; for
> anything attacker-influenced, use `json` or a custom registered codec.

## 2. Sharing the backend with other cache-like capabilities

The same Redis object satisfies `dedup`, `semantic_cache`, `llm_cache`, and
`prompt_cache` — they all use the identical cache contract:

```python
@use.dedup(backend="cache.redis", ttl="30s")
def send_email(to): ...

@use.llm_cache(backend="cache.redis", ttl="1h")
@use.llm(model="gpt-4o-mini")
def chat(messages): ...

@use.semantic_cache(backend="cache.redis", threshold=0.92)
@use.llm(model="gpt-4o-mini")
def answer(question): ...
```

`dedup` relies on the same miss-sentinel semantics, so Redis works unchanged.
`semantic_cache` stores embedding + response records — keep them JSON/pickle as
above; the capability handles key naming.

## 3. Memcached

Any object exposing the same five methods works. With `pylibmc` the mapping is
nearly one-to-one:

```python
# memcached_cache.py
import pickle

_MISSING = object()


class MemcachedCacheBackend:
    def __init__(self, servers=("127.0.0.1:11211",), prefix="capio:"):
        import pylibmc
        self._mc = pylibmc.Client(servers, binary=True, behaviors={"tcp_nodelay": True})
        self._prefix = prefix

    def _key(self, key):
        return self._prefix + str(key)

    def get(self, key, default=_MISSING):
        value = self._mc.get(self._key(key))
        return default if value is None else pickle.loads(value)

    def set(self, key, value, ttl=None):
        self._mc.set(self._key(key), pickle.dumps(value), time=ttl or 0)

    def delete(self, key):
        return self._mc.delete(self._key(key)) == 1

    def clear(self):
        self._mc.flush_all()

    def flush(self, prefix):
        keys = [k for k in self._mc.get_multi(self._prefix + "*")]  # approximate
        for key in keys:
            self._mc.delete(key)
        return len(keys)
```

Register with `runtime.bind_backend("cache.memcached", MemcachedCacheBackend())`
and use `backend="cache.memcached"`.

## 4. Multiple caches, one app

Backends are named, so you can keep the in-memory default for hot per-process
caches and add Redis for cross-node ones:

```python
runtime.bind_backend("cache.redis", RedisCacheBackend(url=os.environ["REDIS_URL"]))

@use.cache(backend="cache.redis", ttl="15m")   # shared, survives restarts
def pricing(currency): ...

@use.cache(backend="cache.memory", ttl="100ms")  # per-process, ultra-fast
def hot_signal(): ...
```

## 5. Testing without a server

The contract is duck-typed — substitute the memory backend in tests:

```python
runtime.services.bind_replace("cache.redis", MemoryCacheBackend())  # tests only
```

See `docs/usage.md` §5 for rebinding (`bind` vs `bind_replace`) semantics.
