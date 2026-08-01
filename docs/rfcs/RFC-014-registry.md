# RFC-014: Registry System

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies the **Registry System**: the indexed collections through which the runtime
looks up capabilities, backends, plugins, serializers, validators, and events. Registries are the
runtime's "phone book" — read-mostly, name-keyed, lifecycle-aware, and rollback-capable (RFC-011
§5.2). The `use` facade (RFC-003), plugin loader (RFC-011), pipeline builder (RFC-005), and
backends (RFC-015) all read from these registries.

## 2. Registry kinds

| Registry | Key | Value | Populated by |
| -------- | --- | ----- | ------------ |
| Capability | capability id | `CapabilityInfo` (class, schema, priority, supports, degradation) | core + plugins |
| Backend | backend id (`kind.name`) | `BackendInfo` (class, interface kind) | core + plugins |
| Plugin | plugin name | `PluginInfo` (manifest, state) | loader |
| Serializer | mime/name | `SerializerInfo` (encode/decode) | core + plugins |
| Validator | validator name | `ValidatorInfo` (validate fn, schema) | core + plugins |
| Event | event type pattern | subscribers (RFC-008) | runtime + plugins |
| Hook | hook name | registrations (RFC-007) | runtime + plugins |

### 2.1 Common entry shape

```python
@dataclass(frozen=True, slots=True)
class RegistryEntry:
    key: str                    # fully-qualified id
    name: str                   # bare name for facade
    owner: str                  # owning plugin or "core"
    version: str
    priority: int | None
    payload: Any                # the registered object (class, subscriber, encoder, ...)
    state: Literal["registered", "active", "suspended", "removed"]
    metadata: Mapping[str, Any] # manifest metadata
```

## 3. Registry contract

### 3.1 Lookup and iteration

- `lookup(name)` — exact bare-name match; ambiguous names (RFC-011 §5.3) resolve via owner or
  raise `AmbiguousNameError`.
- `lookup_qualified(id)` — exact fully-qualified match.
- `list(kind=None, owner=None, state="active")` — filtered iteration.
- All lookups are read-only snapshots; registries are optimized for concurrent reads (copy-on-write
  snapshots, RFC-004 §6.2).

### 3.2 Registration

- `register(entry)` validates: key uniqueness, type correctness, owner non-empty.
- Duplicate registration: replacing an existing `core` entry raises `NameCollisionError`; a plugin
  may replace a previous version of its own entry during an upgrade (atomic swap).
- Registration participates in the plugin loader's atomic transaction (RFC-011 §5.2): all entries
  of a plugin commit together or roll back together.

### 3.3 Removal / suspension

- `suspend(key)` / `resume(key)`: used by plugin suspension and pipeline invalidation
  (RFC-005 §8).
- `remove(key)`: marks removed; memoized pipelines holding the entry keep a reference until
  invalidated (RFC-011 §11), but new lookups fail.
- Every registry emits events (`registry.registered`, `registry.removed`, `registry.suspended`)
  on the Event Bus (RFC-008).

## 4. Name resolution & collisions

### 4.1 Fully-qualified ids

- Core: `retry`, `cache`, `trace`, ... (bare).
- Plugin: `<plugin>.<name>` for contributions (RFC-011 §5.3); backends: `<kind>.<name>` e.g.
  `cache.redis`.

### 4.2 Bare-name facade resolution

The `use` facade resolves bare names to fully-qualified ids:

1. If exactly one active entry owns the bare name → use it.
2. If multiple → the one registered by the higher-priority plugin wins if it declares
   `overrides`; otherwise `AmbiguousNameError` with the candidate list.
3. If none → `UnknownCapabilityError` (RFC-003 §2.3).

Resolution is deterministic and independent of import order (RFC-005 §4.3).

## 5. Registry lifecycle

- Registries initialize empty at runtime creation, are populated at `start()` (core first, then
  plugins in dependency order), and are cleared at `shutdown()`.
- Plugin unload removes all entries with `owner=<plugin>` atomically (RFC-011 §7).
- `capio registry` CLI (RFC-028) lists any registry with state and ownership, enabling
  diagnosis of collisions and leaked registrations.

## 6. Concurrency & performance

- Reads: lock-free snapshot reads (frozen dict swaps). Writes: single-writer lock.
- All lookups are O(1) hash; snapshot copy is O(entries) and amortized (only on mutation).
- Registry access is instrumented (`registry.lookup_ms` metric, RFC-019) and MUST be negligible
  (<1µs typical) — a registry lookup must never dominate an invocation (RFC-027).

## 7. Document Dependencies

- Concepts: RFC-002 (§4.2); architecture: RFC-004; plugin loading: RFC-011; SDK: RFC-012;
  backends: RFC-015; events: RFC-008; errors: RFC-025; performance: RFC-027; CLI: RFC-028.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
