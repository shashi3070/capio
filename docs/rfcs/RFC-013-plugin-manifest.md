# RFC-013: Plugin Manifest & Packaging

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies the **Capability Manifest** (`capability.yaml`), distribution and naming
conventions for ecosystem packages, discovery entry points, versioning, and the future
**Capability Marketplace**. It is the packaging contract that lets third parties publish
`capio-redis`, `capio-openai`, `capio-fastapi`, etc. and lets the runtime validate, load, and
upgrade them safely.

## 2. The Capability Manifest

Every plugin package MUST ship a `capability.yaml` at the package root. It is read by the loader
without importing code (RFC-011 §4).

### 2.1 Schema (normative)

```yaml
name: capio-redis                # distribution/plugin name (RFC-002 §9.4)
version: 0.4.2                   # semver (RFC-032)
api_version: 1                   # runtime contract version (RFC-011 §8)
summary: Redis cache backend for Capio

runtime:
  python: ">=3.9"
  capio: ">=0.1,<1"
  profiles: [prod, test]         # profiles this plugin participates in (optional)

dependencies:                    # plugin dependencies (RFC-011 §6)
  capio-core: ">=0.1"
  capio-serializer: ">=0.2"      # optional: {name: ..., version: ..., optional: true}

capabilities:
  - id: cache.backend.redis
    kind: backend                # backend | capability | hook | integration
    implements: cache            # the interface/capability kind (RFC-015)
    class: capio_redis.backend:RedisBackend
    priority: 0                  # pipeline priority if a capability (RFC-005 §4)
    supports: [sync, async]
    repeatable: false            # RFC-003 §3.2 rule 3
    overridable: false           # RFC-005 §6.3
    degradation: bypass          # RFC-005 §7.1
  - id: capability.llm_cache
    kind: capability
    class: capio_redis.llm:LLMCache
    depends_on: [cache.backend.redis]
    hooks: [before_cache_lookup, after_cache_lookup]

hooks: [before_cache_lookup, after_cache_lookup]   # hook names provided (RFC-007)
backends: [cache]                # backend interfaces provided
permissions: [network_client]    # declared permissions (RFC-026 §4)
config:                          # config namespace (RFC-009 §4.1)
  namespace: capio_redis
  schema_ref: capio_redis.config:SCHEMA
signature:                       # optional (RFC-026 §5)
  algorithm: ed25519
  public_key_ref: capio_redis:key
  value: <signature>
metadata:
  author: ...
  license: MIT
  repository: ...
  tags: [cache, redis]
```

### 2.2 Manifest rules

1. `api_version` MUST match the runtime's; mismatch → `PluginIncompatibleError` (RFC-011 §4).
2. `name` MUST match the distribution name and entry-point namespace.
3. Every `class` reference is a module-path `:` attribute-path string, importable without side
   effects at validation (import is deferred to load).
4. Unknown top-level keys fail validation (typo safety).
5. `capability.yaml` MUST be pure data — no expressions, no includes (except `extends` of other
   manifests from declared dependencies, which is resolved at validation).

## 3. Distribution conventions

### 3.1 Naming

- **Ecosystem package**: `<capio>-<name>` — `capio-redis`, `capio-openai`, `capio-fastapi`.
- **Import package**: `capio_<name>` — `capio_redis`, `capio_openai`, `capio_fastapi`.
- Base package import remains `capio`. Reserved: `capio`, `capio_core`, `capio_sdk`.

### 3.2 Entry points

A plugin declares entry points in its `pyproject.toml`:

```toml
[project.entry-points."capio.plugins"]
capio-redis = "capio_redis:plugin"       # points to a Plugin module/attr

[project.entry-points."capio.backends"]
cache.redis = "capio_redis.backend:RedisBackend"
```

The loader reads these metadata entry points for discovery without importing (RFC-011 §3).

### 3.3 Package layout (canonical)

```
capio-redis/
├── pyproject.toml
├── capability.yaml
├── src/capio_redis/
│   ├── __init__.py        # exposes `plugin` (load entry) and capabilities
│   ├── backend.py         # RedisCacheBackend
│   ├── config.py          # schema
│   └── tests/             # contract tests + golden tests (RFC-029)
├── README.md
└── LICENSE
```

`capio create-plugin capio-redis` generates exactly this scaffold (RFC-028).

### 3.4 Extras & dependency hygiene

- A plugin MUST NOT add the `capio` base package as a hard dependency of unrelated extras; base
  `capio` is a peer/runtime dependency (`requires: capio>=...`).
- Plugins should declare optional integrations as extras (`capio-fastapi[full]`) per PEP 621
  conventions.
- Binary/compiled dependencies must be declared and documented; `capio doctor` flags wheel-only
  platforms (RFC-028).

## 4. Capability Marketplace metadata

When the Marketplace ships (RFC-032 roadmap), published plugins expose additional metadata in the
manifest, surfaced in `capio plugins` and the marketplace index:

- **Maintenance**: latest release date, deprecation flag, archived flag.
- **Security**: signature verification status, permission declarations, audit status, known-issue
  references.
- **Compatibility**: verified matrix (runtime `api_version` × Python versions × host OS),
  contract-test results.
- **Dependents**: number of downstream packages and health.

## 5. Versioning rules

1. Plugins follow semver (RFC-032): a manifest-visible behavior change or added/removed
   capability is a major/minor bump accordingly.
2. A plugin's declared `dependencies` version ranges MUST be resolvable at load; the loader uses
   installed distribution metadata (not PyPI) for resolution.
3. Runtime upgrades that change `api_version` MUST ship a compatibility matrix and a migration
   guide; `capio doctor` detects mismatches before problems appear.

## 6. Publishing checklist

A plugin is publishable when it has:

- a valid `capability.yaml` (validated by `capio manifest check`),
- passing `capio test` contract tests (RFC-029),
- a README with install + example (RFC-031 docs standard),
- an OSI license (base project uses MIT),
- a changelog,
- signed artifacts if the project opts into signing (RFC-026 §5).

## 7. Document Dependencies

- Plugin system: RFC-011; SDK: RFC-012; registries: RFC-014; backends: RFC-015; security:
  RFC-026; CLI: RFC-028; tests: RFC-029; governance/versioning: RFC-032; migration: RFC-033.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
