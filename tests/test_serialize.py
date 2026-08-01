"""Serialize capability + serializer registry tests (RFC-022 §3, RFC-026 §7)."""

from __future__ import annotations

import json

import pytest

import capio.serialize as capio_serialize
from capio import use
from capio.exceptions import ConfigurationError, SerializationError


def test_serialize_in_encodes_field() -> None:
    seen = {}

    @use.serialize(fields=["payload"], mode="in")
    @use.context()
    def store(payload, ctx) -> str:
        seen["payload"] = payload
        return "ok"

    store(payload={"a": 1, "b": [2, 3]})
    assert isinstance(seen["payload"], str)
    assert json.loads(seen["payload"]) == {"a": 1, "b": [2, 3]}


def test_serialize_out_decodes_result() -> None:
    @use.serialize(mode="out")
    def load() -> str:
        return json.dumps({"ok": True})

    assert load() == {"ok": True}


def test_serialize_both_roundtrip() -> None:
    seen = {}

    @use.serialize()
    @use.context()
    def roundtrip(data, ctx) -> str:
        seen["data"] = data
        return json.dumps({"echo": data})

    result = roundtrip(data={"x": 1})
    assert isinstance(seen["data"], str)
    assert result == {"echo": '{"x": 1}'}


def test_serialize_unknown_serializer_raises() -> None:
    @use.serialize(serializer="nope")
    def fn(x):
        return x

    with pytest.raises(SerializationError):
        fn(x=1)


def test_serialize_unserializable_value_raises() -> None:
    @use.serialize(mode="in")
    def fn(x):
        return x

    with pytest.raises(SerializationError):
        fn(x={"nope": object()})


def test_serialize_pickle_requires_trust() -> None:
    @use.serialize(serializer="pickle", mode="in")
    def fn(x):
        return x

    with pytest.raises(ConfigurationError):
        fn(x=1)


def test_serialize_pickle_with_trust() -> None:
    seen = {}

    @use.serialize(serializer="pickle", mode="in", trust=True)
    @use.context()
    def fn(x, ctx) -> str:
        seen["x"] = x
        return "ok"

    fn(x={"a": 1})
    assert isinstance(seen["x"], bytes)


def test_serialize_async_roundtrip() -> None:
    import asyncio

    @use.serialize()
    async def fetch(payload) -> str:
        return json.dumps({"got": payload})

    result = asyncio.run(fetch(payload={"k": "v"}))
    assert result == {"got": '{"k": "v"}'}


def test_serializer_registry_custom() -> None:
    def encode(obj):
        return str(obj).upper()

    def decode(data):
        return data.lower()

    capio_serialize.register_serializer("upper", encode=encode, decode=decode)
    try:
        assert capio_serialize.encode("hello", "upper") == "HELLO"
        assert capio_serialize.decode("HELLO", "upper") == "hello"
        assert "upper" in capio_serialize.serializer_names()
    finally:
        capio_serialize.unregister_serializer("upper")


def test_serializer_registry_unknown() -> None:
    with pytest.raises(SerializationError):
        capio_serialize.encode({"a": 1}, "does-not-exist")
    with pytest.raises(SerializationError):
        capio_serialize.decode("x", "does-not-exist")
