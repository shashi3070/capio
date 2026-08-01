"""Idempotent capability (RFC-023 §9): idempotency-key replay protection."""

from __future__ import annotations

import asyncio
import hashlib
import threading
from typing import Any, Dict

from ..config import parse_duration
from ..context import Context
from ..events import Event
from ..exceptions import IdempotencyConflictError
from ..sdk.capability import CALL_NEXT, Capability


class Idempotent(Capability):
    name = "idempotent"
    version = "1.0.0"
    description = "Enforces idempotency keys so replays return the stored result (RFC-023 §9)."
    priority = 590
    degradation = "propagate"

    schema = {
        "header": {"type": "str", "default": "Idempotency-Key"},
        "key": {"type": "any", "default": None},
        "backend": {"type": "str", "default": "store.memory"},
        "ttl": {"type": "any", "default": "24h"},
        "replay": {"type": "str", "default": "return", "enum": ["return", "error"]},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.RLock()
        self._sync_locks: Dict[str, threading.Lock] = {}
        self._async_locks: Dict[str, asyncio.Lock] = {}

    def _key(self, ctx: Context) -> str | None:
        key_cfg = self.cfg.key
        if callable(key_cfg):
            return str(key_cfg(ctx))
        if key_cfg is not None:
            return str(key_cfg)
        carrier = ctx.carrier or {}
        return carrier.get(self.cfg.header)

    def _request_hash(self, ctx: Context) -> str:
        blob = f"{ctx.fn_module}.{ctx.fn_name}:{ctx.args!r}:{sorted(ctx.kwargs.items())!r}"
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        key = self._key(ctx)
        backend = self.backend(self.cfg.backend)
        if key is None or backend is None:
            return call_next(ctx)
        request_hash = self._request_hash(ctx)
        ttl = parse_duration(self.cfg.ttl) if self.cfg.ttl else None
        with self._lock:
            lock = self._sync_locks.setdefault(key, threading.Lock())
        with lock:
            entry = backend.get("idempotent", key, default=None)
            if entry is not None:
                if entry.get("request_hash") != request_hash:
                    ctx.emit(Event("idempotent.conflict", {"key": key}))
                    raise IdempotencyConflictError(
                        f"idempotency key {key!r} replayed with a different request"
                    )
                ctx.emit(Event("idempotent.replay", {"key": key}))
                if self.cfg.replay == "error":
                    raise IdempotencyConflictError(f"idempotency key {key!r} already used")
                return entry.get("result")
            result = call_next(ctx)
            entry = {"request_hash": request_hash, "result": result}
            backend.put("idempotent", key, entry, ttl=ttl)
            ctx.emit(Event("idempotent.stored", {"key": key}))
            return result

    async def run_async(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        key = self._key(ctx)
        backend = self.backend(self.cfg.backend)
        if key is None or backend is None:
            return await call_next(ctx)
        request_hash = self._request_hash(ctx)
        ttl = parse_duration(self.cfg.ttl) if self.cfg.ttl else None
        with self._lock:
            lock = self._async_locks.setdefault(key, asyncio.Lock())
        async with lock:
            entry = backend.get("idempotent", key, default=None)
            if entry is not None:
                if entry.get("request_hash") != request_hash:
                    ctx.emit(Event("idempotent.conflict", {"key": key}))
                    raise IdempotencyConflictError(
                        f"idempotency key {key!r} replayed with a different request"
                    )
                ctx.emit(Event("idempotent.replay", {"key": key}))
                if self.cfg.replay == "error":
                    raise IdempotencyConflictError(f"idempotency key {key!r} already used")
                return entry.get("result")
            result = await call_next(ctx)
            entry = {"request_hash": request_hash, "result": result}
            backend.put("idempotent", key, entry, ttl=ttl)
            ctx.emit(Event("idempotent.stored", {"key": key}))
            return result

