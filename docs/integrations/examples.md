# End-to-end examples

Three complete, combined setups showing how the guides in this directory fit
together. Each example assumes the backend classes from the other guides
(`RedisCacheBackend`, `OtlpTraceBackend`, `PrometheusMetricsBackend`,
`LokiLogBackend`, `SqliteStore` / `PostgresStore`, `PostgresAuditBackend`) and
the auth providers from `auth-jwt-oidc-oauth-rbac.md`.

Backend setup is a one-time block at startup:

```python
# app_bootstrap.py
from capio import default_runtime

runtime = default_runtime()
runtime.bind_backend("cache.redis",   RedisCacheBackend(os.environ["REDIS_URL"]))
runtime.bind_backend("trace.otlp",    OtlpTraceBackend())
runtime.bind_backend("metrics.prom",  PrometheusMetricsBackend())
runtime.bind_backend("log.loki",      LokiLogBackend(labels={"service": "api"}))
runtime.bind_backend("store.sqlite",  SqliteStore("capio.db"))
runtime.bind_backend("audit.postgres", PostgresAuditBackend(os.environ["DATABASE_URL"]))
```

---

## Example 1 — HTTP API: JWT auth + Redis cache + rate limit + audit + observability

A read API endpoint, hardened end to end. Priority order in the composite is by
capability priority; here we chain so `auth` runs before the readers it feeds.

```python
from capio import use
from jwt_auth import rsa_jwt_provider

jwt_provider = rsa_jwt_provider(
    public_key=os.environ["JWT_PUBLIC_KEY"],
    issuer="https://accounts.example.com",
    audience="billing-api",
)

@use.auth(provider=jwt_provider, scopes=["billing:read"], required=True)
@use.rate_limit(limit=100, window="1m", key=lambda ctx: ctx.auth["subject"])
@use.cache(backend="cache.redis", ttl="5m")
@use.trace(backend="trace.otlp", capture_result=True)
@use.metrics(backend="metrics.prom", tags={"service": "billing"})
@use.audit(backend="audit.postgres", action="invoice.read", actor=lambda ctx: ctx.auth["subject"])
@use.log(backend="log.loki", include_args=True)
@use.context()
def get_invoice(invoice_id, token, ctx):
    """Expose the authenticated actor to the handler via ctx.auth."""
    actor = ctx.auth["subject"]
    return fetch_invoice(actor, invoice_id)   # your business logic
```

Behavior on every call:

1. `auth` verifies the JWT (signature, issuer, audience) → `AuthenticationError`
   for bad/missing tokens.
2. `rate_limit` counts per-subject; over 100/min → `RateLimitExceededError`.
3. `cache` serves a 5-minute Redis hit without touching your DB.
4. `trace`/`metrics`/`log` ship the span, counters + duration histogram, and a
   structured line to Tempo/Prometheus/Loki.
5. `audit` records `who` did `what` to which resource in Postgres, even on
   errors (outcome `"error"`).

Async version — the same chain works on `async def`:

```python
@use.auth(provider=jwt_provider, scopes=["billing:read"])
@use.cache(backend="cache.redis", ttl="5m")
@use.trace(backend="trace.otlp")
async def get_invoice(invoice_id, token):
    ...
```

---

## Example 2 — Order service: transaction + idempotency + outbox + queue + SQLite

A payment path that must be exactly-once and not lose events: idempotency key
replay protection, a rollback-scoped transaction, and a transactional outbox
persisted in `store.sqlite`. Together with `publish` this is the "transactional
outbox" pattern — the event is written atomically with the business change and
forwarded afterward, so no event is lost and none is double-published.

```python
from capio import use

@use.publish(
    topic="order.placed",
    outbox="store.sqlite",            # writes into the outbox namespace in SQLite
    include_result=True,
)
@use.idempotent(backend="store.sqlite", key="Idempotency-Key", replay="return")
@use.transaction(
    actions={
        "charge.wallet": {"commit": lambda result: debit_wallet(result["amount"]),
                          "rollback": lambda: credit_wallet(result["amount"])},
        "book.inventory": {"commit": lambda result: reserve_stock(result["sku"], result["qty"]),
                           "rollback": lambda: release_stock(result["sku"], result["qty"])},
    },
)
@use.context()
def place_order(order, Idempotency_Key, ctx):
    return save_order(order)          # idempotent + atomic + durable event
```

A separate worker drains the outbox and forwards to the real broker
(`broker.memory` here; swap for any broker-protocol backend):

```python
@use.consume(topic="order.placed", broker="broker.memory", group="outbox-forwarder")
def forward_outbox(message, ctx):
    return kafka_produce("orders", message)   # your real sink
```

Replays of a completed order return the stored result instead of charging
again; a concurrent duplicate with a **different** request raises
`IdempotencyConflictError`; a failed transaction rolls back all participants.

---

## Example 3 — RAG chat service: Postgres store + memory + semantic cache + LLM

A chat endpoint with persistent memories and retrieval in Postgres, semantic
cache for repeated questions, and full observability.

```python
from capio import use

@use.auth(provider=jwt_provider, scopes=["chat"])
@use.memory(store="store.postgres", namespace=f"chat-{user_id}", top_k=5)
@use.rag(store="store.postgres", namespace="docs", top_k=3)
@use.semantic_cache(backend="cache.redis", threshold=0.92)
@use.llm_cache(backend="cache.redis", ttl="5m")
@use.trace(backend="trace.otlp", attributes_from=lambda ctx: {"user": ctx.auth["subject"]})
@use.metrics(backend="metrics.prom", tags={"service": "assistant"})
@use.llm(
    model="gpt-4o-mini",
    provider=lambda request: call_openai(
        request["messages"],
        context=request.get("context", []),
        memories=request.get("memories", []),
    ),
)
@use.context()
def chat(user_id, message, token, memories, context, ctx):
    """Build the LLM request; `memories`/`context` are injected by the pipeline."""
    return {
        "messages": [{"role": "user", "content": message}],
        "context": context,
        "memories": memories,
    }
```

What each layer contributes:

1. `memory` loads the user's last `top_k` conversations from Postgres and passes
   them into `chat` as the `memories` kwarg; it stores the exchange afterward.
2. `rag` retrieves the top-3 document chunks and passes them as `context`.
3. `semantic_cache` returns a stored answer when a similar request was made;
   `llm_cache` dedupes exact prompt/response pairs for 5 minutes.
4. `trace`/`metrics` attach the actor as span attributes and record latencies.
5. `llm` injects `model` into the request kwargs, calls your `provider` with the
   request built by `chat`, and stores the response in
   `ctx.capability("llm")["state"]["response"]`.

The `_ai` pipeline degrades gracefully when a store or embedder is missing
(fail-safe), so a cold cache or empty history still produces a valid call.

---

## Verifying the wiring

```bash
python -m capio.cli doctor        # lists bound backends + registered capabilities
python -m capio.cli graph myapp.get_invoice   # prints pipeline order
```

`doctor` surfaces missing backends early; `graph` shows the exact execution
order (priority for composites, physical order for chains) so you can confirm
`auth` wraps its readers.

## Testing integrations without infrastructure

Every example replaces its external backend with the in-memory default during
tests — no Redis, Postgres, or Loki needed:

```python
from capio.backends.memory_cache import MemoryCacheBackend

def test_get_invoice(runtime):
    runtime.services.bind_replace("cache.redis", MemoryCacheBackend())
    ...
```

The decorated functions are unchanged; only the bound backend differs. See
`docs/usage.md` §5 and `docs/custom_capabilities.md` for the full extension
contract.
