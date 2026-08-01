"""Configuration primitives: FrozenConfig, schema validation, durations (RFC-009)."""

from __future__ import annotations

import re
from types import MappingProxyType
from typing import Any, Dict, Iterator, Mapping, Optional

from .exceptions import ConfigurationError

_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h)?\s*$")
_DURATION_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


def parse_duration(value: Any) -> float:
    """Parse a duration into seconds.

    Accepts a number (seconds) or a string like ``"100ms"``, ``"5m"``, ``"1.5h"``.
    """
    if isinstance(value, bool):
        raise ConfigurationError(f"invalid duration: {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = _DURATION_RE.match(value)
        if not match:
            raise ConfigurationError(f"invalid duration: {value!r}")
        number = float(match.group(1))
        unit = match.group(2) or "s"
        return number * _DURATION_UNITS[unit]
    raise ConfigurationError(f"invalid duration: {value!r}")


class FrozenConfig(Mapping[str, Any]):
    """An immutable, attribute-accessible configuration view (RFC-009).

    Supports both ``cfg.max_attempts`` and ``cfg["max_attempts"]``.
    """

    __slots__ = ("_data",)

    def __init__(self, data: Optional[Mapping[str, Any]] = None):
        object.__setattr__(self, "_data", MappingProxyType(dict(data or {})))

    def __getattr__(self, name: str) -> Any:
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(name) from None

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def as_dict(self) -> Dict[str, Any]:
        """Return a mutable copy as a plain dict."""
        return dict(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def __repr__(self) -> str:
        return f"FrozenConfig({self._data!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, FrozenConfig):
            return self._data == other._data
        if isinstance(other, Mapping):
            return dict(self._data) == dict(other)
        return False

    def __hash__(self) -> int:
        return hash(frozenset(self._data.items()))


_SCHEMA_TYPES = {
    "str": str,
    "int": int,
    "float": (int, float),
    "number": (int, float),
    "bool": bool,
    "list": list,
    "dict": dict,
    "any": object,
}


def validate_config(schema: Mapping[str, Any], options: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate ``options`` against ``schema`` and return a fully-defaulted dict.

    Schema shape (RFC-012 §2): ``{key: {"type": ..., "default": ..., "enum": [...], "min": ...}}``.
    ``None`` option values fall back to the schema default (RFC-003 §2.3).
    """
    resolved: Dict[str, Any] = {}
    for key, spec in schema.items():
        default = spec.get("default")
        value = options.get(key, default)
        if value is None:
            value = default
        type_name = spec.get("type", "any")
        if type_name not in _SCHEMA_TYPES:
            raise ConfigurationError(f"unknown schema type {type_name!r} for {key!r}")
        if value is not None and type_name != "any":
            expected = _SCHEMA_TYPES[type_name]
            if not isinstance(value, expected):
                raise ConfigurationError(
                    f"option {key!r}: expected {type_name}, got {type(value).__name__}"
                )
        if value is not None:
            enum = spec.get("enum")
            if enum is not None and value not in enum:
                raise ConfigurationError(f"option {key!r}: {value!r} not in {enum!r}")
            min_val = spec.get("min")
            max_val = spec.get("max")
            if min_val is not None and isinstance(value, (int, float)) and value < min_val:
                raise ConfigurationError(f"option {key!r}: {value!r} below min {min_val}")
            if max_val is not None and isinstance(value, (int, float)) and value > max_val:
                raise ConfigurationError(f"option {key!r}: {value!r} above max {max_val}")
        resolved[key] = value

    for key in options:
        if key not in schema:
            raise ConfigurationError(f"unknown option {key!r}")
    return resolved


def merge_config(base: Mapping[str, Any], override: Mapping[str, Any]) -> Dict[str, Any]:
    """Shallow-merge two config mappings; nested dicts merge recursively."""
    result: Dict[str, Any] = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_config(result[key], value)
        else:
            result[key] = value
    return result


def env_from_os() -> Dict[str, Any]:
    """Read Capio environment knobs from the OS environment (RFC-009)."""
    import os

    cfg: Dict[str, Any] = {}
    env = os.environ.get("CAPIO_ENV")
    if env:
        cfg["env"] = env
    profile = os.environ.get("CAPIO_PROFILE")
    if profile:
        cfg["profile"] = profile
    strict = os.environ.get("CAPIO_STRICT")
    if strict and strict.lower() in ("1", "true", "yes"):
        cfg["strict"] = True
    return cfg
