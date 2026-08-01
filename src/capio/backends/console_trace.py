"""Console trace backend: writes span records as JSON lines (RFC-019 §2.4)."""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, Optional, TextIO


class ConsoleTraceBackend:
    """Best-effort span sink that prints one JSON line per span to a stream."""

    def __init__(self, stream: Optional[TextIO] = None, enabled: bool = True) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self.enabled = enabled

    def emit(self, span: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            self._stream.write("[capio.trace] " + json.dumps(span, default=str) + "\n")
            self._stream.flush()
        except Exception:  # noqa: BLE001 - trace export must never break the invocation
            pass
