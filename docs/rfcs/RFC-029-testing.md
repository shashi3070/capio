# RFC-029: Testing Framework & Contract Tests

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies the **testing framework** for Capio itself and for the ecosystem: the testing
SDK, **contract tests** (the enforcement of "backend agnostic" and "sync/async equal", RFC-001
§3.3–3.4), mock plugins, fake backends, golden tests, property tests, AI evaluation hooks, and
their integration with the benchmark suite (RFC-027) and CI (RFC-031).

## 2. Testing layers

| Layer | What it tests | Tooling |
| ----- | ------------- | ------- |
| Unit | individual capabilities, modules | pytest (workspace standard) |
| Contract | interface conformance (RFC-015 backends, RFC-012 capabilities) | SDK `contract` module |
| Golden | deterministic expected outputs (trees, snapshots) | `capio.testing` snapshots |
| Property | invariants (reentrancy, idempotency, redaction) | hypothesis |
| Integration | framework adapters (FastAPI, MCP, Celery) | pytest + subprocess |
| Performance | budgets (RFC-027) | `capio benchmark` |
| AI eval | model behavior, guardrails, evals | `capio.testing.eval` |

## 3. Testing SDK (`capio.testing`)

| Member | Purpose |
| ------ | ------- |
| `FakeBackend` / `MemoryBackend` | deterministic in-memory backends for every kind (RFC-015) |
| `MockPlugin` | register a fake plugin/capability in a test runtime |
| `isolate_runtime()` | fixture: fresh `CapioRuntime` per test (RFC-004 §6.3) |
| `fake_backend(name, kind)` | substitute a backend via container override (RFC-010 §8) |
| `capture_events(...)` | collect Event Bus traffic in a test |
| `snapshot(ctx)` | golden snapshot compare for contexts/spans |
| `eval_run(...)` | run an AI eval case against a decorated LLM call (RFC-030 §10) |

Test doubles are used via the container `override` mechanism (RFC-010 §8) — production code paths
stay identical.

## 4. Capability contract tests

A published capability MUST pass `capio test <plugin>` (RFC-028 §3.6), which runs:

1. **Kind matrix** — the capability on `def`, `async def`, generator, async generator; correct
   behavior and `UnsupportedExecutionKindError` (RFC-003 §3.3) for disallowed kinds.
2. **Reentrancy** — concurrent + nested invocations produce isolated state (RFC-004 §3.5).
3. **Lifecycle** — configure/initialize/start/stop/destroy in order; idempotent stop.
4. **Config** — schema validation, precedence (RFC-009 §6), overrides, fail-fast errors.
5. **Ordering** — pipeline order matches declared priority / explicit chaining (RFC-005 §4).
6. **Degradation** — fail-safe behavior when a dependency/backend fails (RFC-005 §7).
7. **Errors** — every error path raises the correct typed exception (RFC-025).
8. **Zero-cost hygiene** — no I/O at decoration/import (RFC-027 §2).

## 5. Backend contract tests

For every backend kind (RFC-015 §3), the canonical suite runs the SAME tests against EVERY
registered backend:

- CRUD semantics (cache), metric emission (metrics), span lifecycle (trace), publish/subscribe
  (event), enqueue/ack (queue), transaction commit/rollback (db), auth verify/authorize,
  lock acquire/release.
- TTL/expiry correctness, bulk ops, concurrency safety, `health()` reporting.
- Error typing (RFC-025 backend group) and fail-safe degradation.
- Distributed correctness where applicable (locks under contention, rate limiting across
  processes).

A backend is compatible ONLY when it passes the suite against the real service (in CI with the
service via docker-compose, matching workspace conventions, RFC-031).

## 6. Golden & property tests

- **Golden**: rendered graphs (`capio graph`), context snapshots (RFC-006 §9), audit records
  (RFC-020 §3.3), span trees (RFC-019) — deterministic byte-for-byte (profile `test` seeds all
  RNGs, RFC-009 §7).
- **Property** (hypothesis): reentrancy invariants under interleaving; key-builder determinism
  and sensitivity (RFC-016 §3); redaction completeness (RFC-022 §5); pipeline-order
  determinism across random capability sets; serializer round-trips; retry termination
  (`max_attempts` always terminates).

## 7. AI evaluation

The testing framework provides an **eval harness** for LLM/agent capabilities (RFC-030 §10):

- `capio.testing.eval.case(prompt, expected=..., checks=[...])` runs a prompt against the
  decorated LLM call, capturing full trace/audit context.
- Eval suites (accuracy, latency, cost, safety) run as CI checks with golden thresholds;
  regressions fail CI.
- Production invocations can carry `gen_ai.eval.id` so production traces link to offline evals
  (RFC-019 §4).
- Guardrail evals verify prompts/responses against adversarial inputs (RFC-030 §6, RFC-026 §8).

## 8. CI integration

- Unit + contract + golden + property tests run per PR (RFC-031 CI).
- Benchmark budgets (RFC-027 §3.3) run on the reference runner; regressions fail.
- Coverage targets: core ≥ 90%, plugin SDK ≥ 90%, integrations ≥ 70% (workspace `pytest-cov`
  convention, RFC-031).
- Lint (ruff, workspace standard) enforces the exception-hierarchy rule (RFC-025 §4) and no-core-
  imports-plugin rule (RFC-027 §2).

## 9. Document Dependencies

- Principles: RFC-001; SDK: RFC-012; manifests: RFC-013; backends: RFC-015; observability:
  RFC-019; errors: RFC-025; benchmarks: RFC-027; CLI: RFC-028; AI: RFC-030; CI/repo: RFC-031.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
