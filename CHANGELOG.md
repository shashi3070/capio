# Changelog

All notable changes to this project are documented in this file.

## v1.0.0 (2026-08-01)

- Full reference implementation per RFC-031: **37 capabilities** in five
  families.
- Resilience: added `throttle` (bounded concurrency) and `debounce`
  (coalescing) per RFC-018 §5-6.
- Data/auth (RFC-020 §3-4, RFC-022): `audit` (append-only hash-chained trail),
  `auth` (identity + scopes + policy), `validate` (schema checks), `serialize`
  (RFC-022 §3 codec boundary + registry, safe `json` default, `pickle` opt-in),
  `encrypt` (dependency-free field cipher), `mask`, `dedup`.
- Messaging/orchestration (RFC-023): `publish`, `consume`, `queue`,
  `transaction`, `workflow`, `cron`, `compensate`, `idempotent`.
- AI (RFC-030): `llm`, `llm_cache`, `semantic_cache`, `prompt_cache`, `memory`,
  `rag`, `ingest`, `tool`, `agent`, `guardrails`, `token_budget`,
  `model_router`.
- New backends: `audit.memory` (`InMemoryAuditBackend`), `store.memory`
  (`InMemoryStore`), `broker.memory` (`InMemoryBroker`), `queue.memory`
  (`InMemoryTaskQueue`); all bound by default in `CapioRuntime`.
- New exceptions: `ConcurrencyLimitError`, `AuthenticationError`,
  `AuthorizationError`, `PolicyEvaluationError`, `ValidationError`,
  `SerializationError`, `EncryptionKeyError`, `TransactionError`, `WorkflowError`,
  `IdempotencyConflictError`, `ProviderError`, `GuardrailError`,
  `TokenBudgetExceededError`; added non-retryable guards.
- Tests grown to 126 (execution guards, data/auth, messaging, AI, serialize);
  ruff clean.
- Docs rewritten for all 37 capabilities (usage reference, architecture map,
  README table); status changed to Production/Stable.
- New `docs/cookbook.md`: a runnable example for each of the 37 capabilities.
- Fixed chained decorator stacking: `__capio_leaf__` no longer leaks up through
  `functools.wraps`, so `@use.memory` + `@use.rag` + `@use.llm` +
  `@use.context()` executes each capability exactly once (previously nested
  pipelines re-ran each step).
- Fixed `llm` async provider path: `call_next` is now awaited before the request
  reaches `provider` (previously the provider received an unawaited coroutine
  and the inner function never ran).

## v0.1.1 (2026-08-01)

- Documentation: rewritten README with feature overview, how-it-works section,
  error-handling guide, and architecture/pipeline diagrams
  (`docs/images/architecture.png`, `docs/images/pipeline.png`).
- Fix README links to point at absolute GitHub URLs so they resolve on PyPI.

## v0.1.0 (2026-08-01)

- Initial MVP reference implementation per RFC-031.
- Core runtime: `CapioRuntime`, `ExecutionPipeline`, sync + async engines, Context +
  ContextScope propagation (ContextVars), registry, service container, event bus.
- `use` facade: chained decorators and composite `@use(...)` form, typed,
  no I/O at decoration time, `fn.__capio__` introspection, `capio.unwrap`/`capio.pipeline`.
- Capabilities: `retry` (backoff + jitter), `cache` (memory backend, TTL),
  `timeout` (async `wait_for`, sync worker-thread fallback), `circuit_breaker`,
  `rate_limit`, `trace`, `metrics`, `log`.
- Exception hierarchy per RFC-025 (`CapabilityException` root, `CapioCancelledBase`,
  typed groups for config/execution/backend/auth/data/hooks/registry/bus/integration).
- CLI (Typer): `doctor`, `inspect`, `graph`, `benchmark`, `version`.
- RFC spec (docs/rfcs/RFC-000…033) authored alongside the implementation.
