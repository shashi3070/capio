# RFC-004: Runtime Architecture & Execution Engine

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies the internal architecture of the Capio **runtime**: its components, their
responsibilities, how they communicate, process/thread/task topology, and the design rules that
keep the platform consistent as the ecosystem grows. It is the blueprint for RFC-031 (reference
implementation).

## 2. High-Level Component Map

```
                     ┌────────────────────────────┐
   application       │         CAPIO RUNTIME      │
   code ────────►    │                            │
   @use.retry()      │   ┌──────────────────────┐ │
        │            │   │   Use Facade (API)   │ │  RFC-003
        ▼            │   └──────────┬───────────┘ │
   Decorated         │              │             │
   Callable          │              ▼             │
        │            │   ┌──────────────────────┐ │
        └────────────┼──►│  Pipeline Builder    │ │  RFC-005
                     │   └──────────┬───────────┘ │
                     │              │             │
                     │              ▼             │
                     │   ┌──────────────────────┐ │
                     │   │   Execution Engine   │ │  RFC-024
                     │   └──────────┬───────────┘ │
                     │              │             │
                     │   ┌──────────▼─────────┐   │
                     │   │  Context Manager   │   │  RFC-006
                     │   └──────────┬─────────┘   │
                     │              │             │
                     │   ┌──────────▼─────────┐   │
                     │   │  Hook Dispatcher   │   │  RFC-007
                     │   └──────────┬─────────┘   │
                     │              │             │
                     │   ┌──────────▼─────────┐   │
                     │   │    Event Bus       │   │  RFC-008
                     │   └──────────┬─────────┘   │
                     │              │             │
                     │   ┌──────────▼─────────┐   │
                     │   │   Registries       │   │  RFC-014
                     │   └──────────┬─────────┘   │
                     │              │             │
                     │   ┌──────────▼─────────┐   │
                     │   │  Service Container │   │  RFC-010
                     │   └──────────┬─────────┘   │
                     │              │             │
                     │   ┌──────────▼─────────┐   │
                     │   │  Plugin Loader     │   │  RFC-011
                     │   └────────────────────┘   │
                     └────────────────────────────┘
```

### 2.1 Component responsibilities (summary)

| Component | Responsibility | RFC |
| --------- | -------------- | --- |
| `Use` Facade | Public decorator API; registry-backed capability access; metadata. | RFC-003 |
| Pipeline Builder | Resolve config, graph, order; instantiate capability instances; build pipeline. | RFC-005 |
| Execution Engine | Run invocations through pipelines on the sync or async path; manage reentrancy. | RFC-024 |
| Context Manager | Create/enter/exit per-invocation Context; bind handles and propagation carriers. | RFC-006 |
| Hook Dispatcher | Invoke registered hook callbacks at defined stages with defined ordering/fail rules. | RFC-007 |
| Event Bus | Publish/subscribe immutable events; ordering and backpressure. | RFC-008 |
| Registries | Indexed collections of capabilities, backends, plugins, serializers, validators, events. | RFC-014 |
| Service Container | DI resolution of services with lifetimes. | RFC-010 |
| Plugin Loader | Discover, validate, load, unload plugins; drive their lifecycle. | RFC-011 |
| Config Store | Layer the config sources and produce resolved configuration. | RFC-009 |

## 3. Core Design Rules

The following rules constrain every component. They are the architectural expression of
RFC-001's design principles.

1. **Single-runtime assumption, explicit exceptions.** One `CapioRuntime` is the default per
   process. Applications that genuinely need isolated runtimes (tests, multi-tenant embedders)
   instantiate additional `CapioRuntime` objects; isolation MUST be explicit, and the default
   `use` facade binds to the default runtime.
2. **Acyclic communication.** Component dependencies point one way: API → pipeline → engine →
   context → hooks/events → registries → container → plugins. No component reaches "up" the
   diagram. Enforcement is by package layering (RFC-031) and lint rules.
3. **Laziness everywhere.** Nothing is constructed, imported, or connected until an invocation
   needs it. Importing `capio` MUST NOT import plugins, open backends, or read user config
   (RFC-027).
4. **Immutability at boundaries.** Configuration, manifests, metadata, and events are immutable
   once created. Mutation flows only through defined lifecycle transitions.
5. **Reentrancy.** A decorated callable may be invoked concurrently and re-entrantly. Capability
   instances MUST be reentrant or MUST declare a non-reentrant contract that the engine enforces
   with per-invocation state in the Context (RFC-006, RFC-024).
6. **No thread-affinity of logic.** All logic is written thread-agnostic; concurrency is the
   engine's concern (RFC-024). Components never assume "called from the main thread" or "from an
   asyncio task."
7. **One failure contract.** All component failures raise typed exceptions from the hierarchy of
   RFC-025; nothing may raise bare `Exception` for a Capio-specific condition.
8. **Observability of the runtime itself.** The runtime is itself a Capio-decorated surface:
   pipeline build time, plugin load time, registry lookups, and hook dispatch are measured and
   traced (RFC-019, RFC-027).

## 4. Component Detail

### 4.1 `Use` Facade

The facade is a thin, immutable view over the capability registry plus the Pipeline Builder.

- Attribute access (`use.retry`) resolves the capability name in the registry; unknown names raise
  `UnknownCapabilityError`.
- Calling the resolved factory returns a `CapabilityDecorator` (RFC-003).
- `use(...)` composite form dispatches to the composite builder.
- The facade caches resolved names but never caches pipelines (pipelines are per-callable).
- The facade is the only component the application imports directly, guaranteeing a single stable
  surface.

### 4.2 Pipeline Builder

Input: a wrapped callable plus the set of declared capability options (chained or composite).
Output: an `ExecutionPipeline` (RFC-005).

Pipeline Build runs in these phases:

1. **Collect.** Gather capability declarations from decoration metadata.
2. **Resolve config.** Merge function options with module/project/env/global layers (RFC-009);
   validate against each capability's schema.
3. **Graph.** Build the capability dependency graph (RFC-002 §2.3); verify acyclicity.
4. **Order.** Determine execution order from explicit chaining, else declared priority (RFC-005 §4);
   resolve conflicts by the conflict rules.
5. **Instantiate.** Create capability instances via the Service Container (RFC-010), injecting
   declared dependencies (other instances, backends, config).
6. **Wire hooks.** Register each instance's hooks with the Hook Dispatcher.
7. **Compile.** Emit an executable ordered step list; attach metadata (`fn.__capio__`).

The build is memoized per (callable identity, resolved-config fingerprint). Concurrent first-calls
to the same callable MUST build exactly once (double-checked lock with a per-callable guard).

### 4.3 Execution Engine

The engine runs a pipeline for one invocation. Two engines exist behind one interface: the **sync
engine** and the **async engine** (RFC-024). They share the same step-list abstraction; only the
scheduling differs.

- The engine creates the Context (via Context Manager), walks the steps in order
  (RFC-005), guarantees after-hooks and cleanup run on exception/cancellation, and returns or
  raises.
- The engine enforces capability execution-kind compatibility (RFC-003 §3.3).
- The engine NEVER silently swallows a capability failure; it applies the fail-safe degradation
  rules (RFC-005 §7) or raises typed errors in strict mode.

### 4.4 Context Manager

Per invocation, the Context Manager:

1. Allocates request/correlation IDs (derived from propagation carriers when present).
2. Binds the service handles (logger, tracer, metrics, cache handle, auth principal).
3. Registers the Context in the propagation scope (RFC-006 §6) so child invocations inherit it.
4. Guarantees scope exit and ID finalization even on exception.

### 4.5 Hook Dispatcher

- Maintains a per-runtime, ordered hook table: hook name → list of (priority, owner, callback).
- Invokes hooks in priority order; defines failure semantics per hook (RFC-007 §5): some hooks may
  short-circuit the invocation, others may only observe.
- Is invoked by the engine at the fixed stages; hooks never invoke the engine directly.

### 4.6 Event Bus

- Pub/sub over immutable events. Subscribers register per event type with ordering guarantees
  (RFC-008).
- Emitted synchronously by default with defined backpressure; MAY be async in the async engine
  path with a bounded queue.
- The bus carries both capability events (`retry.scheduled`) and runtime events
  (`pipeline.built`), so runtime health is observable (RFC-008, RFC-019).

### 4.7 Registries

One registry per artifact kind (RFC-014). Registries are read-mostly, guarded, and name-keyed;
they expose lookups and listings and integrate with the lifecycle so that `unload` removes an
artifact atomically from every registry that references it.

### 4.8 Service Container

A lazily-initialized DI container (RFC-010). Provides capabilities their dependencies. Built to
satisfy only the runtime's and plugins' needs: services, lifetimes, lazy proxies, and circular
detection. It is exposed so plugins can register their own services; it is intentionally NOT a
general application DI framework (RFC-001 §7).

### 4.9 Plugin Loader

- Discovers plugins from entry points (RFC-013 §3), the `CAPIO_PLUGIN_PATH` environment variable,
  and namespace packages.
- Validates manifests (RFC-013), enforces version compatibility, and computes the plugin
  dependency graph.
- Drives plugin lifecycle (RFC-011) and coordinates with the security model (RFC-026).

### 4.10 Config Store

Layers configuration sources and exposes a read-only merged view plus change notification for
dynamic config (RFC-009).

## 5. Runtime Object Model

### 5.1 `CapioRuntime`

```python
class CapioRuntime:
    def __init__(self, *, name: str = "default", plugins: Sequence[str] = (),
                 config: ConfigSourceLike | None = None) -> None: ...
    # accessors
    @property
    def config(self) -> ConfigView: ...
    @property
    def container(self) -> ServiceContainer: ...
    @property
    def registries(self) -> RegistrySet: ...
    # lifecycle
    def start(self) -> None: ...     # loads plugins, initializes capabilities, opens backends
    def stop(self) -> None: ...      # reverses initialization; idempotent
    def shutdown(self) -> None: ...  # hard teardown; not restartable
```

- The default runtime is created lazily on first `use` access and bound to the default
  `CapioRuntime`. `capio.runtime()` returns the active default.
- Only one runtime may own the global `use` facade; additional runtimes use their own facade
  (`runtime.use`) or integration with context propagation (RFC-006).
- `start()`/`stop()` are re-entrant and idempotent; `shutdown()` is terminal.

### 5.2 ExecutionPipeline

```python
class ExecutionPipeline:
    callable: Callable[..., Any]
    capabilities: tuple[CapabilityInstanceInfo, ...]   # outermost first
    steps: tuple[Step, ...]                             # ordered executable steps
    mode: Literal["sync", "async"]                     # derived, not fixed (RFC-024)
    version: str
    def run(self, context: Context) -> Any: ...
```

The pipeline is immutable after build. Its `steps` are the compiled form consumed by the engines.

## 6. Topology & Concurrency Placement

### 6.1 Process model

Capio is an in-process library. It owns no processes, threads, or event loops beyond the standard
primitives it is given:

- **Sync engine:** runs on the calling thread; uses short-lived worker threads ONLY for declared
  blocking backends (RFC-024 §5), never by default.
- **Async engine:** runs in the caller's event loop; never spawns its own loop.

### 6.2 Thread and task topology

| Concern | Placement |
| ------- | --------- |
| Capability instance state | per-CapabilityInstance; synchronized via container-scoped guards if shared |
| Invocation state | per-Context, never shared |
| Registry mutations | runtime-level lock; read-mostly (copy-on-write snapshots) |
| Event delivery | caller context by default; bounded async queue in async path |
| Backend I/O | synchronous in sync path; via executor only when a backend declares `blocking` (RFC-024) |

### 6.3 Multi-runtime isolation

Isolated runtimes share nothing: separate registries, container, config, hook table, and event
bus. This is the mechanism by which test suites and embedded hosts isolate plugin effects.

## 7. Communication Between Components

- **Requests (down):** direct method calls, always through interfaces defined in RFC-031's module
  layout. No component bypasses the pipeline to reach a capability.
- **Events (up/sideways):** through the Event Bus only. No component calls another component's
  subscriber directly.
- **Hooks:** only the engine invokes the Hook Dispatcher; only the dispatcher calls callbacks.
- **Config changes:** the Config Store notifies via a dedicated config-change event (RFC-008,
  RFC-009); subscribers rebuild affected pipelines on a versioned fingerprint.

## 8. Startup Sequence

```
1. Import `capio`            → no side effects; creates no runtime.
2. First `use` access         → lazily create default runtime.
3. Runtime.start() (implicit) → Config Store loads layers → Plugin Loader discovers & validates
                                → registries populated → plugin lifecycle: load → configure
                                → capabilities register onto `use` facade.
4. First decoration          → facade resolves capability; Pipeline Builder memoizes graph.
5. First invocation          → build-on-demand completes → engine runs.
```

Everything is lazy except step 3's plugin validation, which is bounded and cached (RFC-027).

## 9. Design Rules Enforced at Build Time (CI)

- Package import graph acyclicity (layering rule 2) via a lint rule.
- No import of plugin/backend modules at `capio/__init__`.
- No socket/file opens during decoration.
- Runtime observability counters present on every component (RFC-019 contract test).
- Reentrancy contract test on every core capability (RFC-029).

## 10. Document Dependencies

- Principles: RFC-001; concepts: RFC-002; API: RFC-003.
- Lifecycle & pipeline: RFC-005; context: RFC-006; hooks: RFC-007; events: RFC-008.
- Config: RFC-009; DI: RFC-010; plugins: RFC-011; registries: RFC-014; backends: RFC-015.
- Concurrency: RFC-024; errors: RFC-025; performance: RFC-027; implementation: RFC-031.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
