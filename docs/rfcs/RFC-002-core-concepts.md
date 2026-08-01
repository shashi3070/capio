# RFC-002: Core Concepts & Glossary

- **Status:** Accepted
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC defines every term Capio uses. Definitions here are canonical: all later RFCs MUST use
terms exactly as defined below, and any term used with a special meaning MUST appear in this
glossary before first use in a later document. Where a section references "the RFC-NNN detailed
design," it points to the document that elaborates the concept.

Terms are listed in dependency order: each term is defined using only earlier terms and plain
language.

## 2. The Capability Layer

### 2.1 Capability

A **Capability** is a self-contained unit of cross-cutting behavior that can be applied to a
function, class, method, or generator. It is the fundamental extension unit of Capio. A capability
declares:

- a **name** and **version**,
- a **lifecycle** (RFC-011) it participates in,
- **configuration schema** validated at apply time,
- **hooks** it subscribes to (RFC-007),
- **dependencies** on other capabilities and backends (RFC-004, RFC-010),
- an **execution wrapper** (sync and/or async, RFC-024) that runs around the wrapped callable.

Retry, cache, timeout, trace, auth, validation, rate limit, circuit breaker, and every third-party
plugin are all capabilities. There is no privileged "core" capability from the runtime's
perspective; core ones just ship in the base package (RFC-012).

### 2.2 Capability Instance / Configuration

A **Capability Configuration** (or *instance config*) is the resolved set of parameters for one
application of a capability to one callable. It is the merge of function-level options, module
config, project config, environment, and global defaults, per the precedence rules in RFC-009.
The runtime instantiates a **Capability Instance** from a capability class plus its resolved
configuration at pipeline build time.

### 2.3 Capability Graph

The **Capability Graph** is the dependency view of the capabilities applied to a callable: which
capability depends on which, and in what order they must initialize. It is computed at pipeline
build time (RFC-004, RFC-005), is acyclic by construction, and is inspectable via `capio graph`.

### 2.4 Capability Manifest

A **Capability Manifest** is the machine-readable description of a capability package: name,
version, entry point, declared capabilities, dependencies, hooks, permissions, backends, and
metadata (RFC-013). It is typically stored as `capability.yaml` inside a plugin package.

## 3. The Runtime Layer

### 3.1 Capability Runtime

The **Capability Runtime** (or *the runtime*) is the Capio engine that: discovers and loads
plugins; resolves configuration; builds execution pipelines; runs invocations; and dispatches
lifecycle, hook, and event traffic. It is a singleton per Python process by default, isolated per
`CapioRuntime` instance when the application needs multiple, independent runtimes (RFC-004).

### 3.2 Execution Pipeline

An **Execution Pipeline** is the concrete, ordered chain of capability instances wrapped around a
specific callable for a specific invocation path. It is built once per decorated callable (and
per resolved configuration) and reused across invocations. The pipeline defines the nesting order,
the execution order, the dependency order, and the teardown order (RFC-005).

### 3.3 Context

The **Context** (formally *invocation context*) is the per-invocation state container. One Context
exists per call into a decorated function (RFC-006). It carries request and correlation IDs,
positional and keyword arguments, function and class metadata, capability state, plugin state,
handles (logger, tracer, metrics, cache, auth principal), a cancellation token, environment,
timing, and thread/process identity. Capabilities communicate **only** through the Context.

### 3.4 Context Propagation

**Context Propagation** is the mechanism by which a Context (or a lightweight carrier of its
identity and state) flows across concurrency and transport boundaries: threads, asyncio tasks,
processes, Celery, Kafka, HTTP, WebSocket, MCP, CLI. The rules and carriers are specified in
RFC-006.

### 3.5 Execution Lifecycle

The **Execution Lifecycle** is the ordered set of stages every invocation passes through, from
"function called" through "result returned": context creation, config resolution, plugin and
dependency resolution, pipeline build, before-hooks, execution, after-hooks, cleanup, metrics
flush, trace finish (RFC-005). It is distinct from the **Capability Lifecycle**, which governs a
capability's own states across time (RFC-011).

### 3.6 Capability State Machine

The **Capability Lifecycle** (state machine) moves every capability and plugin through the states:
`loaded → configured → initialized → running → suspended → stopped → destroyed`. Transitions are
events, observable via the event bus (RFC-008), and specified per artifact in RFC-011.

## 4. The Extension Layer

### 4.1 Plugin

A **Plugin** is an installed, distributable package that contributes one or more capabilities,
backends, hooks, or configuration providers to the runtime. Plugins are discovered, validated,
loaded, and unloaded by the plugin system (RFC-011), carry a manifest (RFC-013), and are subject
to the security model (RFC-026). Examples: `capio-redis` (a backend), `capio-openai` (an AI
capability), `capio-fastapi` (an integration).

### 4.2 Registry

A **Registry** is an indexed collection of artifacts of one kind: capability registry, backend
registry, plugin registry, serializer registry, validator registry, event registry (RFC-014).
Registries provide lookup by name, listing, validation, and lifecycle coordination. "The
Registry" without qualification means the union of all registries exposed by a runtime.

### 4.3 Backend

A **Backend** is a concrete implementation of an infrastructure interface behind a capability:
where a cache lives (memory, Redis, SQLite, disk), where metrics go (Prometheus, OTel, StatsD),
where traces go (OTel, Jaeger, Zipkin), where events go (Kafka, RabbitMQ, NATS). Backends are
swappable via configuration without code changes (RFC-015). A backend is a special kind of
plugin-visible service; it has the same lifecycle as capabilities but a narrower contract.

### 4.4 Hook

A **Hook** is a named, typed extension point at a fixed stage of the execution or capability
lifecycle. Hooks are the *pull* model: the runtime invokes registered hook callbacks at defined
points (`before_execution`, `after_cache_lookup`, `on_exception`, ...). The complete hook
catalogue and ordering are in RFC-007.

### 4.5 Middleware

A **Middleware** is a framework-facing adapter that connects a host framework's lifecycle
(FastAPI, Flask, Django, Celery, MCP) to Capio's context and pipeline model. Middleware is how
Capio stays framework agnostic: it is always an add-on integration, never a core concept
(RFC-013, RFC-033).

### 4.6 Event

An **Event** is an immutable, timestamped record of something that happened: `retry.scheduled`,
`cache.hit`, `plugin.loaded`, `capability.started`. Events are emitted on the **Event Bus**;
subscribers observe them. Events are the *push* model, complementary to hooks' pull model
(RFC-008).

### 4.7 Event Bus / Message Bus

The **Event Bus** is the in-process pub/sub channel through which the runtime and plugins emit and
consume Events. The **Internal Message Bus** is the request/reply channel for commands between
runtime components (e.g., registry → plugin loader). RFC-008 defines both, their ordering,
delivery guarantees, and backpressure.

## 5. Configuration & Services

### 5.1 Configuration Source

A **Configuration Source** is one layer of the configuration precedence ladder: built-in defaults,
environment, project config files, module config, function decorator options, and runtime-provided
dynamic/remote config. Each source contributes a typed partial; the merge produces the resolved
configuration (RFC-009).

### 5.2 Profile

A **Profile** is a named set of configuration defaults selected at runtime
(`dev`, `test`, `prod`, `benchmark`, `minimal`, `debug`). Profiles change which capabilities are
on by default, verbosity, and fail-safe strictness (RFC-009).

### 5.3 Service Container

The **Service Container** is the runtime's dependency-injection scope: a registry of services
(capabilities, backends, loggers, tracers, metric sinks, config) with lifetimes — **singleton,
transient, scoped, factory** — and lazy, circular-safe resolution (RFC-010).

### 5.4 Dependency Resolution

**Dependency Resolution** is the process by which the runtime satisfies a capability's declared
dependencies (other capabilities, backends, services) at pipeline build time, using the service
container and the capability graph, raising `DependencyResolutionError` on cycles or missing
providers (RFC-010, RFC-025).

## 6. Execution Concepts

### 6.1 Wrapped Callable

The **Wrapped Callable** (or *the target*) is the user function, method, class, generator, or
async generator to which a set of capabilities is applied. The result of applying capabilities is
a **Decorated Callable**.

### 6.2 Decorator

A **Decorator** is the public API artifact that binds capabilities to a wrapped callable. The
primary forms are the chained `@use.retry()` family and the composite `@use(...)` form; the exact
contract is RFC-003. A decorator is *pure*: it performs no I/O and touches no backend at
decoration time.

### 6.3 Pipeline Build

**Pipeline Build** is the (typically once, lazily) performed construction of the Execution
Pipeline for a decorated callable: graph computation, config resolution, instance construction,
dependency wiring, and hook registration. The first invocation triggers the build; subsequent
invocations reuse it.

### 6.4 Invocation

An **Invocation** is one call of a decorated callable, including async coroutine runs. Each
invocation is bound to exactly one Context, runs through one pipeline, and produces one result or
exception. Invocations of the same decorated callable may be concurrent; the pipeline and
capability instances MUST be reentrant or explicitly state their concurrency constraints
(RFC-024).

### 6.5 Cancellation Token

A **Cancellation Token** is the standard handle carried in the Context by which an invocation can
be cooperatively cancelled. Sync execution uses token checks; async execution binds to task
cancellation (RFC-006, RFC-024).

## 7. Data Concepts

### 7.1 Serializer

A **Serializer** converts values between native Python objects and a storable/transmittable form.
Serializers are registered (RFC-014) and used by cache, queue, event, and RPC capabilities.
Canonical serializers: JSON, pickle (opt-in), msgpack, cloudpickle (opt-in), and custom.

### 7.2 Cache Key / Cache Entry

A **Cache Key** is the deterministic, hashable representation of the invocation identity for
caching purposes, produced by the cache key builder from function identity plus normalized
arguments. A **Cache Entry** is the stored pair of key, value, and metadata (TTL, tags, staleness)
(RFC-016).

### 7.3 Trace / Span

A **Trace** is the end-to-end record of an invocation chain; a **Span** is one named, timed unit
within a trace (one capability phase or the wrapped call itself). Capio emits spans via trace
backends; the span model follows OpenTelemetry conventions where possible (RFC-019).

### 7.4 Metric

A **Metric** is a named, typed, timestamped measurement emitted by the runtime or by capabilities
(counters, gauges, histograms) to metric backends (RFC-019).

### 7.5 Policy

A **Policy** is a declarative rule set evaluated by the auth/authorization capability (RBAC, ABAC,
or custom), producing an allow/deny decision that gates the wrapped callable (RFC-021).

## 8. Failure Concepts

### 8.1 CapabilityException

The root of the unified exception hierarchy (RFC-025). All errors raised by the runtime, plugins,
capabilities, or backends in their Capio-specific capacity derive from it, enabling catch-all
handling without swallowing user exceptions.

### 8.2 Fail-Safe Degradation

**Fail-Safe Degradation** is the runtime's defined behavior when a capability or backend fails:
the failure is captured, observed, and, unless strict mode is enabled, converted into a no-op that
preserves the wrapped callable's contract (RFC-005, RFC-025).

### 8.3 Strict Mode

**Strict Mode** is a configuration profile option under which capability/backend failures
propagate as exceptions instead of degrading, for environments where silent behavior changes are
worse than failure (RFC-009, RFC-025).

## 9. Ecosystem Concepts

### 9.1 Plugin SDK

The **Plugin SDK** is the library surface (`capio.sdk`) used to author capabilities, backends,
hooks, and manifests: base classes, decorator helpers, contract-test utilities, and the `capio
create-plugin` generator (RFC-012, RFC-028).

### 9.2 Contract Tests

**Contract Tests** are the canonical, backend-independent test suites that a plugin MUST pass to
claim compatibility with a capability or backend interface (RFC-029). They are the enforcement
mechanism behind "backend agnostic."

### 9.3 Capability Marketplace

The **Capability Marketplace** is the (future) discoverable, versioned catalog of published
capability packages with metadata for security, compatibility, and maintenance status
(RFC-013, RFC-032).

### 9.4 Ecosystem Package

An **Ecosystem Package** is a distribution in the convention `<capio>-<name>` — `capio-redis`,
`capio-openai`, `capio-fastapi` — implementing a backend, capability, or integration for the
platform (RFC-013, RFC-033).

## 9.5 AI, Agent & Model Concepts

Terms specific to the AI capability suite (RFC-030). They follow the same platform rules: an AI
concern is a capability, a model provider is a backend, and model input/output is untrusted data.

### 9.5.1 Model / Model Backend

A **Model** is a provider-backed inference service; a **Model Backend** is the `model` backend
kind (`model.openai`, `model.anthropic`, `model.ollama`, ...) exposing a normalized
`complete(...)` interface (RFC-030 §2). Providers are untrusted network peers; completions are
validated and masked.

### 9.5.2 Prompt

A **Prompt** is the rendered input to a model call: a template (docstring or `template=`) with
`{arg}`/`{ctx.field}` placeholders bound at invocation time. Prompts carry `prompt.id` and
`prompt.version` and are masked before logging/tracing/audit by default (RFC-030 §2.3,
RFC-022 §5.3).

### 9.5.3 Tool / Tool Registry

A **Tool** is a Capio-decorated callable registered in the **Tool Registry** with a name,
description, JSON-schema parameters, permission gates, and its own pipeline. Tools are the only
way a model can cause side effects; every tool call inherits the caller's principal and policy
checks (RFC-030 §5.1).

### 9.5.4 Agent

An **Agent** is a bounded, observable, durable loop of model calls and tool calls that achieves a
task, orchestrated by the agent capability (`@use.agent`, RFC-030 §5.3). Agents are workflows
(RFC-023 §5) with step budgets, token budgets, checkpoints, and human-in-the-loop gates.

### 9.5.5 LLM Cache / Semantic Cache

**LLM Cache** is exact-match caching of model calls keyed by prompt+model+params (RFC-030 §3.1).
**Semantic Cache** is similarity-based caching that returns a stored response when an embedding
search exceeds a similarity threshold (RFC-030 §3.2).

### 9.5.6 Memory / RAG / Embedding / Vector Store

**Memory** stores conversation/agent history for context injection. **RAG** (retrieval-
augmented generation) retrieves relevant chunks and grounds model answers with citations.
**Embedding** is the `embedding` backend kind converting text to vectors. A **Vector Store** is
the `vector` backend kind storing/querying vectors with tenant isolation (RFC-030 §4).

### 9.5.7 Guardrail

A **Guardrail** is an input/output scanner that blocks, flags, transforms, or routes a model call
to review (prompt injection, PII, toxicity, hallucination, format, cost, exfiltration). Guardrail
verdicts are audited (RFC-030 §6, RFC-026 §8).

### 9.5.8 Model Router / Token Budget

A **Model Router** deterministically selects a model/provider per call via rules with provider
fallback. A **Token Budget** bounds tokens per call, per agent run, and per rolling period,
deriving cost metrics from a pricing table (RFC-030 §8).

### 9.5.9 MCP

The **Model Context Protocol** (MCP) is a transport for exposing tools, resources, and prompts
to models. Capio's MCP integration treats MCP servers as tool sources (client) and exposes
Capio-decorated callables as MCP tools (server); inbound MCP tool calls run the full pipeline,
and context propagates over the MCP carrier (RFC-030 §7, RFC-006 §5.1, RFC-026 §9).

## 10. Glossary Index (A–Z Quick Reference)

| Term | Defined in |
| ---- | ---------- |
| Backend | §4.3 |
| Cache Entry / Key | §7.2 |
| Capability | §2.1 |
| Capability Configuration | §2.2 |
| Capability Graph | §2.3 |
| Capability Instance | §2.2 |
| Capability Lifecycle | §3.6 |
| Capability Manifest | §2.4 |
| Capability Runtime | §3.1 |
| CapabilityException | §8.1 |
| Cancellation Token | §6.5 |
| Configuration Source | §5.1 |
| Context | §3.3 |
| Context Propagation | §3.4 |
| Contract Tests | §9.2 |
| Decorated Callable | §6.1 |
| Decorator | §6.2 |
| Dependency Resolution | §5.4 |
| Ecosystem Package | §9.4 |
| Event | §4.6 |
| Event Bus | §4.7 |
| Execution Lifecycle | §3.5 |
| Execution Pipeline | §3.2 |
| Fail-Safe Degradation | §8.2 |
| Hook | §4.4 |
| Internal Message Bus | §4.7 |
| Invocation | §6.4 |
| Message Bus | §4.7 |
| Metric | §7.4 |
| Middleware | §4.5 |
| Pipeline Build | §6.3 |
| Plugin | §4.1 |
| Plugin SDK | §9.1 |
| Policy | §7.5 |
| Profile | §5.2 |
| Registry | §4.2 |
| Serializer | §7.1 |
| Service Container | §5.3 |
| Span | §7.3 |
| Strict Mode | §8.3 |
| Trace | §7.3 |
| Wrapped Callable | §6.1 |
| Agent | §9.5.4 |
| Embedding / Vector Store | §9.5.6 |
| Guardrail | §9.5.7 |
| LLM Cache / Semantic Cache | §9.5.5 |
| MCP | §9.5.9 |
| Memory / RAG | §9.5.6 |
| Model / Model Backend | §9.5.1 |
| Model Router / Token Budget | §9.5.8 |
| Prompt | §9.5.2 |
| Tool / Tool Registry | §9.5.3 |

## 11. Relationship to Other RFCs

- Context: RFC-006.
- Lifecycles: RFC-005 (execution), RFC-011 (capability/plugin).
- Hooks/events: RFC-007, RFC-008.
- Config & DI: RFC-009, RFC-010.
- Plugins/backends: RFC-011…RFC-015.
- Failures: RFC-025.
- AI/agents/MCP: RFC-030; security of AI: RFC-026 §8–9.
- SDK/ecosystem: RFC-012, RFC-013, RFC-029, RFC-032.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 1.0     | 2026-08-01 | Initial publication. |
