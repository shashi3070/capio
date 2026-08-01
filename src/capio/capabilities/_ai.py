"""Shared helpers for AI capabilities (RFC-030)."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, List, Sequence

_TOKEN_RE = re.compile(r"\w+|[^\w\s]")


def as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [value]
    return list(value)


def messages(ctx: Any) -> List[Any]:
    """Extract the message list from the invocation kwargs/args."""
    if isinstance(ctx.kwargs.get("messages"), list):
        return ctx.kwargs["messages"]
    if isinstance(ctx.kwargs.get("request"), dict) and isinstance(
        ctx.kwargs["request"].get("messages"), list
    ):
        return ctx.kwargs["request"]["messages"]
    if ctx.args and isinstance(ctx.args[0], dict) and isinstance(ctx.args[0].get("messages"), list):
        return ctx.args[0]["messages"]
    return []


def query_text(ctx: Any) -> str:
    """Best-effort extraction of the query/prompt text."""
    if isinstance(ctx.kwargs.get("query"), str):
        return ctx.kwargs["query"]
    if isinstance(ctx.kwargs.get("input"), str):
        return ctx.kwargs["input"]
    if isinstance(ctx.kwargs.get("prompt"), str):
        return ctx.kwargs["prompt"]
    msgs = messages(ctx)
    if msgs:
        last = msgs[-1]
        if isinstance(last, dict):
            return str(last.get("content", ""))
        return str(last)
    return ""


def result_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        return str(response.get("text", response.get("content", "")))
    return str(response)


def request_signature(ctx: Any) -> str:
    blob = f"{ctx.fn_module}.{ctx.fn_name}:{ctx.args!r}:{sorted(ctx.kwargs.items())!r}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def sha(value: Any) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def count_tokens(text: str) -> int:
    return len(_TOKEN_RE.findall(text or "")) + 1
