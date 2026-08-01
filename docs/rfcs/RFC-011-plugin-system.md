# RFC-011: Plugin System

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies the **Plugin System**: discovery, registration, loading, validation, dependency
handling, versioning, isolation, priority, ordering, compatibility, lifecycle, and cleanup. A
plugin is any distributable package contributing capabilities, backends, hooks, or config
providers (RFC-002 §4.1). The Plugin SDK (RFC-012) and manifest/packaging (RFC-013) build on this
document; the security model (RFC-026) constrains it.

## 2. Definitions

- **Plugin package**: an installed Python distribution (`capio-redis`, `capio-openai`, ...)
  following the packaging conventions of RFC-013.
- **Plugin instance**: the runtime-managed object holding a loaded plugin's state.
- **Contribution**: a capability, backend, hook, or config provider a plugin registers.

## 3. Discovery

Discovery finds candidate plugin packages. Sources, in order:

1. **Entry points** — packages declaring `capio.plugins` (or `capio.capabilities`,
   `capio.backends`) entry points in their distribution metadata (RFC-013 §3).
2. **`CAPIO_PLUGIN_PATH`** — explicit, colon/`os.pathsep`-separated paths to plugin modules or
   packages (for monorepos and private plugins).
3. **Namespace packages** — packages under the `capio_plugins` namespace importable in the
   environment.
4. **Explicit registration** — `runtime.load_plugin("capio_redis")`.

Discovery is lazy and cached: the loader records candidate metadata (name, version, entry points)
without importing modules, so `capio plugins` (RFC-028) can list candidates without executing
them.

## 4. Validation

Before loading, the loader validates:

| Check | Fails with |
| ----- | ---------- |
| Manifest present and schema-valid (RFC-013 §2) | `PluginManifestError` |
| `api_version` compatible with runtime | `PluginIncompatibleError` |
| Declared Python / runtime version range satisfied | `PluginIncompatibleError` |
| Declared plugin dependencies resolvable (names + version ranges) | `PluginDependencyError` |
| Signature check when signed (RFC-026 §5) | `PluginSignatureError` |
| Permission declaration accepted by runtime policy (RFC-026 §4) | `PluginPermissionError` |
| No duplicate capability/backend names collide (unless namespaced) | `PluginNameCollisionError` |

Validation is a pure function of the manifest + registry state; it does not import plugin code.
Invalid plugins are reported and skipped (default) or fail the runtime start (strict profile).

## 5. Loading & registration

### 5.1 Load sequence

```
1. resolve dependencies (topological order; RFC-002 §2.3 graph)
2. import plugin module(s)
3. read manifest (already validated)
4. register contributions with registries (capabilities, backends, hooks, config)
5. expose decorator names on the `use` facade (RFC-003 §2.3)
6. publish `plugin.loaded` event
```

### 5.2 Registration atomicity

Registration is all-or-nothing per plugin: if any contribution fails to register, the loader
rolls back every registration made by that plugin (RFC-008 buses and RFC-014 registries support
atomic rollback) and reports `plugin.failed`. A half-loaded plugin never exists.

### 5.3 Namespacing

Contributions are keyed by fully-qualified name: `capability:capio_redis.redis_cache`,
`backend:cache.capio_redis`. Base capabilities use the bare name (`retry`). The `use` facade maps
decorator names to fully-qualified capability ids; collisions at the bare-name level resolve per
RFC-014 §4 or raise `PluginNameCollisionError`.

## 6. Plugin dependencies

A plugin declares `dependencies` in its manifest (RFC-013 §2): plugin names with version ranges
and optional flags.

- Dependencies are loaded first (topological order).
- Cycles among plugins raise `PluginDependencyError` at load.
- A dependency that fails to load fails the dependent (hard) or is skipped with `optional: true`
  (the dependent must declare the dependency optional to be allowed to run without it).
- Backend dependencies are resolved through the backend registry (RFC-015) at capability
  configure time, not plugin load time.

## 7. Plugin lifecycle (state machine)

Every plugin moves through the Capability Lifecycle states (RFC-002 §3.6):

```
   discovered → validated → loaded → configured → initialized → running
                                      ↕                              │
                                     suspended ←─────────────────────┤
                                                                    ▼
                                                       stopped → destroyed
```

| Transition | Triggers | Hook (RFC-007 §3.3) |
| ---------- | -------- | -------------------- |
| `discovered` | discovery found candidate | — |
| `validated` | validation passed | — |
| `loaded` | import + registration complete | `on_plugin_load` |
| `configured` | contributions have resolved config | `on_capability_configure` |
| `initialized` | backends connected, resources acquired | `on_capability_initialize` |
| `running` | runtime start / start signal | `on_capability_start` |
| `suspended` | runtime pause / stop signal (RFC-032) | `on_capability_stop` |
| `stopped` | runtime stop | `on_capability_stop` |
| `destroyed` | unload / runtime shutdown | `on_plugin_unload`, `on_capability_destroy` |

- Transitions are events on the Event Bus (`plugin.loaded`, `plugin.configured`, ...).
- `running ↔ suspended` may cycle; `destroyed` is terminal.
- The runtime's `start()` drives `configured → running` for all plugins in dependency order;
  `stop()` reverses it; `shutdown()` drives straight to `destroyed` for all plugins.

## 8. Versioning & compatibility

- **Runtime contract version**: the runtime exposes `api_version` (a single integer bumping on
  breaking interface changes to RFC-012/015 contracts). Plugins declare the range they support.
- **Plugin version**: semver (RFC-032). Plugin-to-plugin dependencies declare ranges.
- **Compatibility matrix**: the runtime publishes a compatibility matrix per release; `capio
  doctor` (RFC-028) checks installed plugins against it.

## 9. Isolation

### 9.1 In-process isolation (v1)

Isolation is about **containment and trust boundaries**, not OS boundaries (RFC-001 §7.9):

- Each plugin's contributions run through the same pipeline contract; a plugin cannot intercept
  another plugin's steps except through declared hooks/events.
- Plugin state is namespaced (`ctx.plugin_state["capio_redis"]`), never shared under bare keys.
- A crashing plugin (unhandled `BaseException`) is contained per the failure contract: the engine
  wraps capability execution, so an unhandled exception inside a capability step surfaces as a
  typed `CapabilityRuntimeError` carrying the plugin name (RFC-025), unless strict mode.
- Blocking probes in `debug` profile detect a plugin blocking the event loop (RFC-024 §5).
- Memory/size guardrails for plugin event payloads (RFC-008) prevent unbounded growth.

### 9.2 Stronger isolation (future)

Subprocess/container sandboxing is out of scope for the core runtime (RFC-001 §7.9) and reserved
for RFC-032's roadmap.

## 10. Priority & ordering

- Capabilities get their pipeline priority from the manifest `priority` plus base defaults
  (RFC-005 §4.2).
- Hook priority mirrors capability priority (RFC-007 §7).
- Event handlers run in subscription order (RFC-008 §2.3).
- Plugin load order: dependency order, then registry registration order, then lexicographic by
  package name (deterministic tie-break, RFC-005 §4.1).

## 11. Cleanup & unload

`unload(plugin_name)`:

```
1. mark suspended
2. emit `plugin.unloading`
3. unsubscribe all event handlers owned by plugin (RFC-008 §4)
4. deregister all hooks owned by plugin (RFC-007 §2.2)
5. remove registrations (registries roll back atomically; RFC-014)
6. remove decorator names owned by plugin from `use` facade
7. dispose scoped/singleton services owned by plugin (RFC-010 §7)
8. release backends owned by plugin
9. emit `plugin.unloaded`
```

Unload is idempotent and safe to call during runtime stop. A plugin that is still referenced by a
memoized pipeline delays physical release until that pipeline is invalidated or the runtime
stops (RFC-005 §8), but is logically removed from discovery and re-resolution immediately.

## 12. Document Dependencies

- Concepts: RFC-002; architecture: RFC-004; lifecycle: RFC-005; hooks: RFC-007; events: RFC-008;
  DI: RFC-010; SDK: RFC-012; manifest/packaging: RFC-013; registries: RFC-014; backends:
  RFC-015; security: RFC-026; CLI: RFC-028; governance: RFC-032.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
