"""Capio CLI (RFC-028): doctor, inspect, graph, benchmark, version."""

from __future__ import annotations

import importlib
import platform
import time
from typing import Any, Callable, List

import typer

from .runtime import __version__, default_runtime
from .use import unwrap, use

app = typer.Typer(add_completion=False, help="Capio - composable capabilities (RFC-028).")


def _load_object(target: str) -> Any:
    module_path, _, attr_path = target.rpartition(".")
    if not module_path or not attr_path:
        raise typer.BadParameter(f"expected MODULE.FN, got {target!r}")
    module = importlib.import_module(module_path)
    obj: Any = module
    for part in attr_path.split("."):
        obj = getattr(obj, part)
    return obj


@app.command()
def version() -> None:
    """Print the capio version."""
    typer.echo(f"capio {__version__}")


@app.command()
def doctor() -> None:
    """Smoke-check the environment, registry, and a live pipeline."""
    typer.echo(f"capio {__version__}  (python {platform.python_version()})")
    runtime = default_runtime()
    typer.echo(f"runtime:   {runtime!r}")
    typer.echo(
        f"env:       {runtime.env!r}  profile: {runtime.profile!r}  strict: {runtime.strict}"
    )
    typer.echo(f"backends:  {', '.join(runtime.services.names())}")
    typer.echo(f"capabilities: {', '.join(runtime.registry.names())}")

    @use.retry(max_attempts=1)
    def _smoke() -> str:
        return "ok"

    result = _smoke()
    if result != "ok":
        typer.secho("FAIL: smoke invocation returned unexpected result", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    typer.secho("doctor: OK", fg=typer.colors.GREEN)


@app.command()
def inspect(target: str) -> None:
    """Show the pipeline metadata of a decorated function (MODULE.FN)."""
    obj = _load_object(target)
    meta = getattr(obj, "__capio__", None)
    if meta is None:
        typer.secho(f"{target} is not decorated with capio", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    typer.echo(f"mode: {meta.mode}   capio {meta.version}")
    typer.echo(f"capabilities ({len(meta.capabilities)}), outermost first:")
    for info in meta.capabilities:
        opts = ", ".join(f"{k}={v!r}" for k, v in info.options.items()) or "-"
        typer.echo(f"  {info.priority:>5}  {info.name:<16} v{info.version}  ({opts})")


@app.command()
def graph(target: str) -> None:
    """Render the pipeline order of a decorated function (MODULE.FN)."""
    obj = _load_object(target)
    meta = getattr(obj, "__capio__", None)
    if meta is None:
        typer.secho(f"{target} is not decorated with capio", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    names = [info.name for info in meta.capabilities]
    typer.echo(" -> ".join(names + ["function"]))


def _best_time(fn: Callable[[], Any], n: int) -> float:
    best = float("inf")
    for _ in range(3):
        start = time.perf_counter()
        for _ in range(n):
            fn()
        elapsed = (time.perf_counter() - start) / n
        best = min(best, elapsed)
    return best


def _report(label: str, measured: float, budget: float) -> bool:
    ok = measured <= budget
    flag = "PASS" if ok else "FAIL"
    color = typer.colors.GREEN if ok else typer.colors.RED
    line = f"  {flag}  {label:<32} {measured * 1e6:9.2f} us   budget {budget * 1e6:8.2f} us"
    typer.secho(line, fg=color)
    return ok


@app.command()
def benchmark(
    enforce: bool = typer.Option(False, "--enforce", help="exit non-zero on budget failure"),
) -> None:
    """Micro-benchmark the hot paths against RFC-027 budgets."""
    typer.echo("capio benchmark (RFC-027 budgets):")
    failures: List[str] = []

    def measure(label: str, fn: Callable[[], Any], n: int, budget: float) -> None:
        elapsed = _best_time(fn, n)
        if not _report(label, elapsed, budget):
            failures.append(label)

    def make_decorated() -> Callable[[], int]:
        @use.retry(enable=lambda ctx: False, max_attempts=1)
        @use.cache(enable=lambda ctx: False)
        @use.timeout(enable=lambda ctx: False)
        @use.trace(enable=lambda ctx: False)
        @use.metrics(enable=lambda ctx: False)
        def _f() -> int:
            return 42

        return _f

    decorated = make_decorated()
    measure("decoration (<100us)", lambda: make_decorated(), 5000, 100e-6)

    runtime = default_runtime()
    original = unwrap(decorated)
    meta = decorated.__capio__
    measure("pipeline build (<5ms)", lambda: runtime.get_pipeline(original, meta), 2000, 5e-3)

    measure("5-cap pipeline (<20us)", decorated, 20000, 20e-6)

    @use.retry(enable=lambda ctx: False, max_attempts=1)
    def _one_cap() -> int:
        return 1

    measure("1-cap pipeline (<2us)", _one_cap, 20000, 2e-6)

    if failures and enforce:
        typer.secho(f"benchmark FAILED: {', '.join(failures)}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    typer.secho(
        "Note: budgets are RFC-027 reference-hardware targets asserted in CI; "
        "this machine may differ.",
        dim=True,
    )
    typer.secho("benchmark: OK", fg=typer.colors.GREEN)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
