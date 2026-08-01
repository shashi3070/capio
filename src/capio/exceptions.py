"""Capio exception hierarchy (RFC-025).

The tree mirrors RFC-025 section 2: ``CapioCancelledBase`` derives from ``BaseException``
(never retried, never swallowed), and every other Capio error derives from
``CapabilityException``.
"""

from __future__ import annotations

from typing import Any, ClassVar, Optional


class CapioCancelledBase(BaseException):
    """Root of all Capio cancellation errors. Never retried, never swallowed."""

    code: ClassVar[str] = "capio.cancelled"


class CapioCancelledError(CapioCancelledBase):
    """The invocation was cancelled cooperatively."""

    code: ClassVar[str] = "capio.cancelled.error"


class CapioTimeoutError(CapioCancelledBase):
    """The invocation exceeded its time budget (RFC-018 §3)."""

    code: ClassVar[str] = "capio.timeout.exceeded"

    def __init__(self, message: str = "invocation timed out", *, seconds: Optional[float] = None):
        super().__init__(message)
        self.message = message
        self.seconds = seconds


class CapabilityException(Exception):
    """Root of all Capio-specific errors (RFC-025 §2)."""

    code: ClassVar[str] = "capio.error"

    def __init__(
        self,
        message: str = "",
        *,
        runtime: str = "default",
        capability: Optional[str] = None,
        plugin: Optional[str] = None,
        code: Optional[str] = None,
        **extra: Any,
    ):
        super().__init__(message)
        self.message = message
        self.runtime = runtime
        self.capability = capability
        self.plugin = plugin
        if code is not None:
            self.code = code
        self.extra = extra


# ---------------------------------------------------------------------------
# Runtime group (RFC-004 architecture errors)
# ---------------------------------------------------------------------------


class CapioRuntimeError(CapabilityException):
    """General runtime architecture error."""

    code: ClassVar[str] = "capio.runtime.error"


class LifecycleError(CapabilityException):
    """Capability/plugin lifecycle error (RFC-011 §7)."""

    code: ClassVar[str] = "capio.lifecycle.error"


class PluginIncompatibleError(LifecycleError):
    code: ClassVar[str] = "capio.plugin.incompatible"


class PluginManifestError(LifecycleError):
    code: ClassVar[str] = "capio.plugin.manifest"


class PluginDependencyError(LifecycleError):
    code: ClassVar[str] = "capio.plugin.dependency"


class PluginSignatureError(LifecycleError):
    code: ClassVar[str] = "capio.plugin.signature"


class PluginPermissionError(LifecycleError):
    code: ClassVar[str] = "capio.plugin.permission"


class PluginNameCollisionError(LifecycleError):
    code: ClassVar[str] = "capio.plugin.collision"


class DependencyResolutionError(CapabilityException):
    """A declared dependency could not be resolved (RFC-005 §5)."""

    code: ClassVar[str] = "capio.dependency.resolution"


class DependencyCycleError(DependencyResolutionError):
    code: ClassVar[str] = "capio.dependency.cycle"


class ServiceAlreadyBound(DependencyResolutionError):
    code: ClassVar[str] = "capio.dependency.bound"


class ContextBindingError(CapabilityException):
    """A lazily-bound context handle could not be resolved (RFC-006 §2.3)."""

    code: ClassVar[str] = "capio.context.binding"


# ---------------------------------------------------------------------------
# Configuration group (RFC-009 / RFC-003 §9)
# ---------------------------------------------------------------------------


class ConfigurationError(CapabilityException):
    """Invalid or conflicting configuration."""

    code: ClassVar[str] = "capio.config.error"


class UnknownCapabilityError(ConfigurationError):
    """A capability name is not registered (RFC-003 §2.3)."""

    code: ClassVar[str] = "capio.capability.unknown"


class DuplicateCapabilityError(ConfigurationError):
    """The same non-repeatable capability was applied twice (RFC-003 §3.2)."""

    code: ClassVar[str] = "capio.capability.duplicate"


class UnsupportedExecutionKindError(ConfigurationError):
    """A capability was applied to a callable kind it does not support (RFC-003 §3.3)."""

    code: ClassVar[str] = "capio.execution.unsupported_kind"


class ConflictingPipelineError(ConfigurationError):
    """Capio metadata merge conflict (RFC-003 §5.1)."""

    code: ClassVar[str] = "capio.pipeline.conflicting"


class PipelineConflictError(ConfigurationError):
    """Capability ordering constraint conflict (RFC-005 §4.4)."""

    code: ClassVar[str] = "capio.pipeline.conflict"


# ---------------------------------------------------------------------------
# Hook group (RFC-007)
# ---------------------------------------------------------------------------


class HookError(CapabilityException):
    code: ClassVar[str] = "capio.hook.error"


class HookContractError(HookError):
    code: ClassVar[str] = "capio.hook.contract"


class AsyncHookOnSyncPathError(HookError):
    code: ClassVar[str] = "capio.hook.async_on_sync"


# ---------------------------------------------------------------------------
# Registry group (RFC-014)
# ---------------------------------------------------------------------------


class AmbiguousNameError(CapabilityException):
    code: ClassVar[str] = "capio.registry.ambiguous"


class NameCollisionError(CapabilityException):
    code: ClassVar[str] = "capio.registry.collision"


# ---------------------------------------------------------------------------
# Execution group
# ---------------------------------------------------------------------------


class CapabilityRuntimeError(CapabilityException):
    """An unexpected exception leaked from a capability/backend step (RFC-025 §3)."""

    code: ClassVar[str] = "capio.execution.capability_runtime"


class RetryExhaustedError(CapabilityException):
    """Retry gave up (RFC-017). The last exception is attached as ``__cause__``."""

    code: ClassVar[str] = "capio.retry.exhausted"


class CircuitOpenError(CapabilityException):
    """The circuit breaker is open (RFC-018 §2). Non-retryable by default."""

    code: ClassVar[str] = "capio.breaker.open"


class RateLimitExceededError(CapabilityException):
    """Rate limit exceeded (RFC-018 §4). Non-retryable by default."""

    code: ClassVar[str] = "capio.ratelimit.exceeded"

    def __init__(
        self, message: str = "rate limit exceeded", *, retry_after: Optional[float] = None
    ):
        super().__init__(message)
        self.retry_after = retry_after


class ConcurrencyLimitError(CapabilityException):
    """Concurrency (throttle) limit exceeded (RFC-018 §5)."""

    code: ClassVar[str] = "capio.execution.concurrency"


class DuplicateCallError(CapabilityException):
    code: ClassVar[str] = "capio.execution.duplicate_call"


class CancellationError(CapabilityException):
    """Invocation cancelled (not the uncatchable CapioCancelledError)."""

    code: ClassVar[str] = "capio.execution.cancelled"


class IdempotencyConflictError(CapabilityException):
    """An idempotency key was replayed with a different request (RFC-023 §5)."""

    code: ClassVar[str] = "capio.execution.idempotency_conflict"


class TransactionError(CapabilityException):
    """A transactional scope failed or could not be completed (RFC-023 §4)."""

    code: ClassVar[str] = "capio.execution.transaction"


class WorkflowError(CapabilityException):
    """A workflow step failed or the workflow could not be recovered (RFC-023 §6)."""

    code: ClassVar[str] = "capio.execution.workflow"


class GuardrailError(CapabilityException):
    """An input/output guardrail check failed (RFC-030 §6)."""

    code: ClassVar[str] = "capio.guardrail.error"


class TokenBudgetExceededError(CapabilityException):
    """A token budget was exceeded (RFC-030 §7)."""

    code: ClassVar[str] = "capio.ai.token_budget"

    def __init__(self, message: str = "token budget exceeded", *, used: int = 0, budget: int = 0):
        super().__init__(message)
        self.used = used
        self.budget = budget


class ProviderError(CapabilityException):
    """A model/LLM provider call failed (RFC-030 §2)."""

    code: ClassVar[str] = "capio.ai.provider"


class ToolError(CapabilityException):
    """A tool call could not be dispatched or failed (RFC-030 §5)."""

    code: ClassVar[str] = "capio.ai.tool"


# ---------------------------------------------------------------------------
# Data group (RFC-022)
# ---------------------------------------------------------------------------


class ValidationError(CapabilityException):
    code: ClassVar[str] = "capio.data.validation"


class SerializationError(CapabilityException):
    code: ClassVar[str] = "capio.data.serialization"


class EncryptionKeyError(CapabilityException):
    code: ClassVar[str] = "capio.data.encryption_key"


class CacheKeyError(CapabilityException):
    code: ClassVar[str] = "capio.data.cache_key"


class AuditWriteError(CapabilityException):
    code: ClassVar[str] = "capio.data.audit_write"


# ---------------------------------------------------------------------------
# Backend group (RFC-015)
# ---------------------------------------------------------------------------


class BackendException(CapabilityException):
    """Root of all backend failures. Degradable per RFC-005 §7."""

    code: ClassVar[str] = "capio.backend.error"


class BackendUnavailableError(BackendException):
    code: ClassVar[str] = "capio.backend.unavailable"


class BackendTimeoutError(BackendException):
    code: ClassVar[str] = "capio.backend.timeout"


class BackendContractError(BackendException):
    code: ClassVar[str] = "capio.backend.contract"


# ---------------------------------------------------------------------------
# Auth group (RFC-021)
# ---------------------------------------------------------------------------


class AuthenticationError(CapabilityException):
    code: ClassVar[str] = "capio.auth.authentication"


class AuthorizationError(CapabilityException):
    code: ClassVar[str] = "capio.auth.authorization"


class PolicyEvaluationError(CapabilityException):
    code: ClassVar[str] = "capio.auth.policy"


# ---------------------------------------------------------------------------
# Bus group (RFC-008)
# ---------------------------------------------------------------------------


class UnknownCommandError(CapabilityException):
    code: ClassVar[str] = "capio.bus.unknown_command"


class CommandTimeoutError(CapabilityException):
    code: ClassVar[str] = "capio.bus.timeout"


class EventBusFullError(CapabilityException):
    code: ClassVar[str] = "capio.bus.full"


# ---------------------------------------------------------------------------
# Integration group
# ---------------------------------------------------------------------------


class IntegrationError(CapabilityException):
    code: ClassVar[str] = "capio.integration.error"


class MCPError(IntegrationError):
    code: ClassVar[str] = "capio.mcp.error"


# Exceptions that are always excluded from retry unless explicitly listed.
NON_RETRYABLE_ALWAYS: tuple[type[BaseException], ...] = (
    KeyboardInterrupt,
    SystemExit,
    MemoryError,
    CapioCancelledBase,
)

# Capio exceptions that are non-retryable by default.
NON_RETRYABLE_CAPIO: tuple[type[CapabilityException], ...] = (
    CircuitOpenError,
    RateLimitExceededError,
    ConcurrencyLimitError,
    ConfigurationError,
    IdempotencyConflictError,
    GuardrailError,
    TokenBudgetExceededError,
    SerializationError,
)
