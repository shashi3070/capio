"""Base capability registration (RFC-031 §2). Importing this module registers the
built-in capabilities into the global registry.
"""

from __future__ import annotations

from ..registry import registry
from .cache import Cache
from .circuit_breaker import CircuitBreaker
from .log import Log
from .metrics import Metrics
from .rate_limit import RateLimit
from .retry import Retry
from .timeout import Timeout
from .trace import Trace

__all__ = [
    "Cache",
    "CircuitBreaker",
    "Log",
    "Metrics",
    "RateLimit",
    "Retry",
    "Timeout",
    "Trace",
]

for _capability in __all__:
    registry.register(globals()[_capability])
