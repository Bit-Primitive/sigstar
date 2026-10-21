"""Small dependency-free SVG line charts for reports.

Colours are CSS variables with light-mode fallbacks, so a chart inlined in the HTML report
follows its light/dark theme and a standalone .svg file still renders. Marks follow the
report's chart rules: 2px lines, hairline recessive grid, >=8px markers with a surface
ring, a <title> on every marker and point for hover, text in ink colours, not series ones.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from html import escape

WIDTH, HEIGHT = 640, 220
PAD_L, PAD_R, PAD_T, PAD_B = 64, 16, 16, 28


@dataclass
class Series:
    points: list[tuple[date, float]]
    color_var: str  # e.g. "--series-1"
    fallback: str
    label: str
    dashed: bool = False


@dataclass
class Band:
    points: list[tuple[date, float, float]]  # day, low, high
    color_var: str
    fallback: str
    label: str


@dataclass
class Marker:
    day: date
    value: float
    label: str  # shown on hover and next to the marker


@dataclass
class Chart:
    title: str
    series: list[Series]
    fmt: Callable[[float], str]
    bands: list[Band] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)


def _nice_ticks(lo: float, hi: float, count: int = 4) -> list[float]:
    if hi <= lo:
        return [lo]
    step = (hi - lo) / count
    magnitude = 10 ** math.floor(math.log10(step))
    for mult in (1, 2, 2.5, 5, 10):
        if magnitude * mult >= step:
            step = magnitude * mult
            break
    first = (lo // step + 1) * step
    ticks = []
    value = first
    while value < hi:
        ticks.append(value)
        value += step
    return ticks or [lo]


def render(chart: Chart) -> str:
    days = [d for s in chart.series for d, _ in s.points]
    days += [b[0] for band in chart.bands for b in band.points]
    values = [v for s in chart.series for _, v in s.points]
    values += [x for band in chart.bands for _, lo, hi in band.points for x in (lo, hi)]
    if not days or not values:
        return ""
    d0, d1 = min(days), max(days)
    lo, hi = min(values), max(values)
    pad = (hi - lo) * 0.08 or abs(hi) * 0.05 or 1.0
    lo, hi = lo - pad, hi + pad
    span_days = max((d1 - d0).days, 1)

    def x(d: date) -> float:
        return PAD_L + (d - d0).days / span_days * (WIDTH - PAD_L - PAD_R)

    def y(v: float) -> float:
        return PAD_T + (hi - v) / (hi - lo) * (HEIGHT - PAD_T - PAD_B)

    ink2 = "var(--text-secondary, #52514e)"
    grid = "var(--grid, #e4e3df)"
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'role="img" aria-label="{escape(chart.title)}" class="chart" '
        f'font-family="system-ui, sans-serif" font-size="11">',
        f"<title>{escape(chart.title)}</title>",
    ]
    for tick in _nice_ticks(lo, hi):
        ty = y(tick)
        parts.append(
            f'<line x1="{PAD_L}" x2="{WIDTH - PAD_R}" y1="{ty:.1f}" y2="{ty:.1f}" '
            f'stroke="{grid}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{PAD_L - 6}" y="{ty + 4:.1f}" text-anchor="end" fill="{ink2}">'
            f"{escape(chart.fmt(tick))}</text>"
        )
    for d, anchor in ((d0, "start"), (d1, "end")):
        parts.append(
            f'<text x="{x(d):.1f}" y="{HEIGHT - 8}" text-anchor="{anchor}" fill="{ink2}">'
            f"{d.isoformat()}</text>"
        )
    for band in chart.bands:
        upper = " ".join(f"{x(d):.1f},{y(h):.1f}" for d, _, h in band.points)
        lower = " ".join(f"{x(d):.1f},{y(lo_):.1f}" for d, lo_, _ in reversed(band.points))
        parts.append(
            f'<polygon points="{upper} {lower}" fill="var({band.color_var}, {band.fallback})" '
            f'fill-opacity="0.18"><title>{escape(band.label)}</title></polygon>'
        )
    for s in chart.series:
        path = " ".join(f"{x(d):.1f},{y(v):.1f}" for d, v in s.points)
        dash = ' stroke-dasharray="5 4"' if s.dashed else ""
        parts.append(
            f'<polyline points="{path}" fill="none" stroke="var({s.color_var}, {s.fallback})" '
            f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"{dash}/>'
        )
        # Invisible, larger hit targets carry each point's value on hover.
        for d, v in s.points:
            parts.append(
                f'<circle cx="{x(d):.1f}" cy="{y(v):.1f}" r="6" fill="transparent">'
                f"<title>{escape(s.label)} {d.isoformat()}: {escape(chart.fmt(v))}</title></circle>"
            )
    for m in chart.markers:
        mx, my = x(m.day), y(m.value)
        parts.append(
            f'<circle cx="{mx:.1f}" cy="{my:.1f}" r="5" fill="var(--series-2, #eb6834)" '
            f'stroke="var(--surface-1, #fcfcfb)" stroke-width="2">'
            f"<title>{escape(m.label)}</title></circle>"
        )
        anchor = "end" if mx > WIDTH * 0.8 else "start"
        offset = -8 if anchor == "end" else 8
        parts.append(
            f'<text x="{mx + offset:.1f}" y="{my - 8:.1f}" text-anchor="{anchor}" '
            f'fill="var(--text-primary, #0b0b0b)">{escape(m.label)}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)
