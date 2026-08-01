"""Audit, auth, validate, encrypt, mask, dedup capability tests (RFC-020/022)."""

from __future__ import annotations

import pytest

from capio import use
from capio.exceptions import (
    AuthenticationError,
    AuthorizationError,
    ValidationError,
)
from capio.runtime import default_runtime


def test_audit_records_trail() -> None:
    runtime = default_runtime()
    backend = runtime.services.get("audit.memory")
    backend.clear()

    @use.audit(actor="alice", action="publish")
    def do_stuff() -> str:
        return "done"

    do_stuff()
    records = backend.query(actor="alice", action="publish")
    assert len(records) == 1
    assert records[0]["outcome"] == "success"
    assert backend.verify()


def test_audit_records_error_outcome() -> None:
    runtime = default_runtime()
    backend = runtime.services.get("audit.memory")
    backend.clear()

    @use.audit(actor="bob")
    def boom() -> None:
        raise ValueError("nope")

    with pytest.raises(ValueError):
        boom()
    records = backend.query(actor="bob")
    assert len(records) == 1
    assert records[0]["outcome"] == "error"


def test_auth_accepts_identity_and_scopes() -> None:
    seen = {}

    def provider(ctx):
        if ctx.kwargs.get("token") == "secret":
            return {"subject": "alice", "scopes": ["read", "write"]}
        return None

    @use.auth(provider=provider, scopes=["read"])
    @use.context()
    def secret(token: str, ctx) -> str:
        seen["actor"] = ctx.auth["subject"]
        return "ok"

    assert secret(token="secret") == "ok"
    assert seen["actor"] == "alice"


def test_auth_rejects_missing_identity() -> None:
    def provider(ctx):
        return None

    @use.auth(provider=provider, required=True)
    def secret() -> str:
        return "s3cr3t"

    with pytest.raises(AuthenticationError):
        secret()


def test_auth_rejects_missing_scope() -> None:
    def provider(ctx):
        return {"subject": "alice", "scopes": ["read"]}

    @use.auth(provider=provider, scopes=["admin"])
    def secret() -> str:
        return "s3cr3t"

    with pytest.raises(AuthorizationError):
        secret()


def test_validate_input_checks_types_and_ranges() -> None:
    @use.validate(input={"age": {"type": "int", "min": 18}})
    def signup(name: str, age: int) -> str:
        return "ok"

    assert signup("bob", age=21) == "ok"
    with pytest.raises(ValidationError):
        signup("bob", age=10)


def test_validate_output() -> None:
    @use.validate(output={"type": "int"})
    def compute() -> int:
        return 7

    assert compute() == 7


def test_validate_output_failure() -> None:
    @use.validate(output={"type": "int"})
    def compute() -> str:
        return "not-an-int"

    with pytest.raises(ValidationError):
        compute()


def test_encrypt_roundtrip() -> None:
    calls = {}

    @use.encrypt(key="secret-key", fields=["password"], decrypt_fields=["password"])
    def login(username: str, password: str) -> dict:
        calls["seen"] = password
        return {"user": username, "password": password}

    result = login("bob", password="hunter2")
    assert calls["seen"] != "hunter2"
    assert result["password"] == "hunter2"


def test_mask_args_and_result() -> None:
    seen = {}

    @use.mask(fields=["secret"], mode="both")
    def handler(secret: str, other: str) -> dict:
        seen["secret"] = secret
        return {"secret": secret, "other": other}

    result = handler(secret="abc123", other="x")
    assert seen["secret"] == "******"
    assert result["secret"] == "******"
    assert result["other"] == "x"


def test_dedup_reuses_result_within_ttl() -> None:
    state = {"n": 0}

    @use.dedup(ttl="5s")
    def compute(x: int) -> int:
        state["n"] += 1
        return x * 2

    assert compute(3) == 6
    assert compute(3) == 6
    assert state["n"] == 1
    assert compute(4) == 8
    assert state["n"] == 2
