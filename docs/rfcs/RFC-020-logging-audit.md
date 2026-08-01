# RFC-020: Logging & Audit Capabilities

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies the **Logging** and **Audit** capabilities: structured logging, log
backends, the audit trail contract (who did what, when, with what outcome), correlation with
context (RFC-006), and LLM/AI audit (prompt/response/decision trails for compliance). Consumed
via `@use.log(...)` and `@use.audit(...)` (RFC-003).

## 2. Logging capability

### 2.1 API

```python
@use.log(
    logger_name="auto",           # default fn_module
    level="INFO",                 # default log level for this callable
    on_success="INFO",            # level for success records
    on_error="WARNING",           # level for failure records
    include_args=False,           # record args (redacted, RFC-006 §9)
    include_result=False,         # record result summary (redacted)
    include_duration=True,
    backend="stdio",              # log backend (RFC-015 §3.4)
    message=None,                 # custom format string
)
def refund(order_id: str) -> dict: ...
```

### 2.2 Behavior

- Emits one structured `LogRecord` per invocation (or per retry attempt when
  `log_every_attempt=True`): timestamp, level, `ctx.invocation_id`, `ctx.request_id`,
  `ctx.correlation_id`, fn identity, duration, outcome.
- Records are structured (RFC-002 §7.1 serializer): JSON backend emits JSON; text backends render
  a canonical line format.
- The log capability is a **facade over the Context logger handle** (`ctx.logger`, RFC-006 §2);
  plugins may also call `ctx.logger` directly.

### 2.3 Backends

`LogBackend` (RFC-015 §3.4): stdlib, structlog, loguru, JSON, console, file, cloud logging
(CloudWatch, GCP, Azure), Datadog. `stdio` (console+file) is the default in `dev`; `null` in
`test` profile (RFC-009 §7). Backend failure degrades to the stdlib fallback and is observed
(`log.failed`) — never raised.

## 3. Audit capability

### 3.1 Purpose

The audit trail is the **compliance-grade** record: tamper-evident, complete, queryable,
retention-managed. It is distinct from logs (which are operational). Every audit event records
the actor, action, resource, outcome, and supporting context.

### 3.2 API

```python
@use.audit(
    action="auto",                # default fn_name
    resource=None,                # (ctx) -> str resource identifier
    actor=None,                   # (ctx) -> actor id; default ctx.auth principal
    record_input=False,           # full input (schema-based, redacted)
    record_output=False,          # full output (schema-based, redacted)
    include_meta=True,            # ids, timing, ip/host where available
    backend="sqlite",             # audit backend (RFC-015 §3.5 db / custom)
    hash_chain=True,              # tamper-evidence via chained hashes
    immutability="append",        # "append" | "crypto" (WORM/cloud) 
)
def transfer(from_acct: str, to_acct: str, amount: float) -> dict: ...
```

### 3.3 Audit record

```json
{
  "audit_id": "A-…",
  "ts": "2026-08-01T12:00:00Z",
  "action": "transfer",
  "actor": "user:u-42",
  "resource": "acct:1001→acct:2002",
  "outcome": "success",
  "input": { "to_acct": "acct:2002", "amount": 150.0 },
  "ctx": { "request_id": "r-…", "correlation_id": "c-…", "invocation_id": "i-…" },
  "prev_hash": "sha256:…",
  "hash": "sha256:…"
}
```

### 3.4 Integrity (tamper evidence)

- `hash_chain=True`: each record's hash includes the previous record's hash, forming an
  append-only chain. Auditors verify the chain end-to-end.
- `immutability="crypto"`: records are also written to an append-only/WORM or crypto-anchored
  sink (e.g. cloud object storage with WORM, or periodic digest to a public ledger) — future
  RFC-032 roadmap.
- A failed audit write is **not silently ignored**: audit is the one capability that defaults to
  `degradation="propagate"` (RFC-005 §7) — a failed audit write raises `AuditWriteError`
  (RFC-025) because an incomplete compliance record is worse than a failed operation. Operators
  may configure `degradation="bypass"` with an explicit operational risk decision.

### 3.5 Audit backends

`db`/`log` backends (RFC-015) with append-only semantics: SQLite/Postgres (immutable table with
revoked UPDATE/DELETE), object store, kafka (for event-sourced audit), cloud audit sinks. The
audit capability defines its own `AuditBackend` interface (append + verify + query) built on top
of `db` where possible.

## 4. AI audit (LLM/AI compliance trail)

For AI call sites (RFC-030), the audit capability adds an **AI-specific record**:

- `model`, `prompt` (redacted or hashed), `response` (redacted/hashed), `tool_calls` made,
  `tokens`, `cost`, `agent_steps`, `guardrail_results` (which guardrails passed/failed,
  RFC-030 §6), and the `eval.id` when running evals.
- `record_input/output` for prompts defaults to **hash-only** (`record_prompt="hash"`) for
  privacy; `record_prompt="full"` requires the schema-based redaction config (RFC-026 §6) and an
  explicit opt-in.
- AI audit records feed compliance workflows (who sent what prompt to which model with which
  data), and are queryable alongside `capio audit` (RFC-028).

## 5. Correlation

Both capabilities correlate records by `ctx.request_id`/`correlation_id`/`invocation_id`
(RFC-006 §2.1), so an audit record, its logs, its trace, and its metrics are joinable by ID —
the observability spine (RFC-019 §5).

## 6. Events & metrics

- Events: `log.recorded`, `audit.recorded`, `audit.write_failed` (RFC-008).
- Metrics: `audit.records_total`, `audit.write_latency_ms`, `audit.write_failures_total`
  (RFC-019).

## 7. Document Dependencies

- Concepts: RFC-002; lifecycle: RFC-005; context/redaction: RFC-006; events: RFC-008; config:
  RFC-009; backends: RFC-015; observability: RFC-019; security: RFC-026; errors: RFC-025;
  AI: RFC-030; CLI: RFC-028.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
