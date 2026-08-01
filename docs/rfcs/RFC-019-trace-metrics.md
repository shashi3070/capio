# RFC-019: Trace & Metrics Capabilities

- **Status:** Draft
- **Author:** Shashi Kundan
- **Created:** 2026-08-01
- **Supersedes:** none

## 1. Purpose

This RFC specifies the **Trace** and **Metrics** capabilities: the span model, metric model,
backends, default instrumentation, LLM/AI observability, and the fail-safe behavior that keeps
observability never breaking business logic (RFC-001 §3.8). Consumed via `@use.trace()` and
`@use.metrics(...)` (RFC-003).

## 2. Trace capability

### 2.1 API

```python
@use.trace(
    name="auto",                  # span name; default f"{fn_module}.{fn_name}"
    attributes=None,              # static attributes
    attributes_from=None,         # (ctx) -> dict dynamic attributes
    capture_args=False,           # record args (redacted, RFC-006 §9)
    capture_result=False,         # record result summary (size/type, redacted)
    backend="opentelemetry",      # trace backend (RFC-015 §3.3)
    propagate=True,               # emit W3C traceparent from ctx.carrier (RFC-006 §5)
    sampled=None,                 # None=inherit | True | False | (ctx)->bool
    span_kind="internal",         # "internal" | "server" | "client" | "producer" | "consumer"
)
def process(order: dict) -> dict: ...
```

### 2.2 Span model

- **Trace/span conventions follow OpenTelemetry** where possible (RFC-001 §6.4): one trace per
  top-level invocation chain; each capability phase and the wrapped call are child spans of the
  invocation span.
- Default span tree for `@use.trace()` alone: `invocation` → `wrapped_call`.
  With other capabilities, the trace step (priority 600, RFC-005 §4.2) emits spans around the
  function AND the engine emits spans around each capability phase (`capio.capability.retry`
  etc.) — configurable `instrument_runtime: true` default in prod profile.
- The trace capability is NOT required for spans; the runtime's internal instrumentation
  (RFC-004 §3.8) is always active when a trace backend is configured.

### 2.3 Propagation

- The trace step reads `ctx.carrier` (`traceparent`/`tracestate`, RFC-006 §5.1) and joins the
  inbound trace; outbound spans are placed back on `ctx.scope` so child invocations and
  integrations (HTTP, Kafka, Celery, MCP — RFC-006 §7) inherit them.
- When no inbound carrier exists, a new trace is started for the invocation.
- `sampled` default inherits from the inbound `sampled` flag; the runtime supports a sampling
  backend/hook for head-based sampling and a `Sampler` extension point.

### 2.4 Backends

Trace backends implement `TraceBackend` (RFC-015 §3.3): OpenTelemetry, Jaeger, Zipkin, Tempo,
Datadog, Elastic, and a built-in `console` backend for dev. Export is **asynchronous by default**
(bounded queue, RFC-008 §2.4) so span export never blocks the invocation.

### 2.5 Fail-safe

- Span export failure is observed (`trace.exporter_failed`) and dropped — never raised into the
  invocation (RFC-005 §7). In strict mode it still does not raise; tracing is always best-effort.

## 3. Metrics capability

### 3.1 API

```python
@use.metrics(
    name="auto",                  # metric prefix; default fn_module.fn_name
    counter=True,                 # record calls + duration histogram
    tags=None,                    # static tags
    tags_from=None,               # (ctx) -> dict dynamic tags
    record_duration=True,         # emit duration histogram
    record_result=True,           # tag outcome: success | <error_type> | timeout
    backend="prometheus",         # metrics backend (RFC-015 §3.2)
    per_instance=False,           # include instance id tag for methods
)
def render(page_id: str) -> str: ...
```

### 3.2 Metric model

Capio emits three standard instruments per decorated callable (namespaced `<prefix>`):

| Instrument | Name | Meaning |
| ---------- | ---- | ------- |
| counter | `<prefix>.calls_total` | invocations, tagged `outcome` |
| histogram | `<prefix>.duration_ms` | wall time |
| gauge | `<prefix>.in_flight` | concurrent invocations (updated on enter/exit) |

Plus capability-specific metrics (retry, cache, circuit, rate, timeout — RFC-016/017/018) and
runtime metrics (pipeline build, plugin load, registry lookup — RFC-004 §3.8).

- The runtime owns a `MetricsRegistry`; capability instances emit into it; the metrics backend
  reads at flush (RFC-005 stage 11).
- No metric is emitted unless a metrics backend is configured; a null/`test` backend is the
  default in `test` profile (RFC-009 §7).

### 3.3 Backends

`MetricsBackend` (RFC-015 §3.2): Prometheus, OpenTelemetry, StatsD, Datadog, NewRelic,
CloudWatch, plus built-in `console`/`null`. Flush failure is observed and dropped (fail-safe).

### 3.4 Hooks

`before_metrics`/`after_metrics` (RFC-007 §3.2) let plugins customize metric names/tags per
invocation (e.g. attach tenant id).

## 4. LLM / AI observability

AI calls are the single most important thing to trace in 2026. The AI observability contract
(RFC-030 §9) builds on this RFC:

- **LLM spans**: `span_kind="client"`, attributes: `gen_ai.system`, `gen_ai.request.model`,
  `gen_ai.usage.prompt_tokens`, `gen_ai.usage.completion_tokens`,
  `gen_ai.usage.total_tokens`, `gen_ai.request.temperature`, `gen_ai.request.top_p`,
  `gen_ai.request.max_tokens`, `gen_ai.operation.name` — following the OpenTelemetry GenAI
  semantic conventions.
- **Prompt/response recording**: with `capture_args=True`/`capture_result=True` and explicit
  `capture_prompts=True` (default false — PII-safe), prompts are recorded as span attributes;
  redaction (RFC-026 §6) applies automatically.
- **Cost metrics**: `gen_ai.cost_usd` gauge/histogram derived from token usage + model pricing
  table (RFC-030 §9.3).
- **Tool/agent spans**: `tool.name`, `tool.input`, `tool.output` for tool calls and agent steps
  (RFC-030 §5).
- **Evaluation linkage**: `gen_ai.eval.id` tags link production invocations to offline eval runs
  (RFC-029 §7).
- Backends: standard trace/metrics backends PLUS purpose-built LLM observability backends
  (`trace.llm_observability` kind: Langfuse, LangSmith, Helicone, Phoenix, Arize, W&B, custom)
  that consume the same GenAI spans. Contract tests (RFC-029) verify the GenAI attribute schema.

## 5. Trace + metrics interplay

- Duration histograms and invocation spans both key off `ctx.start_time`/`ctx.invocation_id`
  (RFC-006), so they correlate by ID.
- The engine emits the runtime instrumentation spans through the SAME trace backend and counters
  through the SAME metrics backend, so `capio trace` shows capability internals.

## 6. Document Dependencies

- Concepts: RFC-002 (§7.3–7.4); lifecycle: RFC-005; context: RFC-006; events: RFC-008; config:
  RFC-009; backends: RFC-015; errors: RFC-025; security/redaction: RFC-026; performance:
  RFC-027; AI: RFC-030.

## Change Record

| Version | Date | Change |
| ------- | ---- | ------ |
| 0.1     | 2026-08-01 | Initial draft. |
