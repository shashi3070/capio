# Capio Capability Cookbook

A hands-on reference: what each of the 37 built-in capabilities does and the
smallest realistic way to use it. For the full option-by-option reference, see
[`usage.md` §4](usage.md#4-capability-reference-every-option). For wiring
external backends (Redis, Postgres, OTLP, JWT, ...), see
[`integrations/README.md`](integrations/README.md).

Every example below is the **chained form** (`use.<name>(...)`). The decorator
written highest runs outermost. Swap it for the **composite form** whenever you
want priorities to decide the order instead of physical order:

```python
@use(
    retry={"max_attempts": 3},
    cache={"ttl": "5m"},
    timeout={"seconds": 2},
    trace=True,
)
def search(query: str) -> list[str]:
    ...
```

Inject the current `Context` into a callable with `use.context()`; it is needed
whenever a capability feeds the callable (auth identity, memory, RAG context,
serialize fields, ...). Capabilities that store state read their backend from
the runtime (see §5 of `usage.md`).

---

## 1. Execution guards

### `use.rate_limit` — RFC-018 §4 · [usage §4.5](usage.md#45-userate_limit-rfc-018-4)

Fixed/sliding window or token-bucket admission. Raises `RateLimitExceededError`
(or waits / returns `fallback`) when exceeded.

```python
@use.rate_limit(limit=10, window="1s", strategy="token_bucket",
                refill_rate="10/s", on_exceeded="wait", max_wait="2s")
def tick() -> None:
    ...
```

### `use.throttle` — RFC-018 §5 · [usage §4.9](usage.md#49-usethrottle-rfc-018-5)

Bounds **concurrent in-flight** calls. `strategy="block"` waits for a free slot
(optionally bounded by `timeout`); `strategy="reject"` raises
`ConcurrencyLimitError` immediately.

```python
@use.throttle(limit=4, strategy="block")
def fetch_page(url: str) -> bytes:
    ...
```

### `use.debounce` — RFC-018 §6 · [usage §4.10](usage.md#410-usedebounce-rfc-018-6)

Coalesces rapid calls within `window` into one execution (leading and/or
trailing edge). Coalesced calls return `drop_value`.

```python
@use.debounce(window="200ms", leading=True, trailing=True)
def persist_changes(record: dict) -> None:
    ...
```

### `use.retry` — RFC-017 · [usage §4.1](usage.md#41-useretry-rfc-017)

Retries failures with backoff, optional jitter, `retry_on` / `retry_if` filters,
and a final outcome policy (`"wrap"` | `"reraise_original"`).

```python
@use.retry(max_attempts=5, delay="100ms", backoff="exponential",
           jitter=True, retry_on=(TimeoutError,))
def fetch(url: str) -> bytes:
    ...
```

### `use.circuit_breaker` — RFC-018 §2 · [usage §4.4](usage.md#44-usecircuit_breaker-rfc-018-2)

Opens after `failure_threshold` failures, half-opens after `reset_timeout`, and
rejects fast (`CircuitOpenError`) while open. `only_on` / `exclude` select which
exceptions count as failures.

```python
@use.circuit_breaker(failure_threshold=5, reset_timeout="30s",
                     only_on=(ConnectionError,))
def call_downstream() -> dict:
    ...
```

### `use.timeout` — RFC-018 §3 · [usage §4.3](usage.md#43-usetimeout-rfc-018-3)

Soft deadline (returns control after `seconds`) or hard deadline (`hard=True`,
cancels the work). `raise_on` / `return_on` control what the caller receives.

```python
@use.timeout(seconds=2, hard=True)
def slow_work() -> str:
    ...

@use.timeout(seconds=0.5, raise_on=False, return_on="late")
def best_effort() -> str:
    ...
```

---

## 2. Caching and dedup

### `use.cache` — RFC-016 · [usage §4.2](usage.md#42-usecache-rfc-016)

Caches return values by argument-derived key (customize via `key` / `key_prefix`
/ `tags`). `cache_when` gates what gets cached; `stampede="singleflight"`
coalesces concurrent misses.

```python
@use.cache(ttl="5m", key_prefix="op:")
def get_user(user_id: int) -> dict:
    ...

@use.cache(ttl="1h", cache_when=lambda ctx, result: result is not None)
def lookup(sku: str) -> dict | None:
    ...
```

### `use.dedup` — RFC-022 §6 · [usage §4.16](usage.md#416-usededup-rfc-022-6)

Reuses the stored result for an identical request within `ttl`; concurrent
duplicates wait on the first in-flight execution.

```python
@use.dedup(ttl="5s", key=lambda ctx: f"compute:{ctx.args[0]}")
def compute(x: int) -> int:
    ...
```

### `use.llm_cache` — RFC-030 §3 · [usage §4.26](usage.md#426-usellm_cache-rfc-030-3)

Caches the LLM response for identical model inputs. Stack it directly above
`use.llm`.

```python
@use.llm_cache(ttl="1h")
@use.llm(provider=call_model_api, model="gpt-4o-mini")
def complete(messages: list[dict], model: str) -> dict:
    ...
```

### `use.semantic_cache` — RFC-030 §4 · [usage §4.27](usage.md#427-usesemantic_cache-rfc-030-4)

Embeds the query and returns the cached answer when the nearest neighbor clears
`threshold`. Needs an `embedder(text) -> list[float]`.

```python
def embedder(text: str) -> list[float]:
    return model.encode(text)

@use.semantic_cache(embedder=embedder, threshold=0.92, max_entries=500)
@use.llm(provider=call_model_api)
def answer(query: str) -> str:
    ...
```

### `use.prompt_cache` — RFC-030 §5 · [usage §4.28](usage.md#428-useprompt_cache-rfc-030-5)

Marks the final user message with a cache-control block for prompt caching in
the upstream provider.

```python
@use.prompt_cache()
@use.context()
def complete(messages: list[dict], ctx) -> dict:
    ...
```

---

## 3. Observability

### `use.trace` — RFC-019 §2 · [usage §4.6](usage.md#46-usetrace-rfc-019-2)

Records one span per invocation (trace/span/parent ids, status, duration,
attributes). Best-effort — exporter failures never propagate.

```python
@use.trace(name="search", attributes={"service": "api"}, capture_result=True)
def search(q: str) -> list[str]:
    ...
```

### `use.metrics` — RFC-019 §3 · [usage §4.7](usage.md#47-usemetrics-rfc-019-3)

Emits a call counter and a duration histogram per invocation, tagged by
`outcome`. The default `metrics.null` backend exposes `.records` / `.snapshot()`.

```python
@use.metrics(name="orders.create", tags={"team": "checkout"}, per_instance=True)
def create_order(order_id: int) -> dict:
    ...
```

### `use.log` — RFC-020 §2 · [usage §4.8](usage.md#48-uselog-rfc-020-2)

Structured log line per call: success/error levels, duration, optional args and
result.

```python
@use.log(level="INFO", include_args=True, include_duration=True)
def payment_intent(amount_cents: int) -> str:
    ...
```

### `use.audit` — RFC-020 §4 · [usage §4.11](usage.md#411-useaudit-rfc-020-4)

Appends an immutable record (`actor`, `action`, `resource`, outcome, optional
payload) to the `audit.*` backend. `strict=True` raises if the backend fails.

```python
@use.audit(actor="alice", action="publish", resource="article:42")
def publish_article(article_id: int) -> str:
    ...

# verify the trail afterwards
records = default_runtime().services.get("audit.memory").query(actor="alice")
assert default_runtime().services.get("audit.memory").verify()
```

---

## 4. Data and auth

### `use.auth` — RFC-020 §3 · [usage §4.12](usage.md#412-useauth-rfc-020-3)

Authenticates via `provider(ctx) -> identity | None`, then checks required
`scopes`. The identity lands in `ctx.auth`. Raises `AuthenticationError` /
`AuthorizationError`.

```python
def provider(ctx):
    if ctx.kwargs.get("token") == "secret":
        return {"subject": "alice", "scopes": ["read", "write"]}
    return None

@use.auth(provider=provider, scopes=["read"])
@use.context()
def private(token: str, ctx) -> str:
    return f"hello {ctx.auth['subject']}"
```

### `use.validate` — RFC-022 §3 · [usage §4.13](usage.md#413-usevalidate-rfc-022-3)

Validates inputs and/or output against a JSON-schema-like spec. Raises
`ValidationError` on violation.

```python
@use.validate(input={"age": {"type": "int", "min": 18}})
def signup(name: str, age: int) -> str:
    return "ok"

@use.validate(output={"type": "int"})
def compute() -> int:
    return 7
```

### `use.encrypt` — RFC-022 §4 · [usage §4.14](usage.md#414-useencrypt-rfc-022-4)

Encrypts nominated fields on input and transparently decrypts them on output
(`decrypt_fields`). Deterministic `HMAC-SHA256` stream cipher — no dependency.

```python
@use.encrypt(key="secret-key", fields=["password"], decrypt_fields=["password"])
def login(username: str, password: str) -> dict:
    return {"user": username, "password": password}

login("bob", password="hunter2")["password"] == "hunter2"  # true
```

### `use.mask` — RFC-022 §5 · [usage §4.15](usage.md#415-usemask-rfc-022-5)

Redacts named fields in args and/or result (`mode="both" | "in" | "out"`) before
the body runs and before returning.

```python
@use.mask(fields=["secret"], mode="both")
def handler(secret: str) -> dict:
    return {"secret": secret}  # body sees "******"

handler(secret="abc123")["secret"] == "******"  # true
```

### `use.serialize` — RFC-022 §3 · [usage §4.37](usage.md#437-useserialize-rfc-022-3)

Encodes/decodes nominated fields across the boundary with a registered codec
(`json` safe by default; `pickle` requires `trust=True`). `mode="in"` serializes
the field to wire format before the body runs; `mode="out"` deserializes the
result before it is returned. Raises `SerializationError` on failure.

```python
import json

@use.serialize(fields=["payload"], mode="in")   # encode the field on the way in
@use.context()
def send(payload, ctx) -> str:
    ...   # payload arrives as a JSON string

@use.serialize(mode="out")                      # decode the JSON result
def load() -> str:
    return json.dumps({"data": [1, 2, 3]})      # callers receive the dict
```

---

## 5. Messaging and orchestration

### `use.publish` — RFC-023 §2 · [usage §4.17](usage.md#417-usepublish-rfc-023-2)

Publishes the return value to a broker topic. `outbox` stages it transactionally
if the broker is unavailable; `strict=False` (default) degrades instead of
raising.

```python
@use.publish(topic="orders.created", include_result=True)
def create_order(order_id: int) -> dict:
    return {"id": order_id}
```

### `use.consume` — RFC-023 §3 · [usage §4.18](usage.md#418-useconsume-rfc-023-3)

Pops the next message for a topic and passes it to the callable; returns
`skip_value` when the topic is empty.

```python
@use.consume(topic="orders.created")
def handle(message: dict) -> str:
    print("processing", message["payload"])
    return "processed"
```

### `use.queue` — RFC-023 §4 · [usage §4.19](usage.md#419-usequeue-rfc-023-4)

`mode="enqueue"` pushes the return value as a task; `mode="worker"` pops and
processes the next task from `queue`.

```python
@use.queue(mode="enqueue", queue="emails")
def send_email(to: str) -> dict:
    return {"to": to}

@use.queue(mode="worker", queue="emails")
def process_email(task: dict) -> str:
    return f"sent to {task['args'][0]}"
```

### `use.transaction` — RFC-023 §5 · [usage §4.20](usage.md#420-usetransaction-rfc-023-5)

Runs `actions` in order after the body succeeds; on failure, runs `rollback`s in
reverse. Raises `TransactionError` on commit/rollback failure.

```python
@use.transaction(actions={
    "deduct": {"commit": lambda ctx: ledger.debit(), "rollback": lambda ctx: ledger.revert()},
    "ship": {"commit": lambda ctx: fulfillment.order(), "rollback": lambda ctx: fulfillment.cancel()},
})
def place_order(order_id: int) -> dict:
    ...
```

### `use.workflow` — RFC-023 §6 · [usage §4.21](usage.md#421-useworkflow-rfc-023-6)

Runs a list of steps against a shared `state` dict; `recover(ctx, state, err)`
handles step failures; returns the final state.

```python
def validate(ctx, state: dict) -> None:
    state["ok"] = True

def charge(ctx, state: dict) -> None:
    state["charged"] = state["ok"]

@use.workflow(steps=[validate, charge], recover=recover)
def run() -> dict:
    ...
```

### `use.cron` — RFC-023 §7 · [usage §4.22](usage.md#422-usecron-rfc-023-7)

Runs only when the call is due per a cron expression or `every <duration>`
interval; returns `skip_value` when skipped.

```python
@use.cron(schedule="every 30s")
def heartbeat() -> str:
    return "alive"

@use.cron(schedule="0 3 * * *")
def nightly_backup() -> str:
    ...
```

### `use.compensate` — RFC-023 §8 · [usage §4.23](usage.md#423-usecompensate-rfc-023-8)

Registers compensating actions executed when the body raises (the error still
propagates).

```python
@use.compensate(actions=[lambda ctx, err: rollback_payment(str(err))])
def reserve_payment(amount_cents: int) -> str:
    ...
```

### `use.idempotent` — RFC-023 §9 · [usage §4.24](usage.md#424-useidempotent-rfc-023-9)

Replays the stored result for a repeated `key` within `ttl`; raises
`IdempotencyConflictError` if the same key is reused with a different request.

```python
@use.idempotent(key=lambda ctx: ctx.kwargs.get("request_id"), ttl="24h")
def create_payment(request_id: str, amount: int) -> str:
    ...
```

---

## 6. AI

### `use.llm` — RFC-030 §2 · [usage §4.25](usage.md#425-usellm-rfc-030-2)

Structured provider boundary. `provider(request) -> response` receives the
inner callable's return value; `fallback` handles provider errors; `model` /
`temperature` / `max_tokens` defaults are injected.

```python
def call_model_api(request: dict) -> dict:
    return {"content": "completion"}

@use.llm(provider=call_model_api, model="gpt-4o-mini", fallback="offline")
def complete(messages: list[dict], model: str) -> dict:
    ...
```

### `use.memory` — RFC-030 §6 · [usage §4.29](usage.md#429-usememory-rfc-030-6)

Retrieves relevant conversation memories into the `memories` kwarg and stores
the exchange. Combine with `use.rag` + `use.llm` for a full RAG chat chain.

```python
@use.memory(kind="conversation", top_k=5, namespace="chat")
@use.context()
def chat(input, memories, ctx) -> str:
    return f"reply to {input}"
```

### `use.rag` — RFC-030 §7 · [usage §4.30](usage.md#430-userag-rfc-030-7)

Retrieves top-k documents from a store (populated via `use.ingest`) and injects
them into the `context` kwarg.

```python
@use.rag(top_k=4, namespace="docs")
@use.context()
def answer(query, context, ctx) -> str:
    ...
```

### `use.ingest` — RFC-030 §8 · [usage §4.31](usage.md#431-useingest-rfc-030-8)

Chunks documents and stores them for RAG retrieval; returns
`{"stored": <count>}`.

```python
@use.ingest(chunk_size=512, overlap=64, namespace="docs")
def load_documents() -> list[str]:
    return ["capio is composable", "capio is dependency-free"]
```

### `use.tool` — RFC-030 §9 · [usage §4.32](usage.md#432-usetool-rfc-030-9)

Registers the callable as a tool with a JSON schema (derived from type hints),
exposed via `ctx.capability("tool")["state"]["schema"]`.

```python
@use.tool(name="multiply", description="multiply two ints")
@use.context()
def multiply(a: int, b: int, ctx) -> int:
    return a * b
```

### `use.agent` — RFC-030 §10 · [usage §4.33](usage.md#433-useagent-rfc-030-10)

Loops the model step until it returns a final `content`, executing any
`tool_calls` it emits (up to `max_steps`).

```python
def get_weather(city: str) -> str:
    return f"sunny in {city}"

@use.agent(tools={"get_weather": get_weather}, max_steps=3)
def model_step(messages: list[dict]) -> dict:
    ...
```

### `use.guardrails` — RFC-030 §11 · [usage §4.34](usage.md#434-useguardrails-rfc-030-11)

Runs predicate checks on input and/or output; raises `GuardrailError` when a
predicate returns `False`.

```python
def no_pii(text, ctx) -> bool:
    return "secret" not in text

@use.guardrails(input=no_pii, output=no_pii)
def respond(query: str) -> str:
    ...
```

### `use.token_budget` — RFC-030 §12 · [usage §4.35](usage.md#435-usetoken_budget-rfc-030-12)

Rejects inputs whose estimated token count exceeds `budget` (raises
`TokenBudgetExceededError`, carrying `used`/`budget`).

```python
@use.token_budget(budget=2000)
def complete(messages: list[dict]) -> dict:
    ...
```

### `use.model_router` — RFC-030 §13 · [usage §4.36](usage.md#436-usemodel_router-rfc-030-13)

Picks a model by route predicate and injects it into `ctx.kwargs[model]`.

```python
def is_premium(ctx) -> bool:
    return ctx.kwargs.get("tier") == "premium"

@use.model_router(routes=[{"when": is_premium, "model": "gpt-4o"}],
                  fallback="gpt-4o-mini")
@use.context()
def complete(model, tier, ctx) -> dict:
    ...
```

---

## 7. Going further

- **Backends**: every stateful capability talks to a named backend
  (`cache.*`, `store.*`, `broker.*`, `queue.*`, `trace.*`, `metrics.*`,
  `log.*`, `audit.*`). Bind your own with
  `default_runtime().bind_backend("cache.redis", backend)`. See
  [`usage.md` §5](usage.md#5-backends) and the
  [integration guides](integrations/README.md).
- **Composite form** sorts by priority; **chained form** keeps physical order.
  Chaining works with any number of capabilities — the highest decorator wraps
  the rest.
- **Async**: decorate `async def` with the same decorators; the identical
  options apply.
- **Errors**: each capability raises from
  `capio.exceptions` (e.g. `RateLimitExceededError`, `TimeoutError`,
  `CircuitOpenError`). See [`usage.md` §8](usage.md#8-error-model).
- **Events**: capabilities publish to the runtime event bus (e.g.
  `cache.hit`, `retry.exhausted`, `audit.missing`). See
  [`usage.md` §9](usage.md#9-events).
