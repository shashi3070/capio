# RFC-028: CLI & Developer Tooling

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies the **`capio` command-line interface** and developer tooling: introspection,
configuration, plugin management, graph rendering, benchmarking, tracing/profiling, scaffolding,
and diagnostics. The CLI is built with Typer (matching the workspace convention, RFC-031) and is
a thin, lazy front-end over the runtime — it imports `capio` only, never plugins.

## 2. Command map

| Command | Purpose |
| ------- | ------- |
| `capio doctor` | environment/install health check |
| `capio plugins` | list/status/trust of installed plugins |
| `capio plugin install/remove` | manage plugins (dev aid) |
| `capio manifest check` | validate a `capability.yaml` |
| `capio graph <fn>` | render the capability graph/order |
| `capio inspect <fn>` | show a callable's pipeline, order, config, backends |
| `capio config [key]` | show resolved config with provenance |
| `capio trace <fn> --call "..."` | run one invocation with trace output |
| `capio profile <fn> --call "..."` | profile one invocation |
| `capio benchmark [scenario]` | run the official benchmark suite (RFC-027) |
| `capio test <plugin>` | run a plugin's contract tests (RFC-029) |
| `capio create-plugin <name>` | scaffold a plugin package (RFC-013 §3.3) |
| `capio create-capability <name>` | scaffold a capability (RFC-012) |
| `capio registry` | inspect registry contents/collisions (RFC-014) |
| `capio audit query ...` | query audit records (RFC-020) |
| `capio version` | version + api_version + compatibility info |

## 3. Command details

### 3.1 `capio doctor`

Reports: Python/runtime version, `api_version`, installed plugins with versions + trust posture
(RFC-026 §2), backend health (calls `health()` on configured backends, RFC-015 §6), config
profile, known compatibility mismatches, disk/executor settings, and blocking-probe results.
Exit code nonzero on any `error`-severity finding; `--json` for machine-readable output.

### 3.2 `capio inspect <fn>`

Targets a function by import path (`package.module:function`). Output:

```
search — mode: chained
  order (outer→inner): auth(1000) validate(900) rate_limit(850) cache(750) retry(700) trace(600)
  backends: cache=redis  trace=opentelemetry
  config source for retry.max_attempts: decorator (env CAPIO_RETRY_MAX_ATTEMPTS overridden)
  execution kinds: sync + async
  metadata: __capio__ v0.1.0
```

Also renders the same content the `capio graph` ASCII tree.

### 3.3 `capio graph <fn>`

ASCII/Unicode dependency + priority graph (RFC-005 §4):

```
auth ──► validate ──► rate_limit ──► circuit_breaker ──► cache ──► retry ──► timeout ──► trace ──► metrics
                                                                                              │
                                                                                        (function)
```

Optional `--mermaid`/`--dot` for export.

### 3.4 `capio trace` / `capio profile`

Run a single invocation of `<fn>` with the given args in a child process with the `debug`/`bench`
profile; print spans, events, metrics, and profiler output (RFC-019, RFC-027). Safe: runs in a
subprocess so a crash cannot affect the shell.

### 3.5 `capio create-plugin <name>`

Generates the canonical scaffold (RFC-013 §3.3): `pyproject.toml`, `capability.yaml`, `src/`,
config schema, a stub capability, README, LICENSE, and a contract-test skeleton. Validates the
name convention (`capio-<name>`).

### 3.6 `capio test <plugin>`

Discovers and runs the plugin's contract tests via the SDK (RFC-029) — kind matrix, lifecycle,
config, reentrancy, backend parity, degradation.

## 4. Developer experience

- `--json` output everywhere for scripting.
- `--profile`/`--env`/`--verbose` global flags matching config layers (RFC-009 §4.2).
- The CLI never requires a configured runtime to run `doctor`, `plugins`, `registry`, `version`,
  `manifest check`, or `create-plugin` (metadata-only paths, RFC-011 §3).
- IDE support (RFC-032 roadmap): LSP-style hover/validation for decorators via the type surface
  (RFC-003 §7) and `py.typed` (RFC-031).

## 5. Document Dependencies

- Config: RFC-009; plugin loading: RFC-011; manifest: RFC-013; registries: RFC-014; backends:
  RFC-015; observability: RFC-019; audit: RFC-020; errors: RFC-025; security: RFC-026; benchmarks:
  RFC-027; tests: RFC-029; scaffolding generator: RFC-031; roadmap: RFC-032.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
