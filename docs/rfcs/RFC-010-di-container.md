# RFC-010: Dependency Injection Container

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies the **Service Container**, Capio's dependency-injection scope: how services
are registered, resolved, scoped by lifetime, built lazily, and how circular dependencies are
handled. It is sized for the platform's needs — capabilities, backends, and runtime services
resolve their dependencies through it — and it is deliberately **not** a general application DI
framework (RFC-001 §7.2). Applications keep their own DI; Capio composes with it at the edges.

## 2. Services

### 2.1 Definition

A **Service** is a runtime-managed object resolvable by name and type. Services include:

- capability instances (RFC-012)
- backend instances (RFC-015)
- config views (RFC-009)
- handles: logger, tracer, metrics, cache, auth (RFC-006 §2)
- plugin-registered services

### 2.2 Registration

```python
container.register("retry", RetryCapability, lifetime="singleton", deps=["config", "logger"])
container.register("cache.backend", RedisCacheBackend, lifetime="singleton",
                   deps=["config"], on_init="connect")
container.register_factory("request.scope", lambda ctx: MyScope(), lifetime="scoped")
container.register_instance("logger", logger_obj)   # pre-built
container.register("llm_cache", LLMCache, lifetime="singleton", deps=["cache.backend", "serializer"])
```

Registration forms:

| Form | Meaning |
| ---- | ------- |
| `register(name, cls, deps=...)` | lazy construction by class |
| `register_instance(name, obj)` | pre-built object, always returned as-is |
| `register_factory(name, fn, deps=...)` | lazy callable producing the service |
| `register_alias(name, target)` | another name for an existing service |

## 3. Lifetimes

| Lifetime | Semantics |
| -------- | --------- |
| `singleton` | One instance per runtime; created once (lazily), shared by all invocations. MUST be thread/task-safe. Default for capabilities and backends. |
| `transient` | New instance per resolution; used for per-use helpers. |
| `scoped` | One instance per **scope**: a propagation scope (RFC-006 §6), i.e. per top-level invocation (or per context explicitly). Reused within the same scope; destroyed at scope exit. |
| `factory` | Each resolution calls the factory; caller owns lifecycle. |

Scoped services are the mechanism for "per-request" state without global mutable state, and they
keep reentrancy correct: nested invocations get the parent's scope (inherited) unless a new scope
is entered (RFC-006 §6).

## 4. Resolution

### 4.1 Resolution algorithm

```
resolve(name, scope=None):
    if cached for (name, lifetime, scope): return it
    match lifetime:
      singleton: build once under runtime lock; double-checked lock
      scoped:    lookup scope store; build if absent
      transient/factory: build now
    build: instantiate deps recursively (resolve each dep), then call cls(**deps)
    post-init: run registered init hooks (e.g. backend connect)
    return service
```

- Construction happens inside the **pipeline build** phase for capabilities (RFC-005 stage 5)
  and lazily for handles (RFC-006 §2.2).
- The container is **read-mostly** after runtime start; registrations may be added by plugins at
  load time, but a service name is immutable once resolved (rebinding raises `ServiceAlreadyBound`
  unless the service declares `rebind: true` for tests).

### 4.2 Lazy loading

- Services are constructed only when first resolved; construction must not recurse infinitely
  (cycle detection, §5).
- Backend construction performs **no I/O** at build; I/O (connect) happens at the `initialize`
  lifecycle transition (RFC-011) or lazily on first use (`lazy: true`), per the backend's
  manifest.

### 4.3 Handle resolution from Context

The Context's handles (`ctx.logger`, `ctx.tracer`, `ctx.cache`, ...) resolve through the
container on first access and are cached on the Context (RFC-006 §2.3). Resolution failure raises
`ContextBindingError`; a capability may degrade per RFC-005 §7.

## 5. Circular dependency handling

- The container detects cycles at resolution time via an in-progress resolution set.
- On cycle detection, it raises `DependencyCycleError` (RFC-025) with the cycle path
  (`retry → cache.backend → retry`).
- **Lazy breakers:** a service may declare a dependency as `lazy` (a `LazyService` proxy resolved
  on first attribute access) to break optional cycles:

```python
container.register("a", A, deps=["b"], lazy_deps=["c"])
container.register("c", C, deps=["a"])   # resolves via lazy proxy
```

- Optional dependencies (`deps_optional=["logger"]`) resolve to a configured no-op default when
  absent, enabling fail-safe degradation (RFC-005 §7).

## 6. Scoping rules

1. Default scope is the propagation scope of the current Context (RFC-006 §6). Outside any
   Capio invocation, resolution uses an ambient scope.
2. A top-level invocation entering a decorated callable establishes the scope; scoped services
   bind to it and are disposed at context cleanup (RFC-005 stage 10).
3. `scoped` services MUST implement `close()` if they hold resources; the container disposes them
   in reverse creation order at scope exit.
4. Child tasks/threads that need scoped services must enter a propagation scope explicitly
   (RFC-006 §7).

## 7. Containers per runtime

- Each `CapioRuntime` owns one container (RFC-004 §5.1).
- Plugin unload removes services the plugin registered (only those with `owner=<plugin>`),
   atomically with registries (RFC-011 §7).
- Isolated runtimes have isolated containers; nothing crosses runtimes except explicit carrier
   propagation (RFC-006 §10).

## 8. Testability

- In the `test` profile (RFC-009 §7), the container accepts test doubles via `override`:

```python
with container.override({"cache.backend": FakeCacheBackend()}):
    result = service.run(...)
```

- Overrides apply to resolution within the `with` scope and are the primary mechanism for
  contract tests (RFC-029) to substitute backends.

## 9. Document Dependencies

- Architecture: RFC-004; lifecycle stages using resolution: RFC-005; context handles: RFC-006;
  config values: RFC-009; capability instances: RFC-012; backends: RFC-015; lifecycle of
  services: RFC-011; errors: RFC-025; tests: RFC-029.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
