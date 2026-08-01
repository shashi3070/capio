"""Observability capabilities: trace, metrics, log (RFC-019/RFC-020)."""

from __future__ import annotations

import io

from capio import use
from capio.backends.console_trace import ConsoleTraceBackend
from capio.backends.null_metrics import NullMetricsBackend
from capio.runtime import default_runtime


def test_trace_writes_span_line() -> None:
    stream = io.StringIO()
    default_runtime().services.bind_replace("trace.console", ConsoleTraceBackend(stream=stream))

    @use.trace()
    def add(a: int, b: int) -> int:
        return a + b

    assert add(1, 2) == 3
    output = stream.getvalue()
    assert output.startswith("[capio.trace] {")
    assert '"name":' in output
    assert "add" in output


def test_trace_never_breaks_invocation() -> None:
    class ExplodingBackend:
        def emit(self, span):
            raise RuntimeError("sink down")

    default_runtime().services.bind_replace("trace.console", ExplodingBackend())

    @use.trace()
    def add(a: int, b: int) -> int:
        return a + b

    assert add(2, 3) == 5


def test_metrics_records_counters_and_histograms() -> None:
    backend = NullMetricsBackend()
    default_runtime().services.bind_replace("metrics.null", backend)
    backend.clear()

    @use.metrics(name="work")
    def work(x: int) -> int:
        return x

    work(1)
    work(2)
    try:
        @use.metrics(name="fail")
        def fail() -> None:
            raise ValueError("x")

        fail()
    except ValueError:
        pass

    counters = backend.counters()
    histograms = backend.histograms()
    assert "work.calls_total" in counters
    assert counters["work.calls_total"] == 2
    assert "work.duration_ms" in histograms
    assert len(histograms["work.duration_ms"]) == 2
    assert "fail.calls_total" in counters


def test_log_records_invocation() -> None:
    class InMemoryLogBackend:
        def __init__(self) -> None:
            self.records = []

        def log(self, level, message, **fields):
            self.records.append((level, message, fields))

    backend = InMemoryLogBackend()
    default_runtime().services.bind_replace("log.stdio", backend)

    @use.log()
    def add(a: int, b: int) -> int:
        return a + b

    add(1, 2)
    assert len(backend.records) == 1
    level, message, fields = backend.records[0]
    assert fields["fn"].endswith("add")
    assert fields["outcome"] == "success"
    assert "duration_ms" in fields


def test_metrics_fail_safe() -> None:
    class ExplodingBackend:
        def record(self, metric):
            raise RuntimeError("down")

    default_runtime().services.bind_replace("metrics.null", ExplodingBackend())

    @use.metrics()
    def work(x: int) -> int:
        return x

    assert work(1) == 1
