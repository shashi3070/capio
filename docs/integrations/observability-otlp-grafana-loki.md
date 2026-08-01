# Observability: OpenTelemetry (OTLP), Prometheus/Grafana, Loki

Capio emits telemetry through four capabilities — `trace`, `metrics`, `log`,
and `audit`. Each forwards structured records to a **backend** named in its
`backend` option (defaults: `trace.console`, `metrics.null`, `log.stdio`,
`audit.memory`). This guide shows how to export that telemetry to real
infrastructure:

- **Traces** → OpenTelemetry Collector via OTLP (which Grafana Tempo can ingest)
- **Metrics** → Prometheus, scraped by Grafana
- **Logs** → Loki, queried in Grafana
- **Audit** → durable storage (see [databases.md](databases.md) for Postgres)

## The record shapes each backend receives

### Trace spans — implement `emit(span: dict)`

```python
{
    "name": "app.fetch_user",          # or your use.trace(name=...) value
    "trace_id": "tr-1-abc123",         # parent trace or auto-generated
    "span_id": "span-1-...",
    "parent_span_id": None,            # nested decorate order creates the tree
    "fn": "module.fetch_user",
    "kind": "internal",
    "status": "ok",                    # or "error"
    "error": None,                     # repr(exc) when status == "error"
    "duration_ms": 12.3,
    "attributes": {...},               # use.trace(attributes=..., attributes_from=...)
}
```

Optional keys `args` (when `capture_args=True`) and `result_type` (when
`capture_result=True`).

### Metric records — implement `record(metric: dict)`

```python
# counter (every invocation, tagged with outcome):
{"kind": "counter", "name": "app.fetch_user.calls_total", "tags": {"outcome": "success"}}
# histogram (per-invocation duration in milliseconds):
{"kind": "histogram", "name": "app.fetch_user.duration_ms", "value": 12.3, "tags": {}}
```

### Log lines — implement `log(level: int, message: str, **fields)`

`level` is a stdlib `logging` level (`DEBUG/INFO/WARNING/ERROR`); structured
fields are keyword arguments.

### Audit records — implement `append(record: dict)`

An audit record carries at least `id`, `timestamp`, `actor`, `action`,
`outcome` (`"success"`/`"error"`), plus capability-specific fields. Backends
also implement `query(*, actor, action, limit)`, `verify()`, `size`, `clear`.

> All four capabilities are **fail-safe**: a backend that raises only produces
> a `trace.exporter_failed` / `metrics.exporter_failed` / `log.failed` event and
> never breaks the invocation.

---

## 1. Traces → OTLP / OpenTelemetry Collector

```bash
pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc
```

```python
# otlp_trace.py
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import Status, StatusCode


class OtlpTraceBackend:
    """Trace backend that ships spans to an OTLP endpoint (Collector/Tempo)."""

    def __init__(self, endpoint="http://localhost:4317", service_name="capio-app"):
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
        self._tracer = trace.get_tracer("capio")

    def emit(self, span: dict) -> None:
        with self._tracer.start_as_current_span(span.get("name", "capio.invocation")) as otel:
            for key in ("trace_id", "span_id", "parent_span_id", "fn", "kind", "error", "duration_ms"):
                if span.get(key) is not None:
                    otel.set_attribute(f"capio.{key}", str(span[key]))
            for name, value in (span.get("attributes") or {}).items():
                otel.set_attribute(f"capio.attr.{name}", str(value))
            if span.get("status") == "ok":
                otel.set_status(Status(StatusCode.OK))
            else:
                otel.set_status(Status(StatusCode.ERROR, description=span.get("error") or "error"))
```

Wire it up:

```python
from capio import default_runtime
from otlp_trace import OtlpTraceBackend

default_runtime().bind_backend("trace.otlp", OtlpTraceBackend())

@use.trace(backend="trace.otlp", capture_result=True, attributes={"env": "prod"})
@use.cache(ttl="1m")
def fetch_user(user_id):
    ...
```

The same `trace_id` flows through nested decorated calls, so the whole span tree
lands in Tempo/Collector for waterfall views.

## 2. Metrics → Prometheus (Grafana source)

```bash
pip install prometheus-client
```

```python
# prometheus_metrics.py
from prometheus_client import Counter, Histogram


class PrometheusMetricsBackend:
    """Metric backend that mirrors counter/histogram records into Prometheus."""

    def __init__(self):
        self._counters = {}
        self._histograms = {}

    def record(self, metric: dict) -> None:
        name = metric["name"].replace(".", "_")
        labels = sorted(metric.get("tags", {}))
        tag_values = {k: str(v) for k, v in metric.get("tags", {}).items()}
        if metric["kind"] == "counter":
            counter = self._counters.setdefault(
                name, Counter(name, name, labels)
            )
            counter.labels(**tag_values).inc()
        elif metric["kind"] == "histogram":
            histogram = self._histograms.setdefault(
                name, Histogram(name, name, labels, buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5))
            )
            histogram.labels(**tag_values).observe(metric["value"])
```

Expose the scrape endpoint and bind:

```python
from capio import default_runtime
from prometheus_client import start_http_server
from prometheus_metrics import PrometheusMetricsBackend

start_http_server(8000)                       # GET /metrics for Prometheus
default_runtime().bind_backend("metrics.prom", PrometheusMetricsBackend())

@use.metrics(backend="metrics.prom", tags={"service": "api"})
def checkout(order_id):
    ...
```

Prometheus scrapes `localhost:8000`, Grafana visualizes. Durations are in
milliseconds (`*_duration_ms`), so a `Histogram` with millisecond buckets reads
correctly on dashboards.

### OTLP metrics instead

Swap the exporter only — the backend still implements `record(metric: dict)`:

```python
from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint="http://localhost:4317"))
metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
_meter = metrics.get_meter("capio")


class OtlpMetricsBackend:
    def __init__(self):
        self._counters = {}
        self._histograms = {}

    def record(self, metric: dict) -> None:
        name = metric["name"].replace(".", "_")
        attrs = {k: str(v) for k, v in metric.get("tags", {}).items()}
        if metric["kind"] == "counter":
            self._counters.setdefault(name, _meter.create_counter(name)).add(1, attrs)
        elif metric["kind"] == "histogram":
            self._histograms.setdefault(name, _meter.create_histogram(name)).record(metric["value"], attrs)
```

## 3. Logs → Loki

```bash
pip install requests
```

```python
# loki_log.py
import logging
import time


class LokiLogBackend:
    """Log backend that pushes structured lines to Loki's /api/v1/push."""

    def __init__(self, url="http://localhost:3100", labels=None, timeout=2.0):
        import requests  # optional dependency, imported lazily
        self._requests = requests
        self._url = url.rstrip("/")
        self._labels = {"app": "capio", **(labels or {})}
        self._timeout = timeout

    def log(self, level: int, message: str, **fields) -> None:
        line = {"level": logging.getLevelName(level), "message": message, **fields}
        payload = {
            "streams": [
                {
                    "stream": self._labels,
                    "values": [[str(int(time.time() * 1e9)), self._requests.models.json.dumps(line)]],
                }
            ]
        }
        try:
            self._requests.post(f"{self._url}/loki/api/v1/push", json=payload, timeout=self._timeout)
        except Exception:
            pass  # never break the invocation


default_runtime().bind_backend("log.loki", LokiLogBackend(labels={"service": "api"}))

@use.log(backend="log.loki", include_args=True)
def payment(amount):
    ...
```

`logging.getLevelName(level)` gives `"INFO"`/`"WARNING"`/… so Loki queries like
`{app="capio", service="api"} |= "payment"` work out of the box.

## 4. Audit → durable store

Audit is a *different* contract (`append`/`query`/`verify`), so it belongs to
the database guide: see [databases.md](databases.md) §3 for a Postgres audit
backend. For a lightweight ship-it-everywhere option, wrap the Loki backend:

```python
class LokiAuditBackend:
    def __init__(self, loki_log_backend):
        self._log = loki_log_backend
        self._records = []

    def append(self, record):
        self._records.append(dict(record))
        self._log.log(logging.INFO, "audit", **record)
        return dict(record)

    def query(self, *, actor=None, action=None, limit=100):
        return [r for r in self._records if (actor is None or r.get("actor") == actor)
                and (action is None or r.get("action") == action)][-limit:]

    def verify(self):
        return True

    @property
    def size(self):
        return len(self._records)

    def clear(self):
        self._records.clear()
```

## 5. Grafana

Once the three exporters are running, configure Grafana datasources:

| Data | Export | Grafana datasource type |
|---|---|---|
| Traces | OTLP → Collector/Tempo | **Tempo** (or the collector's OTLP source) |
| Metrics | Prometheus at `http://prometheus:9090` | **Prometheus** |
| Logs | Loki at `http://loki:3100` | **Loki** |

Correlate by `service.name` (set in the backends) so a trace waterfall links to
its logs and metrics. All four capabilities stay fail-safe: exporter outages
never raise into your business logic.
