# RFC-001: Vision, Mission, Philosophy, Design Principles, Non-Goals

- **Status:** Accepted
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Vision

Capio is a **capability runtime for Python**: the standard platform for cross-cutting concerns.

FastAPI is not "an HTTP decorator library." SQLAlchemy is not "a database wrapper." Pydantic is not
"a validation decorator." Each became a platform by naming a fundamental concept and providing the
abstractions around it. Capio names the fundamental concept that nearly every production Python
system re-implements by hand:

> The behavior that surrounds a function but is not part of its business logic.

Retry, caching, timeout, tracing, metrics, authentication, validation, rate limiting, circuit
breaking, encryption, auditing, queuing — these are behaviors. They are applied *around* functions
regardless of what the functions compute. Today they are scattered across Tenacity-style wrappers,
cachetools callers, hand-rolled middleware, and framework-specific plugins, each with different
configuration, error handling, and lifecycle rules.

Capio's vision: **one runtime, one configuration model, one lifecycle, one hook system** for every
cross-cutting concern, with a plugin ecosystem so that adding `retry` or `cache` or `kafka` is as
simple as installing a package and adding a decorator.

```
from capio import use

@use.retry(max_attempts=3, backoff="exponential")
@use.cache(ttl="5m")
@use.trace()
def search(query: str) -> list[str]:
    ...
```

## 2. Mission

Capio's mission is to make production-grade function behavior so obvious, so composable, and so
cheap that teams stop hand-rolling infrastructure glue.

Concretely:

1. **Define one capability interface** so that every behavior — core or third-party — is written
   once and composes with every other.
2. **Make sync and async equal citizens.** A capability written once must work for `def`, `async
   def`, generators, and async generators, without duplicated plugin code.
3. **Be zero-cost when unused.** A module that imports `capio` but applies no decorators must pay no
   measurable runtime cost. Import must be lazy and decorator application must not touch
   third-party backends until a capability is actually invoked.
4. **Be backend agnostic.** Caching, metrics, tracing, logging, and event capabilities must switch
   backends (memory → Redis → Kafka → OpenTelemetry) through configuration, not code changes.
5. **Be framework agnostic.** Capio must work identically inside FastAPI, Flask, Django, Celery,
   plain scripts, CLIs, and MCP servers, and must not force a framework on its users.
6. **Provide fail-safe defaults.** Capabilities must degrade safely: a cache backend outage falls
   back to direct execution; a metrics sink failure never breaks the decorated function.
7. **Become the reference implementation of a platform.** Ship an SDK, a CLI, a packaging
   convention, and a contract-test suite so third parties publish `capio-redis`, `capio-kafka`,
   `capio-openai`, and `capio-fastapi` the way people publish database drivers today.

## 3. Philosophy

Capio is governed by a small set of philosophical positions. They are statements about *taste* and
*priority*, and every design decision in the RFCs that follow must be traceable to one of them.

### 3.1 Explicit > Magic

Capabilities are declared at the function, visible at the call site. Configuration is discoverable.
Introspection is a first-class feature (`capio inspect f`). Nothing happens to a function that the
function has not declared — with the single deliberate exception of global runtime defaults, which
are themselves inspectable.

### 3.2 Zero-cost if unused

The platform must not tax code that does not use it. This covers import time, memory footprint, and
decorator application cost. Lazy loading of backends, plugins, and even capability implementations
is mandatory, not an optimization.

### 3.3 Sync and Async are equal citizens

There is no "async version of Capio." There is one capability model, and the engine routes each
invocation to the correct execution path (RFC-024). A plugin that supports only sync is a
second-class citizen and MUST declare that fact in its manifest; the engine still composes it
correctly.

### 3.4 Backend agnostic; framework agnostic

Behavior and infrastructure are separate concerns. The capability defines *what* happens; the
backend defines *where* it happens. The framework integration defines *when* it is relevant.
Capio owns the first, never the third, and offers the second as an interface.

### 3.5 Type-safe first

Public APIs carry complete type annotations. Generic capability types preserve the decorated
function's signature. Static type checkers must be able to reason about `@use.retry()`, including
the sync/async and return-type variance rules in RFC-003.

### 3.6 Plugin-first architecture

Core and ecosystem capabilities share the same interface. Nothing in the runtime is privileged
because it ships with the package. The Open/Closed Principle is absolute: the runtime is open for
extension via the plugin interface and closed for modification of the core pipeline.

### 3.7 Composition over inheritance

Capabilities compose around a function. They do not subclass each other. Reuse happens by
composing capabilities, never by inheriting from them. The execution pipeline (RFC-005) is an
ordered composition, and ordering is a first-class, documented, introspectable concept.

### 3.8 Fail-safe defaults

When a capability cannot do its job, the *function* must still work. Cache miss on backend failure.
Metrics dropped on sink failure. Trace span elided on exporter failure. The platform records the
degradation (it is itself observable) but never converts an infrastructure failure into a business
logic failure, unless the user has explicitly opted into strict mode.

### 3.9 Production ready by default

Reasonable, secure, observable defaults out of the box: bounded memory caches, exponential retry
with jitter, timeouts, structured logging hooks, and an explicit opt-in for anything that sends
data off-host.

## 4. Design Principles

These translate the philosophy into engineering constraints. Every RFC and every line of the
reference implementation is judged against them.

1. **A capability is a function wrapper with metadata.** The unit of extension is the capability
   (RFC-002, RFC-012). Everything — retry, cache, auth, a custom corporate policy — is a capability.
2. **One lifecycle for everything.** Capabilities, plugins, backends, and the runtime share one
   lifecycle: `loaded → configured → initialized → running → suspended → stopped → destroyed`
   (RFC-011). A developer who learns one lifecycle learns the platform.
3. **Context is the spine.** Every invocation flows through a Context (RFC-006) that carries
   request IDs, arguments, state, and handles. Capabilities communicate only through Context; they
   never reach into each other.
4. **Composition is ordered and declared.** The order in which capabilities wrap a function is
   deterministic, documented, and inspectable. Conflict between capabilities is resolved by
   explicit rules (RFC-005), never by accident of import order.
5. **Configuration is layered and discoverable.** Global → environment → project → module →
   function → runtime, with defined precedence (RFC-009). Every layer is inspectable at runtime.
6. **Failure is a contract, not an accident.** All errors flow through one exception hierarchy
   (RFC-025). Capability failures are typed, catchable, and never silently swallowed unless the
   fail-safe default explicitly says so.
7. **Async follows one concurrency model.** The engine maps sync and async invocations onto the
   platform's native primitives (RFC-024). Blocking-capable capabilities declare their flavor so
   the engine can avoid starving event loops.
8. **Observability of Capio itself.** The runtime is instrumented the same way it instruments user
   functions: metrics, traces, and hooks describe both business functions and the runtime's own
   behavior (plugin load times, cache hit rates, pipeline build cost).
9. **Backwards compatibility is a feature.** Public API stability and semver are specified in
   RFC-032. Breaking change requires a deprecation RFC and a migration path.
10. **Documentation is part of the deliverable.** A capability is not done until its RFC section,
    docstring, and example are done.

## 5. Why Capio Exists

The problems Capio solves are real, recurring, and currently fragmented.

### 5.1 The fragmentation problem

A production Python service commonly layers: Tenacity for retry, cachetools for caching,
`func_timeout` or manual `asyncio.wait_for` for timeouts, manual decorators for rate limiting,
manual `logging` / structlog for structured logs, OpenTelemetry manual spans, hand-rolled auth
middleware, hand-rolled dedup. Each library has its own config model, its own exception types, its
own lifecycle, its own sync/async split, and its own integration story. Replacing one layer means
rewriting call sites. Capio unifies the model.

### 5.2 The "another decorator library" problem

Many libraries implement one capability excellently but cannot compose. Tenacity retries; it does
not cache. cachetools caches; it does not retry. Frameworks provide middleware stacks, but only
inside that framework, and only for HTTP. Capio's claim is not "a better retry" — it is **the
composition**: any behavior, any function, any framework, one model.

### 5.3 The async duplication problem

Writing a capability that works for both sync and async functions typically doubles the code and
quadruples the bugs. Capio's engine runs the same capability logic on both paths (RFC-024),
eliminating the duplication at the platform level.

### 5.4 The observability gap

Hand-rolled wrappers silently swallow errors, skew timing, and produce no traces or metrics.
Capio wraps every capability in the same observability contract (RFC-019), so a composed function
comes with traces, metrics, and logs by default — not by accident.

### 5.5 The plugin economics problem

Building a Redis cache wrapper is a weekend. Publishing it so the ecosystem can reuse it — with a
contract, tests, versioning, and docs — is the part nobody does. Capio's SDK, manifest, and
contract-test suite (RFC-012, RFC-013, RFC-029) make publishing a capability a packaging step, not
a research project.

## 6. Comparison with Existing Libraries

Capio is not the first tool in this space, and it does not pretend to replace every library. This
section clarifies the boundary with the major alternatives. A full migration and mapping matrix
appears in RFC-033.

### 6.1 Tenacity

Tenacity is a retry library. Capio's retry capability (RFC-017) offers comparable retry semantics
(backoff strategies, jitter, retry predicates, async support) but retry is one capability among
many, composed under the same config and lifecycle as everything else. Users migrating from
Tenacity keep their retry behavior; they gain composition. Capio does not require migration —
`use` is designed so retry configuration is expressible in familiar terms.

### 6.2 cachetools

cachetools is a caching library. Capio's cache capability (RFC-016) provides a backend-agnostic
interface with memory and Redis backends, TTL, tags, and stampede protection. cachetools is a
valid cache backend for Capio's cache capability via the backend SDK (RFC-015); Capio does not
re-implement a better cachetools so much as give caches a shared contract and composition.

### 6.3 dependency-injector

`dependency-injector` is a DI framework. Capio ships a lightweight service container (RFC-010)
sufficient for capability dependency resolution — the platform's *internal* need. Capio does not
seek to be a general application DI framework, and it interoperates with whatever DI the
application already uses.

### 6.4 OpenTelemetry

OpenTelemetry is an observability standard and SDK. Capio is not a competing tracing or metrics
standard. Capio's trace and metrics capabilities (RFC-019) are *front-ends* that emit to
OpenTelemetry (and other sinks) via trace/metrics backends. If you already use OTel, Capio makes
applying spans and counters to decorated functions declarative and composes them with retry/cache.
If you do not, Capio provides simple local sinks.

### 6.5 FastAPI / Flask / Django middleware

Framework middleware is framework-bound and request-bound. Capio capabilities are function-bound
and transport-agnostic. Framework integrations (`capio-fastapi`, etc.) adapt framework events into
the Capio lifecycle (RFC-013, RFC-033) rather than being the primary API.

### 6.6 Celery / task frameworks

Celery manages task distribution. Capio's queue, workflow, and cron capabilities (RFC-023)
complement task frameworks by adding behavior *around* individual callables; Capio is not a broker
or a scheduler.

### 6.7 functools / wrapt / decorator

These are low-level building blocks. Capio is built on top of them; it does not compete with them.
The `use` decorator preserves signatures via modern `__wrapped__` conventions and the typing
protocols in RFC-003.

### 6.8 Summary position

Capio replaces the *glue* — the ad-hoc stack of single-purpose decorators, each with its own
model — not the individual engines. Where a specialized library is excellent at one thing
(OTel, Tenacity, cachetools, structlog), Capio composes with it as a backend or capability rather
than reinventing it.

## 7. Non-Goals

The following are explicitly out of scope. "Out of scope" means Capio will not provide first-class
support and will actively avoid coupling its core abstractions to them.

1. **Capio is not an HTTP framework.** No routing, no server, no request/response model. Framework
   integration is an adapter, not the core.
2. **Capio is not a general application DI framework.** The service container (RFC-010) is sized
   for capability dependencies. Application-level DI is the application's business.
3. **Capio is not a distributed computing platform.** No worker nodes, no scheduler leader
   election, no cluster state. Distributed execution is the domain of Celery/Dask/Prefect;
   Capio composes with them.
4. **Capio is not a data-processing engine.** No DataFrame, no query planner, no batch graph.
5. **Capio is not a replacement for the language.** No code generation, no AST rewriting beyond
   what decorators already do, no monkey-patching of third-party functions at import time by
   default.
6. **Capio is not an observability standard.** It emits to standards (OTel, Prometheus, etc.); it
   does not define a wire protocol.
7. **Capio is not an ORM or database.** Database capabilities wrap and manage; they do not
   implement persistence engines.
8. **Capio is not a security authority.** It provides auth/policy *capabilities* that call
   authorities (OIDC, JWT verification, Keycloak, custom providers); it is not itself an identity
   provider or a secrets vault.
9. **Capio does not promise plugin sandboxing as an OS boundary in v1.** Plugin isolation (RFC-026)
   is about containment and trust boundaries in-process; subprocess/container isolation is a
   deployment concern and out of scope for the core runtime.
10. **Capio will not silently change business behavior.** Fail-safe defaults apply to the
    *capability layer*, never to the function's return value or side effects without explicit
    opt-in.
11. **Capio is not an AI framework.** It does not train models, implement model internals, or
    replace orchestrators such as LangChain/LlamaIndex. It provides the *behavior layer* around
    AI: resilience, caching, cost control, guardrails, observability, audit, agents, and MCP
    integration (RFC-030), composing with providers and orchestrators as backends.
12. **Capio is not an MCP server/client framework.** The MCP protocol itself belongs to the MCP
    specification; Capio's `capio-mcp` integration adapts MCP transports to its capability
    model (RFC-030 §7) without redefining the protocol.

## 8. Success Criteria

Capio achieves its vision when:

- A new capability can be published by a third party following one SDK and one manifest, and be
  consumed with one decorator line.
- A function can be taken from a framework-agnostic script into FastAPI, Celery, and an MCP server
  with identical capability behavior.
- The sync/async duplicate-implementation antipattern disappears in the ecosystem Capio serves.
- The failure of any backend is observable and never silently breaks business functions.
- "How does this function behave?" is answerable by `capio inspect`, not by reading source.

## 9. Document Dependencies

- Defines concepts formalized in RFC-002.
- Justifies the API contract in RFC-003.
- Constraints fulfilled by the runtime (RFC-004) and lifecycle (RFC-005).
- Non-goals enforced by the security (RFC-026) and platform (RFC-032) RFCs.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 1.0     | 2026-08-01 | Initial publication. |
