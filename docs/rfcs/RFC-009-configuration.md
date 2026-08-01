# RFC-009: Configuration System

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies Capio's configuration model: the **layers** (sources) of configuration, their
**precedence**, supported **formats**, **validation**, **profiles**, **dynamic/remote**
configuration, and the **resolution** algorithm that produces the immutable config bound to each
invocation's Context (RFC-006 §2).

## 2. Design goals

1. **One model everywhere** — capabilities, backends, plugins, and the runtime share one
   configuration mechanism.
2. **Layered with predictable precedence** — any setting has exactly one winning source.
3. **Discoverable** — every layer and the final merged value are inspectable at runtime and via
   `capio config` (RFC-028).
4. **Typed and validated** — invalid configuration fails at apply time with a typed
   `ConfigurationError` (RFC-025), never silently at runtime.
5. **Env- and file-friendly** — works in containers (env vars), in code (dicts/Python), and in
   repos (YAML/TOML/JSON).
6. **Dynamic** — remote/config-sync can update values at runtime with controlled invalidation.

## 3. Configuration layers (lowest → highest precedence)

| # | Layer | Source | Precedence |
| - | ----- | ------ | ---------- |
| 1 | Built-in defaults | `capio.config.defaults` (code) | lowest |
| 2 | Profile defaults | selected profile's defaults (RFC-002 §5.2) | |
| 3 | Environment | `CAPIO_*` env vars, `--capio-*` CLI flags | |
| 4 | Global config file | `capio.yaml`/`capio.toml`/`capio.json` (cwd/project root) | |
| 5 | Project config | section in project's own config (e.g. `pyproject.toml [tool.capio]`) | |
| 6 | Module config | per-module config declaration (RFC-012) | |
| 7 | Function decorator options | `@use.retry(max_attempts=3)` (RFC-003) | |
| 8 | Runtime-provided | programmatic `runtime.config.update(...)`; dynamic/remote | |
| 9 | Per-invocation override | explicit `with_capio`/call-time overrides (RFC-005 §6.3) | highest |

**Rule:** for a given key, the highest-precedence layer that defines it wins. Merge is
shallow-over-deep per namespace: nested sections merge recursively; scalars are replaced, not
merged.

### 3.1 Why this order

- File config is checked in, but environment must override it in containers (twelve-factor).
- Function decorator options override everything file/env because they are the most specific
  author intent.
- Per-invocation overrides are narrow and explicit — highest, but only for declared-overridable
  keys (RFC-005 §6.3).

## 4. Formats

### 4.1 Supported formats (global config file)

| Format | Filename | Notes |
| ------ | -------- | ----- |
| YAML | `capio.yaml`, `capio.yml` | default; supports anchors (no code execution). |
| TOML | `capio.toml`, `pyproject.toml [tool.capio]` | preferred for Python projects. |
| JSON | `capio.json` | strict JSON only. |

All formats MUST be parsed without code execution (no arbitrary Python). Plugin config lives
under a top-level namespace keyed by the plugin/capability name:

```yaml
# capio.yaml
runtime:
  profile: prod
  strict: false
retry:
  max_attempts: 3
  backoff: exponential
capio_redis:          # plugin namespace (capio-redis)
  url: redis://localhost:6379/0
  pool_size: 10
```

### 4.2 Environment variables

Naming: `CAPIO_` + section + `_` + key, uppercase. Nested keys use `__`:

- `CAPIO_PROFILE=prod`
- `CAPIO_RETRY_MAX_ATTEMPTS=3`
- `CAPIO_CAPIO_REDIS_URL=redis://...` (plugin namespace preserves plugin name)

Type coercion from strings uses the target schema's types (int/float/bool/JSON for mappings and
lists). Unparseable values raise `ConfigurationError` at resolution time with the variable name.

### 4.3 Python config

Programmatic config is accepted as plain nested dicts or Pydantic-style models (if present):

```python
runtime.update_config({
  "retry": {"max_attempts": 3, "jitter": True},
  "trace": {"backend": "opentelemetry", "service": "api"},
})
```

## 5. Schema and validation

### 5.1 Schemas

Every capability, backend, and plugin declares a **config schema** in its manifest/code
(RFC-012, RFC-013). Schemas support:

- Scalars with types and ranges: `int`, `float`, `str`, `bool`, `enum`, duration-strings
  (`"5m"`, `"100ms"`, `"2h"`), byte-size strings (`"10mb"`).
- Nested structures, lists, and maps.
- Defaults (always the built-in layer's value).
- `enable` predicates (RFC-005 §6.1) as a first-class key.
- Strictness: schemas reject unknown keys by default (typo safety); a capability may opt into
  `additional: true` for forward compatibility.

### 5.2 Validation timing

| Layer | When validated |
| ----- | -------------- |
| Built-in defaults | at import of the capability module (schema itself is static). |
| Env/CLI | at runtime config load (fail fast at `start`). |
| Files | at runtime config load. |
| Decorator options | at decoration time (RFC-003 §3.1: eager fail-fast allowed). |
| Per-invocation override | at invocation time (narrow, validated against overridable subset). |

### 5.3 Durations and sizes

- Durations accept both `float` seconds and duration strings; the resolved value normalizes to a
  float of seconds (monotonic deadlines use this float).
- Byte sizes normalize to ints of bytes.
- Duration strings: `<n>(ms|s|m|h|d)`; multiple units allowed (`"1h30m"`).

## 6. Resolution algorithm

For a decorated callable, resolution at pipeline build (RFC-005 stage 3) computes:

```
resolve(fn, cls=None, self_or_cls=None) -> FrozenConfig:
    merged = {}
    for layer in [defaults, profile, env, global_file, project, module, decorator]:
        merged = deep_merge(merged, layer_for(fn, cls))
    merged = apply_runtime_overrides(merged)          # layer 8
    return validate_and_freeze(merged)                # FrozenConfig (immutable)
```

- **Module config** is contributed by a module-level `capio_config = {...}` variable or via the
  capability's manifest namespace; it applies to all decorated callables defined in that module.
- The result is a `FrozenConfig`: an immutable, hashable, deep-frozen mapping that serves as the
  memoization key for pipeline build (RFC-004 §4.2 fingerprint).
- Resolution MUST NOT touch backends or plugins; it is pure merging + validation.

### 6.1 Fingerprinting

The config fingerprint is a deterministic hash of the frozen config plus the runtime/plugin
version set. Pipeline memoization keys on it (RFC-005 §8), so a config change automatically
invalidates affected pipelines without manual calls.

## 7. Profiles

A **Profile** is a named set of overrides (RFC-002 §5.2). Core profiles:

| Profile | Purpose | Typical changes |
| ------- | ------- | --------------- |
| `dev` | local development | verbose logs, relaxed rate limits, `strict: false`, debug hooks on |
| `test` | automated tests | deterministic retry delay=0, metrics to null sink, no external backends |
| `prod` | production | `strict: false`, all backends on, telemetry on |
| `benchmark` | perf runs | all observability off, logging disabled |
| `minimal` | embedded/library use | zero plugins, no auto-discovery |
| `debug` | diagnosis | ring-buffer events, blocking probes on |

- Selected by `CAPIO_PROFILE`, `--capio-profile`, or config file `runtime.profile`.
- Only one profile is active per runtime. Profile defaults sit between built-in defaults and env
  (layer 2), so env still overrides profile.
- Profiles are composable via `runtime.profiles += ["audit"]` (a profile may include others).

## 8. Dynamic & remote configuration

### 8.1 Mechanisms

- `runtime.config.update(...)` (programmatic) and config-file watch (mtime/digest polling).
- **Remote sources** (opt-in plugin, e.g. `capio-consul`, `capio-apollo`, HTTP config endpoint):
  poll or push an immutable snapshot, delivered as a validated layer-8 update.
- Remote updates are applied **atomically**: the Config Store swaps the resolved view and bumps
  the fingerprint; the pipeline builder invalidates affected pipelines (RFC-005 §8).

### 8.2 Delivery guarantees

- Updates are never applied mid-invocation: in-flight invocations keep the config they resolved
  at stage 3.
- A failed remote fetch keeps the last good snapshot and emits `config.remote_failed`; it never
  degrades to an empty config.
- Secret material (RFC-026) is never part of remote snapshots; secrets remain in env/secrets
  manager, referenced by key.

## 9. Inspectability

`capio config` (RFC-028) prints, per setting: effective value, winning layer, and layer source.
`runtime.config.as_tree()` returns the same programmatically. `ctx.config` is the frozen
per-invocation view; `ctx.config.source("retry.max_attempts")` reports provenance.

## 10. Security rules

1. Unknown keys in validated namespaces are rejected (typo protection) unless `additional: true`.
2. No arbitrary code execution from any config format.
3. Secrets: any key matching `*password*`, `*token*`, `*secret*`, `*api_key*`, or declared
   `secret: true` in schema is masked in inspection output and in context snapshots (RFC-006 §9,
   RFC-026).
4. Remote config is validated against the same schemas before acceptance; a schema-invalid
   snapshot is rejected wholesale.

## 11. Document Dependencies

- Concepts: RFC-002 (§5); decorator options: RFC-003; pipeline build: RFC-004/005; context
  binding: RFC-006; events for `config.changed`: RFC-008; DI of config values: RFC-010;
  capability/manifest schemas: RFC-012/013; errors: RFC-025; CLI: RFC-028.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
