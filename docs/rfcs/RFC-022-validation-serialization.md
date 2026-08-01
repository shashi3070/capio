# RFC-022: Validation, Serialization, Encryption, Masking, Deduplication

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies five data-plane capabilities that run around a callable's input/output:
**Validation**, **Serialization**, **Encryption**, **Masking** (PII/secret redaction), and
**Deduplication**. They are the bridge between the runtime's behavior model and the data flowing
through it, and they are the foundation for LLM input/output handling (RFC-030).

## 2. Validation capability

### 2.1 API

```python
@use.validate(
    schema=MyModel,               # pydantic model | jsonschema | callable | list of validators
    mode="input",                 # "input" | "output" | "both"
    coerce=True,                  # coerce input to schema type
    strip_unknown=False,          # remove unknown fields (False = reject)
    on_error="raise",             # "raise" (ValidationError) | "transform" (fn) 
    validators=["iban_check"],    # named registered validators (RFC-014)
)
def create_account(data: dict) -> Account: ...
```

### 2.2 Semantics

- **Input validation** runs after auth (priority 900, RFC-005 §4.2) and before any expensive
  work/cache: bad input never reaches the cache or the function.
- **Output validation** runs after the function (in the validation step's exit path), guarding
  contract of return values; `coerce` normalizes output types.
- Validation failure raises `ValidationError` (RFC-025) typed with field errors; it is NOT
  retried by retry (deterministic, RFC-017 §3.1) unless explicitly listed in `retry_on`.
- Backend-free by default: pydantic (if installed) or jsonschema; registered validators via the
  validator registry (RFC-014). A `ValidationBackend` (RFC-015) may provide remote/ML validation
  (content moderation, embeddings checks — RFC-030 guardrails).

### 2.3 LLM / AI validation

- Prompt/response schemas validated the same way: `@use.validate(schema=PromptSchema)` on an
  LLM call; response `coerce` into typed dataclasses; structured-output parsing (JSON-schema
  constrained decoding, function calling) is delegated to the model integration
  (RFC-030 §3.3).

## 3. Serialization capability

### 3.1 Purpose

Serialize/deserialize a callable's input/output across boundaries: cache (RFC-016), queues and
events (RFC-023), RPC, and persistence. It is a **registry-backed** facility (RFC-014) rather
than a step in most pipelines — it is explicitly composable where needed.

### 3.2 Serializer registry

| Serializer | Use | Safety |
| ---------- | --- | ------ |
| `json` (default) | safe, cross-language | no objects; arbitrary precision via int passthrough |
| `pickle` | fast Python-native | **opt-in only**; insecure for untrusted data (RFC-026) |
| `msgpack` | compact binary | safe for json-compatible + bytes |
| `cloudpickle` | lambdas/functions | **opt-in only**; execution risk (RFC-026) |
| custom | app types | registered via SDK |

- `capio.serialize.encode(obj, serializer="json")` / `decode(...)`; backends and capabilities use
  these uniformly.
- **Content-typed headers** (RFC-016 §8) record serializer + version so data remains
  decode-tolerant across versions (RFC-032).

### 3.3 Rules

- Untrusted input is ALWAYS decoded with a safe serializer (`json`, `msgpack`); `pickle`/
  `cloudpickle` decoding requires explicit config and a trust declaration (RFC-026 §7).
- Serialization failure raises `SerializationError` (RFC-025) and NEVER corrupts a cache/queue
  entry: store/publish is aborted for that value and observed.

## 4. Encryption capability

### 4.1 Purpose

Encrypt/decrypt input, output, or specific fields at the data plane, using the secret backend
(RFC-015 §3.8) for key management.

### 4.2 API

```python
@use.encrypt(
    fields=("ssn", "card"),
    mode="inout",                 # "in" | "out" | "inout"
    algorithm="AES-GCM",
    key_ref="capio:app:data-key", # secret backend ref (RFC-026)
    key_cache_ttl="1h",           # cached in memory, never logged
)
def checkout(cart: dict) -> dict: ...
```

### 4.3 Semantics

- Field-level: selected fields encrypted before the function sees them (`in`) and/or before the
  caller sees them (`out`), depending on mode.
- Whole-value: wraps the value in the serialized envelope (used by cache encryption, RFC-016 §8).
- Keys are never in config/plaintext; the secret backend resolves and caches them in-memory
  (RFC-026 §6).
- A lost key raises `EncryptionKeyError` (RFC-025) with explicit recovery guidance; audit records
  key rotation events.

## 5. Masking capability (PII / secrets)

### 5.1 Purpose

Redact sensitive data in records, logs, traces, audit, events, and LLM prompts before they leave
the process. Foundation for RFC-006 §9 snapshot redaction and RFC-019/020/030.

### 5.2 API

```python
@use.mask(
    fields=("ssn", "email", "api_key"),
    patterns=(r"\b\d{9}\b",),     # regex rules
    redact_with="***",            # or "sha256:<ref>", "preserve:<len>"
    modes=("logs", "traces", "audit", "events", "prompts"),
    field_policy="keep_first_last", # masking style
)
def process_customer(data: dict) -> dict: ...
```

### 5.3 Semantics

- The masking capability installs **redaction rules** into the Context (RFC-006 §9), which every
  emitting surface (log, trace, audit, event bus, snapshot) applies before serialization.
- `redact_with="sha256:<ref>"` replaces values with a stable hash + reference, enabling
  correlational analysis without exposing the value.
- Redaction is applied at the **emission boundary**, never to the in-memory value the function
  uses — business logic is unaffected.
- Default field patterns include credential-shaped keys (`*password*`, `*token*`,
  `*api_key*`, `*secret*`, `*ssn*`, `*card*`...) per RFC-009 §10.
- **LLM prompts** are masked before logging/tracing/audit by default (RFC-030 §9.2); the masking
  capability provides the `mask_prompt(ctx, prompt)` helper used by the LLM observability layer.

## 6. Deduplication capability

### 6.1 Purpose

Prevent duplicate side effects from duplicate calls (idempotency), typically by replaying the
result of the first execution. Distinct from debounce (RFC-018 §6) which coalesces.

### 6.2 API

```python
@use.dedup(
    key=None,                     # (ctx) -> str; default canonical args
    backend="memory",             # store backend (cache/lock, RFC-015)
    ttl="1h",                     # dedup window
    store_exceptions=False,       # record failures as duplicates? (default no)
    on_duplicate="replay",        # "replay" (return stored) | "raise" | "skip"
)
def create_order(order_id: str, items: list) -> dict: ...
```

### 6.3 Semantics

- First call executes; the result (or completion marker) is stored keyed by the dedup key with
  `ttl`.
- Duplicate call within the window: `replay` returns the stored result (with a
  `ctx.dedup = "replayed"` marker), `raise` raises `DuplicateCallError`, `skip` returns without
  executing.
- Concurrency-safe: uses the lock interface (RFC-015 §3.9) — `singleflight` for the same key so
  two racing duplicates do not double-execute (only one runs, the other replays).
- Stored result must be serializable (RFC-016 serializer); the SDK stores a completion
  sentinel + result envelope. Exceptions are NOT replayed by default (a failed first call is
  retryable).

## 7. Ordering & interactions

- Default priority: `validate` 900 (input), `mask` 850, `encrypt` 800 (input mode), `dedup` 750
  (shares tier with cache), cache 750 → retry 700 → ... → `encrypt` (output) / `validate`
  (output) on the unwind path. Output-phase steps unwind in reverse (RFC-005 §3.1).
- Masking rules are active for the whole invocation (RFC-006 §9) regardless of step order.
- **Dedup + retry**: dedup is placed OUTSIDE retry by default so a retried call does not replay
  its own partial results; `idempotent=True` on retry (RFC-017 §6) pairs with dedup.
- **Encryption + masking**: encryption happens before masking at the boundary only for the
  *stored/serialized* form; masking of logs/traces applies to plaintext fields.

## 8. Document Dependencies

- Concepts: RFC-002 (§7); pipeline: RFC-005; context snapshot redaction: RFC-006; config:
  RFC-009; registries: RFC-014; backends: RFC-015; cache: RFC-016; errors: RFC-025; security:
  RFC-026; AI: RFC-030.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
