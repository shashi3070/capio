"""capio: composable capabilities for Python.

The public surface (RFC-003 §2.1): ``use``, ``Capability``, ``with_capabilities``,
plus introspection helpers ``unwrap`` and ``pipeline``.
"""

from __future__ import annotations

import capio.capabilities as _capabilities  # noqa: F401  (registers built-in capabilities)

from .runtime import CapioRuntime, __version__, default_runtime
from .sdk import Capability
from .use import (
    CapabilityInfo,
    CapioMeta,
    pipeline,
    unwrap,
    use,
    with_capabilities,
)

__all__ = [
    "Capability",
    "CapabilityInfo",
    "CapioMeta",
    "CapioRuntime",
    "default_runtime",
    "pipeline",
    "unwrap",
    "use",
    "with_capabilities",
    "__version__",
]
