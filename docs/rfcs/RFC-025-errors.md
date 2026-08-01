# RFC-025: Error Handling & Exception Hierarchy

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies the **unified exception hierarchy**: the root `CapabilityException`, every
typed error the runtime, plugins, capabilities, and backends raise, the distinction between
capability errors and user exceptions, retry/exception semantics, and the catchability contract.
It is the enforcement of RFC-001 §4.6 ("failure is a contract, not an accident") and RFC-001 §7.10
(never silently change business behavior).

## 2. The hierarchy

```
BaseException
└── CapioCancellationBase            (never retried, never swallowed)
    └── CapioCancelledError
    └── CapioTimeoutError            (when caused by cancellation; see TimeoutError)
Exception
└── CapabilityException               (root of all Capio-specific errors)
    ├── RuntimeError_Group            (RFC-004 architecture errors)
    │   ├── CapioRuntimeError
    │   ├── LifecycleError
    │   │   ├── PluginIncompatibleError
    │   │   ├── PluginManifestError
    │   │   ├── PluginDependencyError
    │   │   ├── PluginSignatureError
    │   │   ├── PluginPermissionError
    │   │   └── PluginNameCollisionError
    │   ├── DependencyResolutionError
    │   │   ├── DependencyCycleError
    │   │   └── ServiceAlreadyBound
    │   └── ContextBindingError
    ├── ConfigurationGroup
    │   └── ConfigurationError
    │       ├── UnknownCapabilityError
    │       ├── DuplicateCapabilityError
    │       ├── UnsupportedExecutionKindError
    │       ├── ConflictingPipelineError
    │       └── PipelineConflictError
    ├── HookGroup
    │   ├── HookError
    │   ├── HookContractError
    │   └── AsyncHookOnSyncPathError
    ├── RegistryGroup
    │   ├── AmbiguousNameError
    │   └── NameCollisionError
    ├── ExecutionGroup
    │   ├── CapabilityRuntimeError    (wraps an inner exception from a capability step)
    │   ├── RetryExhaustedError
    │   ├── CircuitOpenError
    │   ├── CapioTimeoutError         (alias in timeout group)
    │   ├── RateLimitExceededError
    │   ├── ConcurrencyLimitError
    │   ├── DuplicateCallError
    │   └── CancellationError         (invocation cancelled; not CapioCancelledError)
    ├── DataGroup
    │   ├── ValidationError
    │   ├── SerializationError
    │   ├── EncryptionKeyError
    │   ├── CacheKeyError
    │   └── AuditWriteError
    ├── BackendGroup
    │   └── BackendException
    │       ├── BackendUnavailableError
    │       ├── BackendTimeoutError
    │       └── BackendContractError
    ├── AuthGroup
    │   ├── AuthenticationError
    │   ├── AuthorizationError
    │   └── PolicyEvaluationError
    ├── BusGroup
    │   ├── UnknownCommandError
    │   ├── CommandTimeoutError
    │   └── EventBusFullError
    └── IntegrationGroup
        ├── IntegrationError
        └── MCPError                  (RFC-030 §7)
```

### 2.1 Naming rule

Every node is a concrete class deriving only from its parent in this tree. Subclassing across
groups is prohibited (keeps `except` behavior predictable).

## 3. Capability errors vs user exceptions

- **Capability errors** (subclasses of `CapabilityException`) are raised BY capabilities,
  backends, or the runtime. They are typed, catchable, and NEVER silently swallowed.
- **User exceptions** are exceptions raised by the wrapped callable or inner user code. The
  engine does NOT wrap them by default: a `ValueError` from the function surfaces as the
  `ValueError` (preserving the function's contract, RFC-003 §9). `CapabilityRuntimeError`
  is raised ONLY when a capability/backend bug leaks an unexpected exception, with the inner
  exception as `__cause__` and the owning plugin attached.
- Retry (RFC-017) distinguishes by checking exception class against the hierarchy: `retry_on`
  default `Exception` excludes `CapabilityException` subclasses declared non-retryable
  (e.g. `CircuitOpenError`, `RateLimitExceededError` are non-retryable by default; retry/timeout
  internals document their retryability per RFC-017 §6).

## 4. Catchability contract

1. Catching `CapabilityException` catches ALL Capio-raised errors without catching user
   exceptions or cancellation.
2. Catching `CapabilityException` NEVER swallows `CapioCancelledError`/`CapioCancelledBase`
   — cancellation always propagates (it derives from `BaseException`).
3. Capabilities MUST raise the most specific error available; raising generic
   `CapabilityException` directly is a lint error (RFC-031 CI).
4. Every raised error carries: message, `runtime` name, `capability`/`plugin` owner, and a
   machine-readable `code` (dotted, e.g. `capio.retry.exhausted`) suitable for logging/alerting.
5. Errors are serializable (RFC-022 serializer) for event bus payloads and audit records
   (RFC-008 §2.1, RFC-020).

## 5. Retry & failure semantics

- `RetryExhaustedError` wraps the last exception as `__cause__` (with `on_final=False`,
  RFC-017 §2) or re-raises the ORIGINAL first exception (`on_final=True`).
- A failure inside a capability's own machinery (not the function) is a `CapabilityRuntimeError`
  and is retryable per the retry policy only if its `code` is listed in `retry_on`.
- Fail-safe degradation (RFC-005 §7) never swallows user exceptions; it only converts
  *capability/backend* failures into the configured degradation behavior.

## 6. Error context on the Context

Every handled/unhandled error is recorded on the Context: `ctx.exception()` (current), plus the
invocation's error list (`ctx.errors`). Hooks (`on_exception`, RFC-007 §3.1), events
(`invocation.failed`), metrics (outcome tag, RFC-019 §3.2), and audit (RFC-020 §3.3) all read
these records, so a single error is observable through every channel.

## 7. Error-handling extension

A plugin MAY register an **error-mapper** (registry: `error_mapper`) to translate framework
errors (e.g. FastAPI HTTPException) into hierarchy types at integration boundaries (RFC-013
integrations) — always preserving `__cause__`. Mappers are opt-in per integration and documented.

## 8. Document Dependencies

- Principles: RFC-001 §4.6/§7.10; degradation: RFC-005 §7; retry: RFC-017; backends: RFC-015;
  hooks: RFC-007; context: RFC-006; events: RFC-008; security: RFC-026; AI/MCP: RFC-030.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
