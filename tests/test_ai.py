"""AI capability tests (RFC-030): llm, caches, memory, rag, ingest, tool, agent, guardrails."""

from __future__ import annotations

import asyncio

import pytest

from capio import use
from capio.exceptions import GuardrailError, TokenBudgetExceededError
from capio.runtime import default_runtime


def test_llm_provider_mode() -> None:
    calls = []

    def provider(request):
        calls.append(request)
        return "hello from provider"

    @use.llm(provider=provider)
    def build_request(messages, model="m"):
        return {"messages": messages, "model": model}

    assert build_request(messages=[{"role": "user", "content": "hi"}]) == "hello from provider"
    assert calls[0]["model"] == "m"


def test_llm_provider_error_falls_back() -> None:
    def bad_provider(request):
        raise RuntimeError("network down")

    @use.llm(provider=bad_provider, fallback="fallback-response")
    def build_request():
        return {"messages": []}

    assert build_request() == "fallback-response"


def test_llm_applies_model_default() -> None:
    seen = {}

    @use.llm(model="gpt-test")
    @use.context()
    def call_model(model, ctx):
        seen["model"] = model
        return f"response for {model}"

    assert call_model() == "response for gpt-test"
    assert seen["model"] == "gpt-test"


def test_llm_cache_hits_on_same_request() -> None:
    state = {"n": 0}

    @use.llm_cache(ttl="1m")
    @use.llm()
    def call_model(messages):
        state["n"] += 1
        return f"resp:{messages[0]['content']}"

    assert call_model(messages=[{"content": "hi"}]) == "resp:hi"
    assert call_model(messages=[{"content": "hi"}]) == "resp:hi"
    assert state["n"] == 1
    assert call_model(messages=[{"content": "bye"}]) == "resp:bye"
    assert state["n"] == 2


def test_semantic_cache_hits_on_identical_query() -> None:
    state = {"n": 0}

    def embedder(text):
        return [float(ord(c)) for c in text]

    @use.semantic_cache(embedder=embedder, threshold=0.9)
    @use.llm()
    def answer(query):
        state["n"] += 1
        return f"answer to {query}"

    assert answer(query="what is capio") == "answer to what is capio"
    assert answer(query="what is capio") == "answer to what is capio"
    assert state["n"] == 1


def test_prompt_cache_marks_blocks() -> None:
    seen = {}

    @use.prompt_cache()
    @use.context()
    def call_model(messages, ctx):
        last = messages[-1]
        seen["marker"] = last.get("cache_control")
        return "resp"

    call_model(messages=[{"role": "user", "content": "hello"}])
    assert seen["marker"] == {"type": "ephemeral"}


def test_memory_retrieves_and_stores() -> None:
    runtime = default_runtime()
    store = runtime.services.get("store.memory")
    store.clear("memory")
    seen = {}

    @use.memory(top_k=5)
    @use.context()
    def chat(input, memories, ctx):
        seen["memories"] = memories
        return f"reply to {input}"

    assert chat(input="hello") == "reply to hello"
    assert chat(input="how are you") == "reply to how are you"
    chat(input="again")
    assert len(seen["memories"]) == 2


def test_rag_injects_context() -> None:
    runtime = default_runtime()
    store = runtime.services.get("store.memory")
    store.put("rag", "doc-1", {"text": "capio is composable"})
    seen = {}

    @use.rag(top_k=4)
    @use.context()
    def answer(query, context, ctx):
        seen["context"] = context
        return "answered"

    answer(query="what is capio")
    assert any(doc.get("text") == "capio is composable" for doc in seen["context"])


def test_ingest_chunks_and_stores() -> None:
    runtime = default_runtime()
    store = runtime.services.get("store.memory")
    store.clear("rag")

    @use.ingest(chunk_size=8, overlap=0)
    def load_documents():
        return ["abcdefghijklmnopqrstuvwxyz"]

    result = load_documents()
    assert result["stored"] >= 4
    assert store.sequence("rag") >= 4


def test_tool_registers_schema() -> None:
    seen = {}

    @use.tool(name="multiply", description="multiply two ints")
    @use.context()
    def multiply(a: int, b: int, ctx) -> int:
        seen["schema"] = ctx.capability("tool")["state"]["schema"]
        return a * b

    assert multiply(3, 4) == 12
    assert seen["schema"]["properties"]["a"]["type"] == "integer"


def test_agent_loops_until_final() -> None:
    tool_calls = {"n": 0}

    def get_weather(city):
        tool_calls["n"] += 1
        return f"sunny in {city}"

    @use.agent(tools={"get_weather": get_weather}, max_steps=3)
    def model_step(messages):
        if len(messages) == 1:
            return {
                "content": None,
                "tool_calls": [{"name": "get_weather", "arguments": {"city": "paris"}}],
            }
        return {"content": "It is sunny in paris"}

    result = model_step(messages=[{"role": "user", "content": "weather?"}])
    assert tool_calls["n"] == 1
    assert result["steps"] == 2
    assert result["response"]["content"] == "It is sunny in paris"


def test_guardrails_reject_bad_input() -> None:
    def clean_input(text, ctx):
        return "bad" not in text

    @use.guardrails(input=clean_input)
    def respond(query):
        return "ok"

    assert respond(query="good question") == "ok"
    with pytest.raises(GuardrailError):
        respond(query="bad question")


def test_guardrails_reject_bad_output() -> None:
    def safe_output(text, ctx):
        return "secret" not in text

    @use.guardrails(output=safe_output)
    def leak():
        return "the secret is out"

    with pytest.raises(GuardrailError):
        leak()


def test_token_budget_raises_when_exceeded() -> None:
    @use.token_budget(budget=3)
    def respond(input):
        return "ok"

    assert respond(input="hello world") == "ok"
    with pytest.raises(TokenBudgetExceededError):
        respond(input="hello there world")


def test_model_router_injects_model() -> None:
    seen = {}

    def is_premium(ctx):
        return ctx.kwargs.get("tier") == "premium"

    @use.model_router(
        routes=[{"when": is_premium, "model": "premium-model"}],
        fallback="basic-model",
    )
    @use.context()
    def call_model(model, tier, ctx):
        seen["model"] = model
        return "ok"

    call_model(tier="premium")
    assert seen["model"] == "premium-model"
    call_model(tier="free")
    assert seen["model"] == "basic-model"


def test_chained_memory_rag_llm_runs_each_once() -> None:
    """Stacking several chained AI capabilities over a context leaf must not
    re-run the pipeline (RFC-003 §5.4): the __capio_leaf__ flag must not leak up
    through functools.wraps into every wrapper above it."""
    runtime = default_runtime()
    store = runtime.services.get("store.memory")
    store.clear("docs")
    calls = {"provider": 0, "rag": 0, "memory": 0}

    def provider(request):
        calls["provider"] += 1
        return {"answer": "ok"}

    @use.memory(top_k=5, namespace="chat")
    @use.rag(top_k=2, namespace="docs")
    @use.llm(provider=provider)
    @use.context()
    def chat(message, memories, context, ctx):
        return {"messages": [{"role": "user", "content": message}]}

    assert chat(message="hi") == {"answer": "ok"}
    assert calls["provider"] == 1


def test_chained_async_context_runs_once() -> None:
    calls = {"provider": 0, "leaf": 0}

    def provider(request):
        assert isinstance(request, dict)
        assert request["messages"][0]["content"] == "hi"
        calls["provider"] += 1
        return {"answer": "ok"}

    @use.llm(provider=provider)
    @use.context()
    async def chat(message, ctx):
        calls["leaf"] += 1
        return {"messages": [{"role": "user", "content": message}]}

    result = asyncio.run(chat(message="hi"))
    assert result == {"answer": "ok"}
    assert calls["provider"] == 1
    assert calls["leaf"] == 1
