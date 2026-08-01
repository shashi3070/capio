# RFC-033: Migration Guide, Comparison Matrix, FAQ

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC is the **practical companion** to the specification: how to move existing code onto
Capio, how Capio maps onto the libraries people already use, and the frequently-asked questions
that surface during evaluation. It is written to be read by engineers, not architects.

## 2. Migration guide

### 2.1 Incremental adoption

Capio is designed for **gradual migration** — decorate one function at a time; nothing global is
required.

1. **Install** `capio` and the backends you need (`capio-redis`, `capio-otel`, ...).
2. **Profile** `dev` first (`CAPIO_PROFILE=dev`) to see defaults without prod behavior.
3. **Replace one wrapper at a time** using the mapping tables below.
4. **Verify** with `capio inspect <fn>` and `capio graph <fn>` that the pipeline is what you
   intend (RFC-028).
5. **Ship** telemetry first (trace/metrics/log/audit), then behavior (retry/cache), then
   admission (auth/rate limit/circuit breaker).

### 2.2 Mapping from common libraries

| You use today | Capio equivalent | Notes |
| ------------- | ---------------- | ----- |
| `@retry` (Tenacity) | `@use.retry(max_attempts=3, backoff="exponential")` | Same semantics: retry_on, jitter, async. Composition is the win. |
| `@lru_cache` / cachetools | `@use.cache(ttl=..., backend=...)` | `functools.lru_cache` is a valid key/backend adapter; cachetools can be a `cache` backend via SDK. |
| `func_timeout` / manual `asyncio.wait_for` | `@use.timeout(seconds=...)` | Async native; sync hard mode POSIX-only (RFC-018 §3.3). |
| manual rate-limit decorators | `@use.rate_limit(limit=..., window=...)` | Per-key via `ctx.auth` (RFC-021). |
| OpenTelemetry manual spans | `@use.trace()` | Same span semantics; OTel as backend (RFC-019). |
| Prometheus client | `@use.metrics()` | Standard counters/duration emitted (RFC-019 §3.2). |
| structlog / logging decorators | `@use.log(backend=structlog)` | Structured records, context-correlated (RFC-020). |
| manual JWT verification | `@use.auth(provider="oidc", scopes=...)` | Same verification; adds RBAC/ABAC/policy (RFC-021). |
| pydantic `validate_call` | `@use.validate(schema=Model)` | Same validation; adds output validation + validator registry. |
| celery task decorators | `@use.queue(backend="celery")` via `capio-celery` | Context propagates in task headers (RFC-023 §3). |
| kafka producer boilerplate | `@use.publish(topic=...)` | Outbox support (RFC-023 §2). |
| manual prompt caching | `@use.llm_cache()` / `@use.semantic_cache()` | RFC-030 §3. |
| manual agent loops | `@use.agent(tools=[...])` | Durable, observable agent loop (RFC-030 §5). |
| raw MCP client code | `capio-mcp` `mcp.connect(...)` + `@use.llm(tools=mcp.client_tools(...))` | Tools become Capio tools with full pipelines (RFC-030 §7). |

### 2.3 Keeping both

Capio composes WITH the libraries, so you can keep an existing library as a **backend** or
**capability** rather than deleting it: Tenacity's retry logic, cachetools' stores, structlog's
handlers, OTel's SDK — all valid backends (RFC-015). Migration is not all-or-nothing.

## 3. Comparison matrix

| Criterion | Tenacity | cachetools | dependency-injector | OpenTelemetry | FastAPI middleware | **Capio** |
| --------- | -------- | ---------- | ------------------- | ------------- | ------------------ | --------- |
| Retry | ✅ core | ❌ | ❌ | ❌ | ❌ | ✅ capability |
| Cache | ❌ | ✅ core | ❌ | ❌ | ❌ | ✅ capability |
| Timeout | partial | ❌ | ❌ | ❌ | ❌ | ✅ capability |
| Rate limit | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ capability |
| Circuit breaker | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ capability |
| Auth/authorization | ❌ | ❌ | ❌ | ❌ | framework-bound | ✅ capability |
| Validation | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ capability |
| Audit | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ capability |
| Tracing/metrics | ❌ | ❌ | ❌ | ✅ standard | ❌ | ✅ emits to OTel/etc. |
| DI | ❌ | ❌ | ✅ | ❌ | ❌ | ✅ internal container |
| Composes w/ other tools | ❌ (single purpose) | ❌ | — | ✅ (ecosystem) | ❌ (framework-bound) | ✅ (backs w/ them) |
| Sync+async one model | ✅ | ❌ | — | ✅ | ❌ | ✅ |
| Framework agnostic | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ |
| Plugin ecosystem | ❌ | ❌ | ❌ | ✅ | ❌ | ✅ (SDK/manifest/tests) |
| Config model | per-call | per-call | per-call | env-ish | framework | ✅ unified, layered |
| Backend switchable | n/a | partial | n/a | n/a | n/a | ✅ config-only |

**Position**: Capio is not "a better retry" — it is the composition + lifecycle + plugin
platform that the others each do one slice of. OTel remains the observability standard Capio
emits to; dependency-injector remains a valid app-DI if you already use it.

## 4. FAQ

**Q: Is Capio another decorator library?**
A: It ships decorators as its API (RFC-003), but it is a runtime: one lifecycle, one config, one
hook system, one backend contract, and a plugin SDK (RFC-001 §1, §5.5).

**Q: Does it work with FastAPI/Django/Celery?**
A: Yes — via integrations (`capio-fastapi`, etc.) that adapt framework lifecycles into Capio
contexts (RFC-013 §3.2, RFC-033 §2.2). Capio is framework agnostic at its core (RFC-001 §7.1).

**Q: Sync and async — do I write capabilities twice?**
A: No. One `run(ctx, call_next)` drives both engines (RFC-012 §3.2, RFC-024 §2). Only
genuinely path-specific backends split.

**Q: What happens if Redis/cache/metrics is down?**
A: Fail-safe degradation (RFC-005 §7): the capability bypasses itself, records the failure, and
the function still works — unless you opt into strict mode. Audit is the one exception
(propagates by default, RFC-020 §3.4).

**Q: Is it slow?**
A: Zero-cost if unused (RFC-027 §2.1: <25ms import, no plugin imports); ~µs per capability
overhead when used; budgets enforced in CI.

**Q: Can I keep my Tenacity/cachetools/structlog code?**
A: Yes — they become backends/capabilities; migrate incrementally (RFC-033 §2.3).

**Q: How do I add a new behavior?**
A: Subclass `Capability`, declare a schema, run `capio test` (RFC-012, RFC-029). Publishing is
`capio create-plugin` + contract tests + manifest (RFC-013).

**Q: How do AI/LLM/agents fit?**
A: As capabilities (RFC-030): `@use.llm()`, `@use.llm_cache()`, `@use.semantic_cache()`,
`@use.memory()`, `@use.rag()`, `@use.agent()`, `@use.guardrails()`, `@use.model_router()`,
and MCP via `capio-mcp`. They compose with retry/cache/auth/audit like anything else.

**Q: Is Capio a replacement for LangChain/LlamaIndex?**
A: No — they are orchestrators; Capio provides the production *behavior layer* (resilience,
caching, cost, observability, safety, audit) around model calls and agent loops, and composes
with them as backends (RFC-030 §11).

**Q: Security — can a plugin do anything?**
A: In-process containment + permissions + signatures (RFC-026): plugins declare permissions,
unsigned plugins run default-deny, secrets are ref-only, pickle is opt-in, AI tool calls are
permissioned. OS-level sandboxing is future work (RFC-032).

**Q: What is `api_version`?**
A: The runtime contract version plugins declare compatibility with (RFC-011 §8); `capio doctor`
detects mismatches.

**Q: When is 1.0?**
A: Per the roadmap (RFC-032 §6) — when the foundation is frozen and CI gates are green.

## 5. Document Dependencies

- All capability RFCs (RFC-016–023); security: RFC-026; performance: RFC-027; tests: RFC-029;
  AI: RFC-030; governance: RFC-032.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
