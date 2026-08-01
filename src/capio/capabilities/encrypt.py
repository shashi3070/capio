"""Field encryption capability (RFC-022 §4): dependency-free stream cipher."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from typing import Any, Optional

from ..context import Context
from ..events import Event
from ..exceptions import EncryptionKeyError
from ..sdk.capability import CALL_NEXT, Capability


def _derive_key(secret: str) -> bytes:
    salt = hashlib.sha256(b"capio:encrypt:v1").digest()
    return hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, 100_000, dklen=32)


def encrypt_string(value: str, key: bytes) -> str:
    """Encrypt ``value`` to a base64 string using an HMAC-SHA256 keystream."""
    nonce = os.urandom(12)
    stream = hmac.new(key, nonce, hashlib.sha256).digest()
    data = bytes(c ^ stream[i % len(stream)] for i, c in enumerate(value.encode("utf-8")))
    return base64.b64encode(nonce + data).decode("ascii")


def decrypt_string(token: str, key: bytes) -> str:
    """Reverse :func:`encrypt_string`."""
    raw = base64.b64decode(token.encode("ascii"))
    nonce, data = raw[:12], raw[12:]
    stream = hmac.new(key, nonce, hashlib.sha256).digest()
    plain = bytes(c ^ stream[i % len(stream)] for i, c in enumerate(data))
    return plain.decode("utf-8")


class Encrypt(Capability):
    name = "encrypt"
    version = "1.0.0"
    description = "Encrypts sensitive fields before the call (RFC-022 §4)."
    priority = 690
    degradation = "propagate"

    schema = {
        "key": {"type": "any", "default": None},
        "fields": {"type": "any", "default": None},
        "envelope": {"type": "any", "default": None},
        "decrypt_fields": {"type": "any", "default": None},
        "strict": {"type": "bool", "default": True},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self._key: Optional[bytes] = None
        self._fields: list[str] = []
        self._envelope: list[str] = []
        self._decrypt_fields: list[str] = []

    def initialize(self, services: Any) -> None:
        super().initialize(services)
        self._fields = self._as_list(self.cfg.fields)
        self._envelope = self._as_list(self.cfg.envelope)
        self._decrypt_fields = self._as_list(self.cfg.decrypt_fields)

    def _as_list(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return [str(v) for v in value]

    def _resolve_key(self, ctx: Context) -> bytes:
        if self._key is not None:
            return self._key
        raw = self.cfg.key
        if callable(raw):
            raw = raw(ctx)
        if raw is None:
            env_key = self.backend("env.encryption_key")
            if env_key is not None:
                raw = getattr(env_key, "get", lambda _: None)("CAPIO_ENCRYPTION_KEY")
        if raw is None:
            if self.cfg.strict:
                ctx.emit(Event("encrypt.missing_key", {}))
                raise EncryptionKeyError("no encryption key configured")
            raw = "capio-ephemeral"
        self._key = raw if isinstance(raw, bytes) else _derive_key(str(raw))
        return self._key

    def run(self, ctx: Context, call_next: CALL_NEXT) -> Any:
        key = self._resolve_key(ctx)
        kwargs = dict(ctx.kwargs)
        for field in self._fields:
            if field in kwargs and isinstance(kwargs[field], str):
                kwargs[field] = encrypt_string(kwargs[field], key)
        for envelope in self._envelope:
            payload = kwargs.get(envelope)
            if isinstance(payload, dict):
                for field in list(payload):
                    if isinstance(payload[field], str):
                        payload[field] = encrypt_string(payload[field], key)
        ctx.kwargs = kwargs
        result = call_next(ctx)
        if self._decrypt_fields:
            if isinstance(result, dict):
                for field in self._decrypt_fields:
                    if field in result and isinstance(result[field], str):
                        try:
                            result[field] = decrypt_string(result[field], key)
                        except Exception:  # noqa: BLE001 - not encrypted, leave as-is
                            pass
        return result

