# RFC-030: AI Capabilities — LLM, Agents, RAG, MCP

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies Capio's **AI capability suite**: everything needed to put production-grade
behavior around LLM calls, agents, retrieval, embeddings, and the Model Context Protocol (MCP).
It follows the platform's core rule — every AI concern is a capability that composes with the
rest (retry, cache, auth, rate limit, circuit breaker, audit, observability). A team that already
composes `@use.retry() @use.cache()` applies the same mental model to `@use.llm() @use.memory()`
`@use.agent()`.

This RFC defines: model integration, LLM caching, prompt management, model routing, guardrails,
tool registry & function calling, agents & orchestration, memory & RAG, embeddings & vector
stores, MCP client/server, AI observability, cost control, and AI evaluation. It is a platform
spec: individual integrations (`capio-openai`, `capio-llamaindex`, `capio-langchain`,
`capio-mcp`, ...) implement it as plugins (RFC-013).

## 2. Model integration layer

### 2.1 A model is a backend

LLM providers are **backends** of a new kind — `model` — so switching `openai` ↔ `anthropic` ↔
`ollama` is a configuration change, not a code change (RFC-015 §4):

```yaml
model:
  backend: openai            # or anthropic | google | ollama | local | vllm | custom
  default: "gpt-4o"
  temperature: 0.2
  max_tokens: 2048
  timeout: "60s"
```

```python
class ModelBackend(Backend):
    kind = "model"
    def complete(self, messages, *, model=None, temperature=None, max_tokens=None,
                 tools=None, stream=None, **opts) -> Completion: ...
```

- `Completion` carries `text`/`message`, `tool_calls`, `usage` (prompt/completion/total tokens),
  `model`, `raw`, and `finish_reason` — normalized across providers (provider adapters
  translate provider-specific fields).
- `stream=True` returns an async/sync iterator of deltas (RFC-012 §4 streaming contract applies).
- `capio-openai`, `capio-anthropic`, `capio-google`, `capio-ollama`, `capio-vllm`,
  `capio-hf` publish `model` backends.
- **Providers are untrusted network peers**: completions are validated (RFC-022 §2), masked
  (RFC-022 §5), and treated as input to tool calls (RFC-026 §8).

### 2.2 The `@use.llm()` decorator

```python
@use.llm(                       # the canonical AI call decorator
    model="gpt-4o",             # model override; default from config
    system="You are a support agent.",      # or (ctx) -> str
    max_tokens=1024,
    temperature=0.2,
    cache=True,                 # llm_cache (RFC-030 §3)
    memory=False,               # conversational memory (RFC-030 §4)
    rag=False,                  # retrieval augmentation (RFC-030 §4)
    tools=None,                 # tool registry subset (RFC-030 §5)
    guardrails="default",       # guardrail policy (RFC-030 §6)
    timeout="60s",
    retry=True,                 # uses retry with LLM presets (RFC-017 §7)
)
def answer(question: str) -> str:
    """The prompt template lives in the docstring by default; override via template=."""
```

- The decorated function's docstring (or `template=`) is the **prompt template**; `{arg}` /
  `{ctx.field}` placeholders are rendered by the prompt engine (§2.3).
- `@use.llm()` composes the full AI pipeline internally: guardrails → memory/rag → llm_cache →
  retry → timeout → model backend → response validation → output guardrails →
  observability/audit. It is sugar over composing the individual capabilities below; the
  composed pipeline is fully inspectable (`capio graph`, RFC-028).

### 2.3 Prompt engine & template management

- Templates are plain strings or files (`templates/answer.j2`), rendered with a safe
  (non-executing) template renderer — no arbitrary Python in templates (RFC-026).
- **Prompt versioning**: every rendered prompt records `prompt.id`, `prompt.version`, and the
  template hash on the span/audit record (RFC-019 §4, RFC-020 §4). `capio inspect` shows the
  exact template+version used.
- **Prompt validation** (RFC-022 §2): `@use.validate(schema=PromptSchema)` on the rendered
  prompt; a `prompt_validator` checks length, required sections, PII policy, and allowed-topic
  rules before the model call.

## 3. LLM caching

### 3.1 `llm_cache` — exact-match caching

```python
@use.llm_cache(
    ttl="1h",
    key="llm",                   # canonical prompt+model+params (RFC-016 key contract)
    include_model=True,          # different model → different key
    include_params={"temperature", "max_tokens"},  # which params affect the key
    max_entries=10000,
    backend="memory",            # any cache backend (RFC-016)
)
def answer(question: str) -> str: ...
```

- **Key**: sha256 of `(model, normalized params subset, rendered prompt, tool schemas)`.
  Prompts containing dynamic/PII data are excluded from key building (RFC-016 §3.1).
- Uses the cache capability's machinery (RFC-016): TTL, tags, stampede protection, compression,
  encryption, backend switching.
- **Exact-match only**: for semantic similarity caching see §3.2.

### 3.2 `semantic_cache` — semantic similarity caching

```python
@use.semantic_cache(
    ttl="1h",
    threshold=0.85,              # cosine similarity hit threshold
    embedding_model="text-embedding-3-small",
    top_k=5,
    store="vector",              # vector store backend (RFC-030 §4.3)
    backend="memory",            # result/value store (RFC-016)
)
def answer(question: str) -> str: ...
```

- On miss-like flow: embed the query, `top_k` search the vector store, and if the best match
  similarity ≥ `threshold`, return the stored response (with `ctx.semantic_cache="hit"`).
- **Accuracy guard**: semantic hits are best-effort; `min_confidence` and per-domain thresholds
  are configurable; a wrong hit degrades to a miss when the caller's validation (RFC-022 §2)
  rejects the response.
- **Poisoning defense**: cached semantic entries are immutable snapshots keyed by the original
  prompt hash; an entry can only be added by an actual model response (never by user input).
  Tenant-isolated (RFC-021 §6, RFC-026 §8).

### 3.3 Prompt caching (provider-native)

Exposes provider prompt-caching (e.g. Anthropic/OpenAI cached prefixes) as a capability:
`@use.prompt_cache(max_cache_blocks=...)` records cache-control markers and emits
`llm.prompt_cache.{hit,miss}` metrics.

## 4. Memory & RAG

### 4.1 Conversational memory

```python
@use.memory(
    store="redis",               # memory backend (RFC-015 cache/db)
    window=10,                   # last N turns in context
    summarizer=True,             # compress long history via model
    ttl="24h",                   # per-conversation expiry
    key="auto",                  # conversation id from ctx (e.g. user session)
    embeddings=False,            # enable semantic recall (§4.3)
)
```

- Stores conversation turns keyed by conversation id; injects the memory window into the prompt
  as a system/context block.
- `summarizer=True` runs a small model to compress history beyond `window` (bounded cost).
- Memory content is tenant-scoped and privacy-redacted before injection/logging (RFC-026 §8).

### 4.2 RAG (retrieval-augmented generation)

```python
@use.rag(
    store="vector",              # vector store backend
    embed="text-embedding-3-small",
    top_k=4,
    min_score=0.6,
    chunker="markdown",          # text splitting strategy
    rerank="cross_encoder",      # optional reranker
    prefix="docs",               # collection/namespace
)
def answer(question: str) -> str: ...
```

- On each invocation: embed query → retrieve `top_k` chunks → (optional) rerank → inject as
  context block with citations → the model answers grounded in retrieved text.
- **Grounding & citations**: retrieved chunks carry `source`/`page` metadata injected into the
  prompt and returned to the caller in `ctx.rag.citations` so answers are auditable (RFC-020 §4).
- Chunking/indexing is a pipeline-side job: `@use.ingest(docs=..., chunker=..., store=...)`
  decorates an ingestion callable and handles embedding + upsert (batch, retry, dedup).
- RAG + semantic cache: retrieval results are part of the semantic cache key material.

### 4.3 Vector store & embedding backends

New backend kinds:

```python
class EmbeddingBackend(Backend):
    kind = "embedding"
    def embed(self, texts: list[str]) -> list[Vector]: ...

class VectorStoreBackend(Backend):
    kind = "vector"
    def upsert(self, id, vector, metadata) -> None: ...
    def search(self, vector, *, top_k, filter=None) -> list[VectorHit]: ...
    def delete(self, id) -> None: ...
    def namespace(self, name) -> VectorStoreBackend: ...   # tenant isolation
```

Canonical backends: `vector.pgvector` (Postgres), `vector.qdrant`, `vector.pinecone`,
`vector.chroma`, `vector.weaviate`, `vector.redis`, `vector.faiss`, `vector.sqlite_vec`;
`embedding.openai`, `embedding.hf`, `embedding.ollama`, `embedding.custom`. All pass the backend
contract tests (RFC-029 §5) including tenant-isolation tests (RFC-026 §8).

### 4.4 Long-term / agent memory

`@use.memory(kind="agent")` provides an agent's episodic memory: tool-call history, decisions,
and outcomes stored/retrieved (semantic + recency) and replayed as few-shot context. Bounded,
tenant-scoped, and auditable (which memories were injected — RFC-020 §4).

## 5. Tools, function calling, and agents

### 5.1 Tool registry

A **tool** is a Capio-decorated callable registered in the tool registry (RFC-014):

```python
@use.tool(                       # registers fn as a tool the model may call
    name="search_orders",
    description="Search customer orders by email",
    parameters=OrderSearchSchema,        # JSON schema for arguments
    permissions=["db_read"],             # declared capability permissions (RFC-026 §4)
    allow_for=["support_agent"],         # role/policy gate (RFC-021)
    audit=True,                          # every invocation audited (RFC-020)
    max_parallel=None,                   # parallel-call cap
)
def search_orders(email: str, limit: int = 10) -> list[Order]: ...
```

- Tool registry entries expose: name, description, JSON-schema parameters, and the wrapped
  callable (its own pipeline: auth, rate limit, retry, audit).
- **Tool identity flows to the model** as function/tool schema (provider-adapted).
- Tools are the *only* way a model can cause side effects. Every tool call goes through the
  full pipeline and the permission/role gate (RFC-026 §8) — an agent can never call a tool with
  rights beyond its authenticated principal (RFC-021 §4.3).

### 5.2 Function calling

`@use.llm(tools=[...])`:

1. Renders tool schemas from the registry into the model request.
2. Receives `tool_calls` in the completion.
3. Executes the tools through their pipelines, with **per-call timeout, concurrency cap, and
   result size bounds**.
4. Feeds tool results back for the next model turn (auto tool-loop, §5.3).

Invalid tool args (schema violations from the model) are caught by validation (RFC-022 §2) and
returned to the model as an error message — never executed.

### 5.3 Agent orchestration

```python
@use.agent(
    role="research",             # system prompt role
    tools=["search_orders", "web_search", "calculator"],
    model="gpt-4o",
    max_steps=12,                # hard bound on tool loop
    max_tool_parallel=3,         # concurrent tool calls per step
    token_budget=100000,         # total token budget (RFC-030 §8)
    memory=True,                 # agent memory (§4.4)
    reflect=True,                # optional self-critique step
    plan=True,                   # plan-then-execute
    subagents={"legal": ...},    # nested agents (§5.5)
    human_in_the_loop="on_tool", # "none" | "on_tool" | "on_sensitive" | "always" (§6.4)
)
def research_topic(topic: str) -> str: ...
```

- The agent loop is a **workflow** (RFC-023 §5): steps are model calls and tool calls, control
  flow decided by the model, bounded by `max_steps`, `token_budget`, and `deadline`.
- Durable by default (`durable=True` checkpointing, RFC-023 §5.2): a crashed agent resumes from
  the last checkpoint.
- Every step is traced (`agent.step` spans, tool spans — RFC-019 §4) and audited (§9).
- The agent acts as the caller's principal; all tool-call permission checks inherit it
  (RFC-021 §4.3).

### 5.4 Agent failure & compensation

- A tool call that fails is returned to the model as an error result (bounded retries).
- `on_tool_failure="retry_model" | "compensate" | "stop"`: `compensate` runs the tool's undo
  (RFC-023 §7) for completed side-effecting tools before stopping.
- Tool rollback: side-effecting tools may register `undo` (RFC-023 §7) so a failed agent run can
  reverse partially-applied effects.

### 5.5 Subagents & multi-agent

`subagents` registers nested agents as callables; a parent agent can delegate subtasks to a
subagent via a tool. Subagents inherit the parent's context/correlation (RFC-006), tenant, and
principal; their token/step budgets are shared from the parent's budget. Depth and fan-out are
bounded (config `max_subagent_depth=2`).

## 6. Guardrails & safety

### 6.1 Guardrail model

Guardrails are **input and output scanners** that gate a model call:

```python
@use.guardrails(
    input=["prompt_injection", "pii_policy", "topic_blocklist"],
    output=["toxicity", "hallucination_check", "format_check"],
    on_violation="block",        # "block" | "flag" | "transform" | "review"
    block_message="Request blocked by policy.",
)
def answer(question: str) -> str: ...
```

- **Input guardrails** run BEFORE the model (after auth/validation, before cache — a blocked
  prompt never hits the cache or the provider).
- **Output guardrails** run AFTER the model response, before the caller sees it.
- `on_violation`:
  - `block` → raise `GuardrailBlockedError` (RFC-025) + `guardrail.blocked` event + audit.
  - `flag` → allow but record `guardrail.flagged` (metrics + audit).
  - `transform` → rewrite via a configured transformer (e.g. PII redaction, RFC-022 §5).
  - `review` → pause the invocation for human approval (§6.4).

### 6.2 Guardrail catalog

| Guardrail | Checks | Example sources |
| --------- | ------ | --------------- |
| `prompt_injection` | prompt-injection / jailbreak attempts | LLM-as-judge, heuristics, allowlists |
| `pii_policy` | PII/secret leakage into prompts | detectors + masking (RFC-022 §5) |
| `topic_blocklist` | disallowed topics | classifier / embeddings |
| `toxicity` | toxic/abusive output | moderation API (capio-openai moderation) |
| `hallucination_check` | grounding against RAG context | citation coverage, self-check |
| `format_check` | structured-output validity | JSON-schema validator (RFC-022 §2) |
| `cost_check` | per-call budget exceeded | §8 |
| `data_exfiltration` | secrets/keys in output | regex + masking detectors |

Guardrails are registered validators (RFC-014); a `ValidationBackend` (RFC-015) may host remote
guardrail models. All guardrail verdicts are audited (RFC-020 §4) — safety decisions are
compliance data.

### 6.3 Defense in depth

Guardrails sit INSIDE auth/validation and OUTSIDE model/cache: `auth → validate → guardrails
(input) → memory/rag → cache → model → guardrails (output) → validate → caller` (RFC-005 §4
priority additions: guardrails ≈ 880). A blocked input never reaches the provider.

### 6.4 Human-in-the-loop

`on_violation="review"` and agent `human_in_the_loop` use the workflow approval mechanism
(RFC-023 §5.2): the invocation pauses, an `approval` step surfaces (tool call, dashboard,
email), and resumes on the review decision (approve/deny/modify). Review actions are audited.

## 7. MCP (Model Context Protocol)

MCP is how Capio connects to (and exposes) external AI tools/resources. Capio treats MCP as
**transports and tool sources**, not a separate programming model — MCP tools are Capio tools,
MCP resources are Capio data, and context propagation flows over the MCP carrier.

### 7.1 `capio-mcp` client — using remote tools

```python
from capio import mcp

client = mcp.connect("https://mcp.example.com/sse")      # or stdio/streamable-http

@use.llm(tools=mcp.client_tools("mcp-weather"), ...)      # register remote tools as Capio tools
def forecast(city: str) -> str: ...
```

- Remote MCP tools are **proxied into the tool registry** with their own Capio pipelines
  (auth, rate limit, retry, timeout, audit, guardrails). Each remote tool call is a normal
  invocation (RFC-026 §9).
- Tool-call permissioning applies to remote tools identically (RFC-026 §8) — an MCP server
  cannot escalate rights.
- Server tools are cached/refreshed (`tools/list` with a TTL, `tool.changed` notification
  handling via the Event Bus, RFC-008).
- Streaming (SSE/streamable HTTP), auth (OAuth2 for servers that require it), and connection
  lifecycle (reconnect with backoff — reuse retry/circuit-breaker capabilities) are built in.

### 7.2 `capio-mcp` server — exposing Capio as MCP tools

```python
from capio import mcp
from capio import use

@use.auth(...)                    # inbound tool calls are fully gated
@use.validate(...)
@use.audit(...)
def create_ticket(summary: str, priority: str = "low") -> dict: ...
```

- `capio server` (RFC-028 §3) or `mcp.serve([create_ticket, ...])` exposes decorated callables
  as MCP tools, resources, and prompts.
- **Inbound MCP tool calls run the full pipeline** (auth, validate, rate limit, guardrails,
  audit) exactly like in-process calls — attacker-controlled input is handled by the same rules
  (RFC-026 §9).
- Context propagation: the MCP carrier carries `traceparent` + `capio-*` fields (RFC-006 §5.1)
  so server-side spans join the client's trace.

### 7.3 MCP resources & prompts

- A Capio callable can expose MCP **resources** (`resource://orders/{id}`) via
  `mcp.resource(uri, fn)`; access is permissioned and audited.
- MCP **prompts** (`@mcp.prompt(...)`) expose reusable prompt templates to MCP clients.

### 7.4 Agent ↔ MCP

An agent (RFC-030 §5.3) can use MCP client tools as its tools — Capio's agent loop and MCP are
composable, giving agents a uniform tool interface across in-process and remote tools.

## 8. Cost control & model routing

### 8.1 Token & cost budgeting

```python
@use.token_budget(
    per_call=8192,
    per_invocation=100000,       # agent run budget (§5.3)
    per_period={"1h": 1000000},  # per-key rolling budget (rate-limit machinery, RFC-018 §4)
    key="auto",                  # per user/tenant from ctx
    on_exceeded="raise",         # "raise" | "wait" | "degrade_model"
    degrade_to="gpt-4o-mini",
)
```

- Uses rate-limit backend machinery (RFC-018 §4) with token units.
- Costs derive from token usage × model pricing table (config or pricing plugin); emitted as
  `gen_ai.cost_usd` metrics (RFC-019 §4).

### 8.2 Model routing

```python
@use.model_router(
    rules=[
        ("is_retry", "gpt-4o-mini"),            # cheap on retries
        ("lang == 'fr'", "gpt-4o"),             # capability-based
        ("topics contains 'legal'", "claude-sonnet"),
        ("*", "gpt-4o"),
    ],
    provider_fallback=["openai", "anthropic"],  # provider outage fallback
    latency_aware=True,                          # route on recent p95
)
```

- Rules are predicates over `(ctx, prompt)` — deterministic and auditable (`route.reason`
  recorded on the span/audit).
- `provider_fallback` pairs with retry (RFC-017 §7): a provider outage routes to the next
  provider with backoff.

## 9. AI observability, audit & privacy

- **Spans** follow OpenTelemetry GenAI conventions (RFC-019 §4): `gen_ai.system`,
  `gen_ai.request.model`, usage tokens, tool names/inputs/outputs, agent step spans.
- **Audit** (RFC-020 §4): AI records carry model, prompt (hash or full per policy), response,
  tool calls, tokens, cost, guardrail verdicts, eval.id, and the acting principal — queryable
  via `capio audit` (RFC-028).
- **Privacy**: prompts/responses are masked at emission boundaries by default (RFC-022 §5.3,
  RFC-026 §6); raw prompt capture is opt-in with schema-based redaction.
- Backends: standard trace/metrics backends plus purpose-built LLM observability backends
  (Langfuse, LangSmith, Helicone, Phoenix, Arize, W&B) consuming the same spans (RFC-019 §4).

## 10. AI evaluation

The eval harness (RFC-029 §7) covers: prompt-level accuracy (reference answers), latency, cost,
guardrail effectiveness (adversarial suite), RAG grounding quality, and agent task completion —
with golden thresholds in CI and production `eval.id` linkage.

## 11. Capability & plugin catalogue (AI)

| Plugin | Contributes |
| ------ | ----------- |
| `capio-openai` | `model.openai`, `embedding.openai`, moderation guardrail |
| `capio-anthropic` / `capio-google` / `capio-ollama` / `capio-vllm` | model + embedding backends |
| `capio-langchain` / `capio-llamaindex` | reuse existing orchestrators as backends |
| `capio-mcp` | MCP client + server (§7) |
| `capio-pgvector` / `capio-qdrant` / `capio-chroma` | vector backends (§4.3) |
| `capio-langfuse` / `capio-langsmith` / `capio-phoenix` | LLM observability backends (§9) |
| `capio-llmguard` | guardrail validators (§6) |

All are built on RFC-012/013/015 contracts and pass contract tests (RFC-029).

## 12. AI capability pipeline priority additions

Extends RFC-005 §4.2 default priority table:

| Priority | Capability |
| -------- | ---------- |
| 1000 | auth |
| 920 | validate (input) |
| 890 | guardrails (input) |
| 860 | token_budget / model_router |
| 830 | memory / rag |
| 780 | semantic_cache |
| 760 | llm_cache |
| 700 | retry (LLM presets) |
| 650 | timeout |
| 600 | trace (GenAI spans) |
| 550 | metrics (cost/usage) |
| 0 | model call / agent loop |

## 13. Document Dependencies

- Platform core: RFC-001–005; context: RFC-006; config: RFC-009; backends: RFC-015; cache:
  RFC-016; retry: RFC-017; guards: RFC-018; observability: RFC-019; audit: RFC-020; auth:
  RFC-021; data-plane: RFC-022; workflows: RFC-023; async: RFC-024; errors: RFC-025; security:
  RFC-026; tests: RFC-029; CLI: RFC-028.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft; expanded to cover LLM, agents, RAG, MCP. |
