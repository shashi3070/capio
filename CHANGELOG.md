# Changelog

All notable changes to this project are documented in this file.

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
