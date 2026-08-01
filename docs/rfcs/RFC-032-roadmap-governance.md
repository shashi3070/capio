# RFC-032: Roadmap, Governance, Versioning, Ecosystem Strategy

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies how Capio is **governed** and **versioned**, what the **roadmap** is, and the
**ecosystem strategy** that turns a library into a platform (RFC-001 §7, §9). It also defines the
RFC process itself (the meta-governance of this specification).

## 2. RFC governance

- Every RFC is a living document with a status (RFC-000 §Status Legend): Draft → Accepted →
  Implemented; Deprecated only via supersession.
- **Change policy**: an Accepted RFC changes only through an amendment recorded in its Change
  Record, or a new RFC that supersedes it (explicit `Supersedes` header). Behavior-affecting
  changes to public contract require a deprecation RFC (RFC-001 §4.9, RFC-003 §10).
- RFC numbering is append-only; 034+ reserved for future RFCs (agent marketplace, distributed
  runtime, etc.).
- Decision authority: the maintainer (sole owner today) with a public RFC review window;
  the RFC process is designed so contributors can drive RFCs without committing code first
  (RFC-001 §5.5).

## 3. Semantic versioning

### 3.1 Public contract

The following are public and semver-governed:

- The `use` API surface and typing contract (RFC-003 §10).
- Default capability ordering (RFC-005 §4.2).
- `fn.__capio__` shape (RFC-003 §5.3).
- Capability/Backend interfaces in `capio.sdk` (RFC-012/015).
- The plugin manifest schema and entry-point names (RFC-013).
- The command surface of the Internal Message Bus (RFC-008 §3.5).
- `api_version` (RFC-011 §8) — bumps on breaking interface changes.

### 3.2 Version rules

| Bump | Trigger |
| ---- | ------- |
| **MAJOR** | breaking change to public contract; reordering defaults; `api_version` bump; Python floor bump. |
| **MINOR** | new capability, backend, hook, event, option (backward compatible); new `use.<name>`. |
| **PATCH** | bug fixes, performance, docs, non-contract changes. |

- 0.x: minor may contain breaking changes but MUST be documented; 1.0 is the first stable
  release (roadmap §6).
- Deprecation: a feature is marked deprecated (warning + docs) in one MINOR, removed in the next
  MAJOR (RFC-003 §10 style).
- Plugins follow the same rules with their own semver; plugin↔runtime compatibility via
  `api_version` and the compatibility matrix (RFC-011 §8).

## 4. Quality gates (definition of done)

A release is shippable when RFC-031 CI passes entirely: lint, types, tests, contract tests,
benchmarks, coverage, docs, signed artifacts, and `capio doctor` clean.

## 5. Ecosystem strategy

### 5.1 The plugin flywheel

1. **SDK + manifest + contract tests** (RFC-012/013/029) make publishing a capability a
   packaging step.
2. **Backend contract tests** (RFC-029 §5) make "switch backends without code changes" a
   verifiable claim (RFC-015 §4).
3. **CLI scaffolding** (`capio create-plugin`) lowers the first-plugin barrier (RFC-028 §3.5).
4. **First-party reference plugins** (`capio-redis`, `capio-openai`, `capio-mcp`,
   `capio-fastapi`, `capio-celery`, `capio-otel`) demonstrate the pattern and seed the
   ecosystem (RFC-031 §2).
5. **Ecosystem package convention** `<capio>-<name>` and reserved names (RFC-013 §3.1) keep the
   namespace clean.

### 5.2 Marketplace (roadmap)

The **Capability Marketplace** (RFC-002 §9.3, RFC-013 §4) is a future discoverable catalog with
security/compatibility/maintenance metadata. It is NOT required for the core value; the
packaging + contract infrastructure precedes it.

### 5.3 Governance of plugins

- Core repository hosts only first-party plugins; third-party plugins live in their own repos
  (RFC-013) and register via entry points.
- A curated "verified" badge is awarded after signature + contract-test verification (RFC-026
  §2/§5); no code is vendored.

## 6. Roadmap

| Phase | Goal | Key deliverables |
| ----- | ---- | ---------------- |
| **0.x (current)** | Core runtime + foundation capabilities | RFC-003–025 implemented; retry/cache/timeout/trace/metrics/log/audit/auth/validate; CLI subset; contract-test harness; `capio-redis`, `capio-otel`. |
| **0.6** | Async/streaming parity + workflows | RFC-024 fully; publish/queue/transaction/workflow/cron/compensate; `capio-celery`. |
| **0.8** | AI suite | RFC-030: `capio-openai`, model/embedding/vector backends, llm_cache, semantic_cache, memory/rag, tool registry, guardrails, token budget, model router. |
| **0.9** | MCP + agents | `capio-mcp` client+server; agent orchestration; human-in-the-loop; AI eval harness. |
| **1.0** | Stable platform | API freeze (RFC-003 §10); signed releases; compatibility matrix; benchmark SLAs; `capio doctor` hardened; docs complete. |
| **1.x** | Ecosystem growth | Verified badge; marketplace beta; more integrations (Kafka, FastAPI, Django, LLM observability). |
| **2.0** | Distributed runtime (future RFC) | Cross-process context, distributed state (breakers/rate limits), leader-coordinated scheduling. |
| **3.0** | Marketplace (future RFC) | Plugin distribution, discovery, trust signals. |
| **4.0** | Cloud runtime (future RFC) | Managed execution surfaces. |
| **5.0** | AI-powered optimization (future RFC) | Model-routed config, learned cache/stampede policies, auto-tuning backoff. |

### 6.1 Principles governing the roadmap

- Foundation first: RFC-001–010 stability gates everything else.
- Every phase ships working, tested code (no vaporware RFCs at Implemented status).
- AI features are first-class from 0.8 but never destabilize the core (they are plugins).
- Breaking changes only via deprecation RFCs.

## 7. Success metrics (ecosystem)

- ≥ N third-party plugin packages passing contract tests (RFC-029).
- Backend-parity suite green for ≥ 4 cache backends, ≥ 3 model providers.
- Zero-cost and invocation budgets maintained across releases (RFC-027).
- Adoption: measurable installs; framework integrations used in production.

## 8. Document Dependencies

- Principles: RFC-001; index/process: RFC-000; versioned contract: RFC-003; api_version:
  RFC-011; packaging: RFC-013; trust: RFC-026; tests: RFC-029; repo/CI: RFC-031; AI: RFC-030.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
