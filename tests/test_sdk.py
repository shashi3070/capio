"""SDK contract tests: custom capabilities, lifecycle, class decoration (RFC-012)."""

from __future__ import annotations

from capio import use
from capio.config import FrozenConfig
from capio.exceptions import CapabilityRuntimeError
from capio.registry import registry
from capio.sdk import Capability


class _Stamps(Capability):
    name = "stamps"
    version = "2.0.0"
    description = "Test-only capability that stamps the result."
    priority = 400
    degradation = "bypass"

    schema = {
        "prefix": {"type": "str", "default": "stamped"},
        "enable": {"type": "any", "default": None},
    }

    def __init__(self) -> None:
        super().__init__()
        self.lifecycle = []

    def configure(self, config: FrozenConfig) -> None:
        super().configure(config)
        self.lifecycle.append("configure")

    def initialize(self, services) -> None:
        super().initialize(services)
        self.lifecycle.append("initialize")

    def start(self) -> None:
        self.lifecycle.append("start")

    def stop(self) -> None:
        self.lifecycle.append("stop")

    def run(self, ctx, call_next):
        result = call_next(ctx)
        return f"{self.cfg.prefix}:{result}"


def test_custom_capability_registers_and_runs() -> None:
    registry.register(_Stamps)
    try:
        @use.stamps(prefix="v1")
        def fn() -> str:
            return "ok"

        assert fn() == "v1:ok"
        assert fn.__capio__.capabilities[0].name == "stamps"
    finally:
        registry.unregister("stamps")


def test_lifecycle_happens_at_build_time() -> None:
    from capio import pipeline

    registry.register(_Stamps)
    try:
        @use.stamps()
        def fn() -> str:
            return "ok"

        pipe = pipeline(fn)
        step = pipe.steps[0]
        assert step.lifecycle == ["configure", "initialize", "start"]
        assert fn() == "stamped:ok"
    finally:
        registry.unregister("stamps")


def test_decorating_a_class_wraps_methods() -> None:
    registry.register(_Stamps)
    try:
        @use.stamps()
        class Service:
            def greeting(self) -> str:
                return "hi"

        instance = Service()
        assert instance.greeting() == "stamped:hi"
    finally:
        registry.unregister("stamps")


def test_static_and_class_methods_decorated() -> None:
    registry.register(_Stamps)
    try:
        @use.stamps()
        class Service:
            @staticmethod
            def ping() -> str:
                return "pong"

            @classmethod
            def clsping(cls) -> str:
                return "clspong"

        assert Service.ping() == "stamped:pong"
        assert Service.clsping() == "stamped:clspong"
    finally:
        registry.unregister("stamps")


def test_start_failure_is_reported() -> None:
    class _Broken(Capability):
        name = "broken"
        priority = 600

        def start(self) -> None:
            raise RuntimeError("nope")

    registry.register(_Broken)
    try:
        from capio import pipeline

        @use.broken()
        def fn() -> str:
            return "x"

        try:
            pipeline(fn)
            raise AssertionError("expected CapabilityRuntimeError")
        except CapabilityRuntimeError:
            pass
    finally:
        registry.unregister("broken")


def test_transparent_sync_capability_works_on_async() -> None:
    class _Transparent(Capability):
        name = "transparent"
        priority = 400

        def run(self, ctx, call_next):
            return call_next(ctx)  # pass-through, does not consume the inner result

    registry.register(_Transparent)
    try:
        import asyncio

        @use.transparent()
        async def fn() -> str:
            return "ok"

        assert asyncio.run(fn()) == "ok"
    finally:
        registry.unregister("transparent")
