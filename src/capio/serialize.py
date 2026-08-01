"""Serializer registry (RFC-022 §3).

Registry-backed serialization used across boundaries (cache, queues/events,
RPC, persistence) and composed as the ``serialize`` capability.

Safe by default: ``json`` is the default serializer; unsafe serializers such as
``pickle`` are registered but only usable when the caller declares ``trust``
explicitly (RFC-026 §7).
"""

from __future__ import annotations

import json
import pickle
from typing import Any, Callable, Dict, Optional, Tuple

from .exceptions import SerializationError

_Serializer = Dict[str, Callable[[Any], Any]]

_serializers: Dict[str, _Serializer] = {}


def register_serializer(
    name: str,
    *,
    encode: Callable[[Any], Any],
    decode: Callable[[Any], Any],
    unsafe: bool = False,
) -> None:
    """Register (or idempotently re-register) a serializer under ``name``."""
    _serializers[name] = {"encode": encode, "decode": decode, "unsafe": unsafe}


def unregister_serializer(name: str) -> None:
    _serializers.pop(name, None)


def serializer_names() -> Tuple[str, ...]:
    return tuple(sorted(_serializers))


def get_serializer(name: str) -> Optional[_Serializer]:
    return _serializers.get(name)


def is_unsafe(name: str) -> bool:
    entry = _serializers.get(name)
    return bool(entry and entry.get("unsafe"))


def encode(obj: Any, serializer: str = "json") -> Any:
    entry = _serializers.get(serializer)
    if entry is None:
        raise SerializationError(f"unknown serializer {serializer!r}")
    try:
        return entry["encode"](obj)
    except SerializationError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize every encode failure
        raise SerializationError(f"serialize with {serializer!r} failed: {exc!r}") from exc


def decode(data: Any, serializer: str = "json") -> Any:
    entry = _serializers.get(serializer)
    if entry is None:
        raise SerializationError(f"unknown serializer {serializer!r}")
    try:
        return entry["decode"](data)
    except SerializationError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize every decode failure
        raise SerializationError(f"deserialize with {serializer!r} failed: {exc!r}") from exc


register_serializer("json", encode=json.dumps, decode=json.loads, unsafe=False)
register_serializer("pickle", encode=pickle.dumps, decode=pickle.loads, unsafe=True)
