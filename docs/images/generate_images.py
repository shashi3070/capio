"""Generate the PNG diagrams used by README.md.

Run from this directory:
    python generate_images.py

Produces architecture.png and pipeline.png. Requires Pillow.
"""

from __future__ import annotations

import math
import os
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont

FONT_DIR = r"C:\Windows\Fonts"

BLUE = "#2563eb"
BLUE_FILL = "#eff6ff"
LIGHT = "#f8fafc"
DARK = "#0f172a"
SLATE = "#475569"
GRAY = "#94a3b8"
GREEN = "#16a34a"
GREEN_FILL = "#f0fdf4"
ORANGE = "#ea580c"
ORANGE_FILL = "#fff7ed"
PURPLE = "#7c3aed"
PURPLE_FILL = "#f5f3ff"

WHITE = "#ffffff"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "arialbd.ttf" if bold else "arial.ttf"
    return ImageFont.truetype(os.path.join(FONT_DIR, name), size)


def text_center(
    d: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    lines: List[str],
    size: int = 16,
    bold: bool = False,
    fill: str = DARK,
    spacing: int = 4,
) -> None:
    f = font(size, bold)
    widths = [d.textbbox((0, 0), ln, font=f)[2] - d.textbbox((0, 0), ln, font=f)[0] for ln in lines]
    heights = [
        d.textbbox((0, 0), ln, font=f)[3] - d.textbbox((0, 0), ln, font=f)[1] for ln in lines
    ]
    total_h = sum(heights) + spacing * (len(lines) - 1)
    y = cy - total_h / 2
    for ln, w, h in zip(lines, widths, heights):
        d.text((cx - w / 2, y), ln, font=f, fill=fill)
        y += h + spacing


def box(
    d: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    fill: str,
    outline: str,
    lines: List[str],
    size: int = 16,
    bold: bool = False,
    text_fill: str = DARK,
    radius: int = 12,
) -> None:
    d.rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=fill, outline=outline, width=2)
    text_center(d, x + w / 2, y + h / 2, lines, size=size, bold=bold, fill=text_fill)


def arrow(
    d: ImageDraw.ImageDraw,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    color: str = SLATE,
    width: int = 3,
    head: int = 11,
) -> None:
    d.line([x1, y1, x2, y2], fill=color, width=width)
    ang = math.atan2(y2 - y1, x2 - x1)
    d.polygon(
        [
            (x2, y2),
            (x2 - head * math.cos(ang - 0.42), y2 - head * math.sin(ang - 0.42)),
            (x2 - head * math.cos(ang + 0.42), y2 - head * math.sin(ang + 0.42)),
        ],
        fill=color,
    )


def title_bar(d: ImageDraw.ImageDraw, title: str, subtitle: str, w: int) -> None:
    text_center(d, w / 2, 34, [title], size=30, bold=True, fill=BLUE)
    text_center(d, w / 2, 62, [subtitle], size=15, fill=SLATE)


def make_architecture() -> None:
    W, H = 1100, 780
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.ImageDraw(img)

    title_bar(d, "Capio Architecture", "one decorated function, one lazy-built pipeline, every invocation", W)

    # application layer
    box(d, 250, 90, 600, 64, BLUE_FILL, BLUE,
        ["Your application", "@use.retry(...)  @use.cache(...)  @use({...})  (sync & async)"],
        size=16, bold=True)
    arrow(d, 550, 154, 550, 190)

    # use facade
    box(d, 250, 190, 600, 66, LIGHT, DARK,
        ["use facade", "use.<name>(**options)  |  use(*names, **options)  |  use.context()  |  unwrap() / pipeline()"],
        size=15)
    arrow(d, 550, 256, 550, 292)

    # runtime
    box(d, 250, 292, 600, 80, LIGHT, DARK,
        ["CapioRuntime", "config (env / profile / strict)  ·  registry  ·  service container  ·  event bus  ·  pipeline cache"],
        size=15)
    arrow(d, 550, 372, 550, 408)

    # pipeline
    box(d, 250, 408, 600, 72, LIGHT, DARK,
        ["ExecutionPipeline  (built lazily on first call, memoized)", "steps outer-first  ·  kind = sync | async | sync_gen | async_gen"],
        size=15)
    arrow(d, 550, 480, 550, 516)

    # engine
    box(d, 250, 516, 600, 62, LIGHT, DARK,
        ["Engine", "execute_sync / execute_async  ·  build_context()  ·  recursive call_next(ctx)"],
        size=15)
    arrow(d, 550, 578, 550, 614)

    # capabilities + backends
    box(d, 130, 614, 400, 70, GREEN_FILL, GREEN,
        ["Capabilities (8)", "rate_limit · circuit_breaker · cache · retry\n"
        "timeout · trace · metrics · log"],
        size=15, bold=True)
    box(d, 570, 614, 400, 70, ORANGE_FILL, ORANGE,
        ["Backends", "cache.memory  ·  trace.console\nmetrics.null  ·  log.stdio"],
        size=15, bold=True)
    arrow(d, 460, 649, 570, 649)
    d.text((515, 636), "use", font=font(13, True), fill=SLATE)

    # side: context
    box(d, 60, 430, 140, 120, PURPLE_FILL, PURPLE,
        ["Context", "invocation_id", "trace_id · span_id", "env · strict",
         "deadline · result"], size=13)
    arrow(d, 250, 500, 200, 490, color=PURPLE)

    # side: events
    box(d, 900, 430, 140, 120, BLUE_FILL, BLUE,
        ["EventBus", "cache.hit", "retry.attempt", "circuit.open",
         "rate.limited"], size=13)
    arrow(d, 900, 490, 850, 480, color=BLUE)

    img.save(os.path.join(os.path.dirname(__file__), "architecture.png"))


def make_pipeline() -> None:
    W, H = 900, 760
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.ImageDraw(img)

    title_bar(d, "Capio Pipeline", "ordered outermost-first; composite form sorts by priority", W)

    rows: List[Tuple[str, str, str, str]] = [
        ("850", "rate_limit", "admission control per key", BLUE_FILL, BLUE),
        ("800", "circuit_breaker", "fail fast when unhealthy", ORANGE_FILL, ORANGE),
        ("750", "cache", "TTL + stampede protection", GREEN_FILL, GREEN),
        ("700", "retry", "backoff + jitter", PURPLE_FILL, PURPLE),
        ("650", "timeout", "bound execution time", BLUE_FILL, BLUE),
        ("600", "trace", "span recording", GREEN_FILL, GREEN),
        ("550", "log", "structured records", ORANGE_FILL, ORANGE),
        ("500", "metrics", "counters + histograms", PURPLE_FILL, PURPLE),
    ]

    y = 96
    row_h = 46
    x = 120
    w = 660

    for prio, name, desc, fill, out in rows:
        box(d, x, y, 90, row_h, WHITE, GRAY, [prio], size=15)
        box(d, x + 100, y, 250, row_h, fill, out, [name], size=15, bold=True)
        box(d, x + 360, y, w - 360, row_h, LIGHT, DARK, [desc], size=14)
        arrow(d, x + 45, y + row_h, x + 45, y + row_h + 12)
        y += row_h + 12

    box(d, x, y, w, 52, DARK, DARK, ["your function (sync / async / generator)"], size=16, bold=True, text_fill=WHITE)

    # call direction note
    arrow(d, 60, y + 26, x - 14, y + 26)
    text_center(d, 62, y - 34, ["call"], size=14, bold=True, fill=SLATE)
    arrow(d, x + w + 14, y + 26, 840, y + 26)
    text_center(d, 838, y - 34, ["result"], size=14, bold=True, fill=SLATE)

    img.save(os.path.join(os.path.dirname(__file__), "pipeline.png"))


if __name__ == "__main__":
    make_architecture()
    make_pipeline()
    print("wrote architecture.png and pipeline.png")
