"""Stdio log backend: structured records through the stdlib logging module (RFC-020 §2.3)."""

from __future__ import annotations

import logging
from typing import Any, Mapping


class StdioLogBackend:
    """A thin facade over ``logging`` emitting a canonical structured line."""

    def __init__(self, logger_name: str = "capio", level: int = logging.INFO) -> None:
        self._logger = logging.getLogger(logger_name)
        self._logger.setLevel(level)

    def log(self, level: int, message: str, **fields: Any) -> None:
        try:
            self._logger.log(level, "%s %s", message, _format_fields(fields))
        except Exception:  # noqa: BLE001 - log backend failure is never raised
            pass


def _format_fields(fields: Mapping[str, Any]) -> str:
    if not fields:
        return ""
    parts = []
    for key, value in fields.items():
        parts.append(f"{key}={value!r}")
    return " ".join(parts)
