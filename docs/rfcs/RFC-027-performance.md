# RFC-027: Performance & Benchmark Framework

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies Capio's **performance contract** ("zero-cost if unused", RFC-001 §3.2; cheap
when used) and the **benchmark framework** that verifies it continuously. It defines budgets for
import, decoration, invocation overhead, plugin loading, memory, and startup — and the official
benchmark suite (RFC-029 integrates it with CI).

## 2. Performance contract

### 2.1 Zero-cost if unused

| Measure | Budget | Verification |
| ------- | ------ | ------------ |
| Import `capio` | < 25ms cold (no plugins, no config read) | benchmark: import |
| Import footprint | no plugin/backend module imported by `capio/__init__` | lint rule (RFC-004 §9) |
| Process memory after import | < 8MB RSS delta | benchmark: memory |
| No side effects on import | no sockets/files/locks | contract test |

### 2.2 Cheap when used

| Measure | Budget (p95, warm) | Verification |
| ------- | ------------------ | ------------ |
| Pipeline build (first call) | < 5ms for ≤5 capabilities | benchmark: build |
| Decorator application | < 100µs per decorator | benchmark: decorate |
| Overhead of empty pipeline (`@use()` no capabilities... not valid) → 1 capability no-op step | < 2µs/invocation | benchmark: invocation |
| 5-capability pipeline (cache miss, no I/O backends) | < 20µs/invocation | benchmark: invocation |
| Registry lookup | < 1µs | benchmark: registry |
| Context create + scope enter/exit | < 2µs | benchmark: context |
| Event bus emit with no subscribers | < 1µs (skip compiled match) | benchmark: bus |

Budgets are asserted in CI on reference hardware (RFC-031 CI matrix) with a documented ± tolerance;
regressions fail CI (RFC-029).

### 2.3 Techniques the runtime MUST use

1. **Lazy everything** (RFC-004 §3.3): capability implementations, backends, config, plugins are
   constructed on demand.
2. **Memoized pipeline build** keyed by config fingerprint (RFC-004 §4.2, RFC-009 §6.1).
3. **Compiled step lists**: pipeline steps pre-bound; per-invocation dispatch is a flat loop,
   no reflection.
4. **Copy-on-write registries**: lock-free reads (RFC-014 §6).
5. **Event skip-index**: zero overhead when no subscriber matches a type (RFC-008 §2.4).
6. **Fast Context allocation**: slot dataclass + `__slots__`; lazily-bound handles
   (RFC-006 §2.3).
7. **Avoid allocation in hot path**: reuse key builders, span buffers, metric buckets where safe;
   measured by the benchmark suite (allocations per invocation).
8. **Async path parity**: async overhead target ≤ 2× sync overhead for the same pipeline
   (RFC-024).

## 3. Benchmark framework

### 3.1 Harness

- `capio benchmark` (RFC-028) runs the official suite; `capio.benchmark` is also importable for
  programmatic runs (RFC-029 golden tests).
- Scenarios run warm (after a calibration loop) and cold, in the `benchmark` profile
  (RFC-009 §7: observability off, deterministic seed, null backends).
- Each scenario reports: p50/p95/p99 latency, throughput (ops/s), allocations/op, RSS delta,
  and GC pressure.

### 3.2 Scenario catalogue

| Scenario | Measures |
| -------- | -------- |
| `import` | cold import time/memory |
| `decorate` | application cost for 1/3/5 capabilities |
| `build` | pipeline build 1/5/10 capabilities |
| `invoke.empty` | no-capability overhead |
| `invoke.pipeline5` | 5-capability pipeline, all pass-through |
| `invoke.retry.noop` | retry success path |
| `invoke.cache.hit` / `.miss` | cache fast path |
| `invoke.async.pipeline5` | async parity vs sync |
| `invoke.stream.sync` / `.async` | generator overhead |
| `registry.lookup` | registry hit |
| `bus.emit` | zero-subscriber emit |
| `context.enter_exit` | scope cost |
| `startup.cold` / `.warm` | runtime start with N plugins |
| `plugin.load` | load+validate cost per plugin |

### 3.3 Regression policy

- Benchmarks run in CI on every PR (reference runner, RFC-031).
- A change that regresses a budget by more than the documented tolerance (default +10% on p95) OR
  adds measurable allocation in the hot path requires review; a deliberate trade-off must be
  documented in the PR (performance budget table updated).

## 4. Optimization strategy

1. **Profile first**: the framework and `capio profile` (RFC-028) drive optimizations; no
   micro-optimization without a benchmark scenario proving it.
2. **Pay only for what's used**: observability hooks (spans, metrics) add cost only when a
   backend is configured (RFC-019 §3.2).
3. **Async optimization**: hot async paths avoid task creation per capability where possible
   (direct await chains); measured by the async parity scenario.
4. **Memory**: `__slots__` data classes, frozen config, zero-copy carriers (RFC-006 §5.2), reuse
   buffers.

## 5. Document Dependencies

- Zero-cost principle: RFC-001; lazy architecture: RFC-004; pipeline memoization: RFC-005;
  registries: RFC-014; observability: RFC-019; async: RFC-024; CLI: RFC-028; tests/golden:
  RFC-029; CI: RFC-031.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
