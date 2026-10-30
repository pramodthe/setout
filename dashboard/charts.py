"""Inline SVG marks, written out by hand: no chart library and no external script.

Specs follow the dataviz method: 2 px lines, hairline grid, one axis per chart, a legend for two or
more series, selective end labels. Sizes are real pixels at the viewBox width, so each chart comes
in a wide and a narrow drawing and CSS shows the one that fits; scaling one drawing down would
shrink its text. Colours are CSS classes, so light and dark mode each pick their own steps.
page.js adds the crosshair and tooltip from the JSON beside the charts.
"""
import json
import math
from html import escape

WIDE, NARROW = (520, 264), (340, 232)
MARGIN_LEFT, MARGIN_RIGHT, MARGIN_TOP, MARGIN_BOTTOM = 36, 96, 14, 34
MIN_LABEL_GAP = 16  # end labels closer than this are dropped: the legend and tooltip carry them


def nice_ticks(top):
    """Clean y ticks from 0 to at least `top`."""
    if top <= 0:
        return [0.0, 1.0]
    power = 10 ** math.floor(math.log10(top))
    step = next((s * power for s in (0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10) if top / (s * power) <= 4), 10 * power)
    count = math.ceil(top / step - 1e-9)
    return [round(i * step, 10) for i in range(count + 1)]


def x_ticks(first, last):
    """Pass numbers to label: the first, then round numbers."""
    span = max(last - first, 1)
    step = next(s for s in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000) if span / s <= 6)
    ticks = [first] + [v for v in range(step, last + 1, step) if v > first]
    return ticks[1:] if len(ticks) > 2 and ticks[1] - ticks[0] < step / 2 else ticks


def _tick(value):
    return f"{value:g}"


def _drawing(chart_id, xs, series, ticks, size, x_top, best, label):
    width, height = size
    left, right, top, bottom = MARGIN_LEFT, width - MARGIN_RIGHT, MARGIN_TOP, height - MARGIN_BOTTOM
    first, last = xs[0], max(x_top or xs[-1], xs[-1], xs[0] + 1)
    scale = (right - left) / (last - first)
    sx = lambda x: left + (x - first) * scale  # noqa: E731
    sy = lambda y: bottom - y / ticks[-1] * (bottom - top)  # noqa: E731
    out = [f'<svg class="plot" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(label)}" '
           f'tabindex="0" data-chart="{chart_id}" data-left="{left}" data-right="{right}" '
           f'data-first="{first}" data-scale="{scale:.5f}">']
    for tick in ticks:
        out.append(f'<line class="{"axis" if tick == 0 else "grid"}" x1="{left}" x2="{right}" '
                   f'y1="{sy(tick):.1f}" y2="{sy(tick):.1f}"/>')
        out.append(f'<text class="tick" x="{left - 6}" y="{sy(tick) + 4:.1f}" text-anchor="end">{_tick(tick)}</text>')
    for x in x_ticks(first, last):
        out.append(f'<text class="tick" x="{sx(x):.1f}" y="{bottom + 16}" text-anchor="middle">{x}</text>')
    out.append(f'<text class="tick" x="{right}" y="{height - 4}" text-anchor="end">Pass</text>')
    ends = []
    for item in series:
        points = [(x, v) for x, v in zip(xs, item["values"]) if v is not None]
        if not points:
            continue
        path = " ".join(f"{'M' if i == 0 else 'L'}{sx(x):.1f} {sy(v):.1f}" for i, (x, v) in enumerate(points))
        out.append(f'<path class="line {item["cls"]}" d="{path}"/>')
        ends.append((item, points[-1]))
    labelled = sorted(sy(v) for _, (_, v) in ends)
    apart = all(b - a >= MIN_LABEL_GAP for a, b in zip(labelled, labelled[1:]))
    for item, (x, v) in ends:
        out.append(f'<circle class="dot {item["cls"]}" cx="{sx(x):.1f}" cy="{sy(v):.1f}" r="4"/>')
        if apart:
            out.append(f'<text class="end" x="{sx(x) + 8:.1f}" y="{sy(v) + 4:.1f}">'
                       f'{escape(item["name"])} {item.get("fmt", "{:.3f}").format(v)}</text>')
    if best:
        x, value, text = best
        out.append(f'<circle class="best" cx="{sx(x):.1f}" cy="{sy(value):.1f}" r="5"/>')
        anchor = "end" if sx(x) > (left + right) / 2 else "start"
        out.append(f'<text class="end" x="{sx(x):.1f}" y="{sy(value) + 20:.1f}" text-anchor="{anchor}">{escape(text)}</text>')
    out.append(f'<line class="crosshair" x1="0" x2="0" y1="{top}" y2="{bottom}" visibility="hidden"/>')
    out.append(f'<rect class="hit" x="{left}" y="{top}" width="{right - left}" height="{bottom - top}"/></svg>')
    return "".join(out)


def line_chart(chart_id, xs, series, ticks, x_top=None, best=None, label=""):
    """A line chart in both sizes. Series are {"name", "cls", "values", "fmt"}, aligned with xs."""
    return (f'<div class="chart-wide">{_drawing(chart_id, xs, series, ticks, WIDE, x_top, best, label)}</div>'
            f'<div class="chart-narrow">{_drawing(chart_id, xs, series, ticks, NARROW, x_top, best, label)}</div>')


def legend(series):
    keys = "".join(f'<li><i class="key {s["cls"]}"></i>{escape(s["name"])}</li>' for s in series)
    return f'<ul class="legend">{keys}</ul>'


def chart_json(charts):
    """The tooltip data, safe inside a <script> element."""
    return json.dumps(charts, separators=(",", ":")).replace("<", "\\u003c")


def sparkline(values, y_top=1.0, width=128, height=30):
    """One class over the passes, on a shared 0..1 scale: de-emphasis line, accent end dot."""
    points = [(i, v) for i, v in enumerate(values) if v is not None]
    if not points:
        return ""
    pad = 4
    span = max(len(values) - 1, 1)
    xy = [(pad + i / span * (width - 2 * pad), height - pad - v / y_top * (height - 2 * pad)) for i, v in points]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f} {y:.1f}" for i, (x, y) in enumerate(xy))
    x, y = xy[-1]
    return (f'<svg class="spark" viewBox="0 0 {width} {height}" width="{width}" height="{height}" aria-hidden="true">'
            f'<line class="grid" x1="{pad}" x2="{width - pad}" y1="{height - pad}" y2="{height - pad}"/>'
            f'<path class="spark-line" d="{path}"/><circle class="spark-dot" cx="{x:.1f}" cy="{y:.1f}" r="3"/></svg>')


def dumbbell(before, after, width=112, height=20):
    """Before and after on a 0..1 track. Either side may be missing; then only the other is drawn."""
    pad = 7
    sx = lambda v: pad + v * (width - 2 * pad)  # noqa: E731
    y = height / 2
    parts = [f'<line class="grid" x1="{pad}" x2="{width - pad}" y1="{y}" y2="{y}"/>']
    if before is not None and after is not None:
        parts.append(f'<line class="link" x1="{sx(before):.1f}" x2="{sx(after):.1f}" y1="{y}" y2="{y}"/>')
    if before is not None:
        parts.append(f'<circle class="before" cx="{sx(before):.1f}" cy="{y}" r="4"/>')
    if after is not None:
        parts.append(f'<circle class="after" cx="{sx(after):.1f}" cy="{y}" r="4"/>')
    return (f'<svg class="dumbbell" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
            f'aria-hidden="true">{"".join(parts)}</svg>')


def bar(value):
    """A thin in-cell bar on a 0..1 scale; the number beside it carries the exact value."""
    if value is None:
        return ""
    return f'<span class="bar"><i style="width:{max(0.0, min(1.0, value)) * 100:.1f}%"></i></span>'
