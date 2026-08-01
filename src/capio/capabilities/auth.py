"""Authentication/authorization capability (RFC-020 §3)."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from ..context import Context
from ..events import Event
from ..exceptions import (
    AuthenticationError,
    AuthorizationError,
    PolicyEvaluationError,
)
from ..sdk.capability import CALL_NEXT, Capability


class Auth(Capability):
    name = "auth"
    version = "1.0.0"
    description = "Authenticates the caller and enforces scopes/policies (RFC-020 §3)."
    priority = 710
    degradation = "propagate"

    schema = {
        "provider": {"type": "any", "default": None},
        "required": {"type": "bool", "default": True},
        "scopes": {"type": "any", "default": None},
        "policy": {"type": "any", "default": None},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self._provider: Optional[Callable[[Context], Optional[Dict[str, Any]]]] = None
        self._policy: Optional[Callable[[Dict[str, Any], Context], bool]] = None

    def initialize(self, services: Any) -> None:
        super().initialize(services)
        if callable(self.cfg.provider):
            self._provider = self.cfg.provider
        if callable(self.cfg.policy):
            self._policy = self.cfg.policy

    def _scopes(self, ctx: Context) -> List[str]:
        scopes = self.cfg.scopes
        if scopes is None:
            return []
        if callable(scopes):
            return list(scopes(ctx))
        if isinstance(scopes, str):
            return [scopes]
        return list(scopes)

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        identity = self._provider(ctx) if self._provider is not None else None

        if identity is None:
            if self.cfg.required:
                ctx.emit(Event("auth.denied", {"reason": "unauthenticated"}))
                raise AuthenticationError("authentication required")
            identity = {"anonymous": True}
        ctx.auth = identity
        ctx.emit(Event("auth.authenticated", {"actor": identity.get("subject", "anonymous")}))

        required = self._scopes(ctx)
        if required:
            granted = set(identity.get("scopes", []) or [])
            missing = [s for s in required if s not in granted]
            if missing:
                ctx.emit(Event("auth.denied", {"reason": "insufficient_scope", "missing": missing}))
                raise AuthorizationError(f"missing required scope(s): {', '.join(missing)}")

        if self._policy is not None and not self._policy(identity, ctx):
            ctx.emit(Event("auth.denied", {"reason": "policy_failed"}))
            raise PolicyEvaluationError("policy evaluation failed")
        return call_next(ctx)
