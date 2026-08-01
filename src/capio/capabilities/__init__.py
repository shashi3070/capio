"""Built-in capability registration (RFC-031 §2). Importing this module registers
all 37 built-in capabilities into the global registry.
"""

from __future__ import annotations

from ..registry import registry
from .agent import Agent
from .audit import Audit
from .auth import Auth
from .cache import Cache
from .circuit_breaker import CircuitBreaker
from .compensate import Compensate
from .consume import Consume
from .cron import Cron
from .debounce import Debounce
from .dedup import Dedup
from .encrypt import Encrypt
from .guardrails import Guardrails
from .idempotent import Idempotent
from .ingest import Ingest
from .llm import LLM
from .llm_cache import LLMCache
from .log import Log
from .mask import Mask
from .memory import Memory
from .metrics import Metrics
from .model_router import ModelRouter
from .prompt_cache import PromptCache
from .publish import Publish
from .queue import Queue
from .rag import Rag
from .rate_limit import RateLimit
from .retry import Retry
from .semantic_cache import SemanticCache
from .serialize import Serialize
from .throttle import Throttle
from .timeout import Timeout
from .token_budget import TokenBudget
from .tool import Tool
from .trace import Trace
from .transaction import Transaction
from .validate import Validate
from .workflow import Workflow

__all__ = [
    "Agent",
    "Audit",
    "Auth",
    "Cache",
    "CircuitBreaker",
    "Compensate",
    "Consume",
    "Cron",
    "Debounce",
    "Dedup",
    "Encrypt",
    "Guardrails",
    "Idempotent",
    "Ingest",
    "LLM",
    "LLMCache",
    "Log",
    "Mask",
    "Memory",
    "Metrics",
    "ModelRouter",
    "PromptCache",
    "Publish",
    "Queue",
    "Rag",
    "RateLimit",
    "Retry",
    "SemanticCache",
    "Serialize",
    "Throttle",
    "Timeout",
    "TokenBudget",
    "Tool",
    "Trace",
    "Transaction",
    "Validate",
    "Workflow",
]

for _capability in __all__:
    registry.register(globals()[_capability])
