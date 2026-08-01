"""Context object, cancellation token, and in-process propagation scope (RFC-006)."""

from __future__ import annotations

import itertools
import os
import threading
import time
import uuid
from contextvars import ContextVar, Token
from typing import Any, Callable, Dict, List, Mapping, Optional

from .events import Event, EventBus
from .exceptions import CapioCancelledError

_id_counter = itertools.count(1)


def _new_id(prefix: str) -> str:
    """Monotonic counter + process-random suffix; unique within and across processes."""
    return f"{prefix}-{next(_id_counter)}-{uuid.uuid4().hex[:8]}"


class CancellationToken:
    """Cooperative cancellation flag (RFC-006 §8.3)."""

    __slots__ = ("_cancelled",)

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise CapioCancelledError("invocation cancelled")


_scope_var: "ContextVar[Optional[Context]]" = ContextVar("capio_ctx", default=None)


class ContextScope:
    """Registers a Context in the current execution context (ContextVars, RFC-006 §6)."""

    __slots__ = ("_ctx", "_token")

    def __init__(self, ctx: "Context") -> None:
        self._ctx = ctx
        self._token: Optional[Token] = None

    def enter(self) -> None:
        self._token = _scope_var.set(self._ctx)

    def exit(self) -> None:
        if self._token is not None:
            _scope_var.reset(self._token)
            self._token = None

    @property
    def current(self) -> Optional["Context"]:
        return _scope_var.get()


def current_context() -> Optional["Context"]:
    """Return the innermost active Context, or None outside Capio code (RFC-006 §6)."""
    return _scope_var.get()


class Context:
    """Per-invocation state container (RFC-006 §2). Mutable by capabilities; identity and
    input fields are set at creation and never mutated.
    """

    __slots__ = (
        "_invocation_id",
        "_request_id",
        "_correlation_id",
        "_parent_id",
        "args",
        "kwargs",
        "fn",
        "fn_name",
        "fn_module",
        "cls",
        "self_or_cls",
        "config",
        "profile",
        "env",
        "strict",
        "capabilities",
        "plugin_state",
        "logger",
        "tracer",
        "metrics",
        "cache",
        "auth",
        "cancel",
        "deadline",
        "trace_id",
        "span_id",
        "start_time",
        "thread_id",
        "process_id",
        "loop",
        "carrier",
        "scope",
        "_event_bus",
        "_result",
        "_result_set",
        "_exception",
        "_errors",
        "_started",
    )

    def __init__(
        self,
        *,
        fn: Callable[..., Any],
        args: tuple = (),
        kwargs: Optional[Dict[str, Any]] = None,
        cls: Optional[type] = None,
        self_or_cls: Any = None,
        config: Optional[Mapping[str, Any]] = None,
        profile: str = "default",
        env: str = "dev",
        strict: bool = False,
        carrier: Optional[Mapping[str, str]] = None,
        parent: Optional["Context"] = None,
        event_bus: Optional[EventBus] = None,
        invocation_id: Optional[str] = None,
        request_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        self.fn = fn
        self.args = args
        self.kwargs = dict(kwargs or {})
        self.cls = cls
        self.self_or_cls = self_or_cls
        self.fn_name = getattr(fn, "__name__", type(fn).__name__)
        self.fn_module = getattr(fn, "__module__", "")
        self.config = config or {}
        self.profile = profile
        self.env = env
        self.strict = strict
        self.carrier = carrier
        self.loop = None
        self.auth = None
        self.trace_id = None
        self.span_id = None
        self.logger = None
        self.tracer = None
        self.metrics = None
        self.cache = None
        self.deadline = None

        now = time.monotonic()
        # IDs are generated lazily on first access (RFC-027 §2.3: avoid allocation in hot path).
        self._invocation_id = invocation_id
        self._request_id = request_id
        self._correlation_id = correlation_id
        self._parent_id = parent.invocation_id if parent is not None else None

        self.cancel = CancellationToken()
        self.start_time = now
        self.thread_id = threading.get_ident()
        self.process_id = os.getpid()
        self.capabilities: Dict[str, Dict[str, Any]] = {}
        self.plugin_state: Dict[str, Any] = {}
        self._event_bus = event_bus
        self._result = None
        self._result_set = False
        self._exception: Optional[BaseException] = None
        self._errors: List[BaseException] = []
        self._started = now
        self.scope = ContextScope(self)

    # -- lazy identifiers ----------------------------------------------------
    @property
    def invocation_id(self) -> str:
        if self._invocation_id is None:
            self._invocation_id = _new_id("inv")
        return self._invocation_id

    @property
    def request_id(self) -> str:
        if self._request_id is None:
            self._request_id = _new_id("req")
        return self._request_id

    @property
    def correlation_id(self) -> str:
        if self._correlation_id is None:
            self._correlation_id = self.request_id
        return self._correlation_id

    @property
    def parent_id(self) -> Optional[str]:
        return self._parent_id

    # -- result / exception -------------------------------------------------
    def result(self) -> Any:
        return self._result

    def set_result(self, value: Any) -> None:
        self._result = value
        self._result_set = True

    def has_result(self) -> bool:
        return self._result_set

    def exception(self) -> Optional[BaseException]:
        return self._exception

    def set_exception(self, exc: BaseException) -> None:
        self._exception = exc
        self._errors.append(exc)

    @property
    def errors(self) -> List[BaseException]:
        return self._errors

    # -- per-capability state slot (RFC-006 §2.3) ---------------------------
    def capability(self, name: str) -> Dict[str, Any]:
        slot = self.capabilities.get(name)
        if slot is None:
            slot = {"state": {}}
            self.capabilities[name] = slot
        return slot

    # -- events --------------------------------------------------------------
    def emit(self, event: Event) -> None:
        if self._event_bus is not None:
            if event.ctx is None:
                event.ctx = self.snapshot()
            self._event_bus.publish(event)

    # -- handles ---------------------------------------------------------------
    def bind(self, name: str, value: Any) -> None:
        setattr(self, name, value)

    # -- serialization ----------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        """A frozen, redacted, JSON-serializable view (RFC-006 §9). Raw args are excluded."""
        return {
            "invocation_id": self.invocation_id,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "parent_id": self.parent_id,
            "fn": f"{self.fn_module}.{self.fn_name}",
            "profile": self.profile,
            "env": self.env,
            "strict": self.strict,
            "start_time": self.start_time,
            "duration": time.monotonic() - self.start_time,
            "args": {"count": len(self.args), "keys": sorted(self.kwargs)},
            "thread_id": self.thread_id,
            "process_id": self.process_id,
            "capabilities": [str(name) for name in self.capabilities],
            "error": repr(self._exception) if self._exception is not None else None,
        }

    def to_dict(self) -> Dict[str, Any]:
        """Alias of :meth:`snapshot` (RFC-006 §4)."""
        return self.snapshot()
