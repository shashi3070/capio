# RFC-031: Reference Implementation & Repository Structure

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies the **reference implementation** of Capio: the repository layout, module
structure, packaging, CI, lint/format, documentation standard, and release flow. It turns the
architecture RFCs into an engineerable tree. Repository conventions follow the workspace standard
(`src/` layout, setuptools, ruff, pytest, coverage — matching `json-fix`/`ai-accel`).

## 2. Monorepo layout

```
capio/                                  # the Capio project root (this workspace)
├── pyproject.toml                      # core package: name = "capio"
├── LICENSE                             # MIT
├── README.md
├── CHANGELOG.md
├── docs/
│   ├── rfcs/                           # THIS SPEC (RFC-000…033)
│   ├── tutorials/                      # quick start, compose, agents, mcp
│   ├── guides/                         # plugin author, backend author, migration
│   └── api/                            # generated API reference
├── src/
│   └── capio/
│       ├── __init__.py                 # exports use, Capability, with_capabilities
│       ├── runtime.py                  # CapioRuntime (RFC-004 §5.1)
│       ├── use.py                      # Use facade (RFC-003, RFC-004 §4.1)
│       ├── pipeline.py                 # ExecutionPipeline + builder (RFC-004/005)
│       ├── engine/
│       │   ├── sync.py                 # sync engine
│       │   └── async_engine.py         # async engine (RFC-024)
│       ├── context.py                  # Context, CancellationToken (RFC-006)
│       ├── propagation.py              # carriers, scopes, codecs (RFC-006)
│       ├── config/                     # Config Store, schemas, profiles (RFC-009)
│       ├── di.py                       # Service Container (RFC-010)
│       ├── hooks.py                    # Hook Dispatcher (RFC-007)
│       ├── events.py                   # Event Bus, Message Bus (RFC-008)
│       ├── registry.py                 # Registries (RFC-014)
│       ├── plugin/
│       │   ├── loader.py               # discovery/load (RFC-011)
│       │   └── lifecycle.py            # state machine (RFC-011)
│       ├── sdk/                        # Capability, Backend, contract helpers (RFC-012/015)
│       ├── backends/
│       │   ├── memory_cache.py         # cache.memory (RFC-016)
│       │   ├── console_trace.py        # trace.console (RFC-019)
│       │   ├── null_metrics.py         # metrics.null/test (RFC-019)
│       │   └── stdio_log.py            # log.stdio (RFC-020)
│       ├── capabilities/
│       │   ├── retry.py                # RFC-017
│       │   ├── cache.py                # RFC-016
│       │   ├── timeout.py              # RFC-018
│       │   ├── circuit_breaker.py      # RFC-018
│       │   ├── rate_limit.py           # RFC-018
│       │   ├── throttle.py             # RFC-018
│       │   ├── debounce.py             # RFC-018
│       │   ├── trace.py                # RFC-019
│       │   ├── metrics.py              # RFC-019
│       │   ├── log.py                  # RFC-020
│       │   ├── audit.py                # RFC-020
│       │   ├── auth.py                 # RFC-021
│       │   ├── validate.py             # RFC-022
│       │   ├── serialize.py            # RFC-022
│       │   ├── encrypt.py              # RFC-022
│       │   ├── mask.py                 # RFC-022
│       │   ├── dedup.py                # RFC-022
│       │   ├── publish.py              # RFC-023
│       │   ├── queue.py                # RFC-023
│       │   ├── transaction.py          # RFC-023
│       │   ├── workflow.py             # RFC-023
│       │   ├── cron.py                 # RFC-023
│       │   └── compensate.py           # RFC-023
│       └── cli.py                      # Typer CLI (RFC-028)
├── plugins/                            # first-party ecosystem plugins (separate dists)
│   ├── capio-redis/
│   ├── capio-openai/
│   ├── capio-mcp/
│   ├── capio-fastapi/
│   └── ...
├── tests/                              # core package tests (RFC-029)
├── benchmarks/                         # benchmark suite (RFC-027)
└── scripts/                            # dev tooling
```

## 3. Module layering rule

The acyclic dependency rule (RFC-004 §2.2, rule 2) maps to a strict import direction:

```
api (use) → pipeline → engine → context/propagation → hooks/events → registries → di → plugin → config → sdk
```

Enforced by a lint rule (ruff custom rule or a CI script): `capio/__init__` imports only
`use`/`runtime`/`sdk`; no module imports upward; `sdk` never imports engine internals except via
public interfaces. This keeps the "no plugin import at `capio/__init__`" guarantee (RFC-027 §2).

## 4. Packaging

- Core: `name = "capio"`, `src/` layout, setuptools (RFC-000 conventions match `json-fix`).
- `requires-python = ">=3.9"`; optional extras:
  - `[capio.redis]` → `capio-redis`
  - `[capio.ai]` → `capio-openai`, `capio-mcp`, vector/embedding plugins
  - `[capio.otel]` → OpenTelemetry backends
  - `[capio.dev]` → pytest, ruff, coverage, hypothesis, mypy, pyright
- `py.typed` marker ships with the package (RFC-003 §7.2).
- Plugin distributions live in `plugins/` as separate projects (RFC-013), published to PyPI
  under the `<capio>-<name>` convention.
- The CLI script is `capio` (console entry point, Typer; workspace standard per `json-fix`).

## 5. CI pipeline

GitHub Actions (workspace standard — `ai-accel` uses `.github/workflows`):

1. **lint**: ruff (select `E,F,I` plus custom rules: hierarchy-raise rule RFC-025 §4,
   no-upward-import rule §3).
2. **type**: mypy + pyright on `src/` (RFC-003 §7 type contract).
3. **unit + contract + golden + property**: pytest + pytest-cov (targets RFC-029 §8),
   coverage ≥ 90% core.
4. **backend parity**: run cache/queue/db contract tests against real services via
   docker-compose (Redis, Postgres, Kafka) — the workspace already uses docker-compose
   (`ai-accel`).
5. **benchmark**: `capio benchmark` on the reference runner; budgets enforced (RFC-027 §3.3).
6. **async/Windows matrix**: sync+async on CPython 3.9–3.13; Windows CI for timeout fallback
   behavior (RFC-018 §3.3, no SIGALRM) and path handling.
7. **docs**: RFC links valid; API reference builds.

## 6. Documentation standard

- Every capability ships: RFC link, docstrings, README example, tutorial reference, and a
  contract-test suite (RFC-029).
- API reference generated from docstrings (pydoc-style, workspace convention).
- Changelog follows Keep a Changelog (workspace `json-fix` convention).
- Every public symbol is documented; undocumented public symbols fail CI.

## 7. Release flow

- Trunk-based: `main`; releases from `release/*` or tags `v0.x.y` (semver per RFC-032).
- CI gates all the way to release; `capio doctor` runs as a release smoke test.
- Release artifacts: signed wheels (RFC-026 §5) + source dist; changelog updated; compatibility
  matrix published (RFC-011 §8).
- Plugins release on their own cadence; breaking `api_version` bumps trigger core release and
  matrix update.

## 8. Document Dependencies

- All architecture RFCs (RFC-004…024); errors: RFC-025; security: RFC-026; performance:
  RFC-027; tests: RFC-029; governance/versioning: RFC-032; migration: RFC-033.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
