"""Closed regions of a section picture: what a person sees as separate cut parts.

The input is only the picture, so this works on any drawing with closed outlines. Each region is the
white area inside its lines. Anything open to the sheet's edge is outside the assembly.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

from . import hatching

INK = 128
SMALLEST = 24  # pixels: specks where lines nearly meet
HATCHED = 20  # marks taken out before a drawing counts as hatched
TINY = 0.0004  # of the sheet: a cell this small is a fragment, not a part
NEAR = 4  # pixels: fragments this close touch across their ink
GATHER = 4  # fragments that together make a part
INK_RADIUS = 2  # pixels: ink this deep is a heavy line, not an outline
INK_SMALLEST = 0.00012  # of the sheet: smaller heavy marks are arrowheads and the like


@dataclass
class Region:
    id: int
    rows: dict  # y -> [(x0, x1), ...], x1 exclusive
    box: tuple  # x0, y0, x1, y1
    area: int

    @property
    def width(self):
        return self.box[2] - self.box[0]

    @property
    def height(self):
        return self.box[3] - self.box[1]

    def bands(self, tolerance=2) -> list[tuple]:
        """The region as rectangles: rows with the same spans merge, so a brick is one box."""
        out, open_bands = [], []
        for y in sorted(self.rows):
            spans, carried = sorted(self.rows[y]), []
            for x0, x1 in spans:
                match = next((b for b in open_bands if abs(b[0] - x0) <= tolerance and abs(b[1] - x1) <= tolerance
                              and b[3] == y), None)
                if match:
                    open_bands.remove(match)
                    carried.append((match[0], match[1], match[2], y + 1))
                else:
                    carried.append((x0, x1, y, y + 1))
            out.extend(open_bands)
            open_bands = carried
        return out + open_bands


def _runs(row):
    edges = np.flatnonzero(np.diff(np.concatenate(([0], row.astype(np.int8), [0]))))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist()))


def _gathered(regions, sheet) -> list[Region]:
    """A hatched thin layer comes through as a row of tiny cells: they are one part. Touching tiny
    cells join up, and a tiny cell left alone is a mark, so it goes. Only done on a hatched drawing."""
    tiny = [r for r in regions if r.area < TINY * sheet]
    parent = list(range(len(tiny)))

    def root(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i, a in enumerate(tiny):
        for j, b in enumerate(tiny[:i]):
            if a.box[0] <= b.box[2] + NEAR and b.box[0] <= a.box[2] + NEAR and a.box[1] <= b.box[3] + NEAR and b.box[1] <= a.box[3] + NEAR:
                parent[root(i)] = root(j)
    groups: dict = {}
    for i, region in enumerate(tiny):
        groups.setdefault(root(i), []).append(region)
    out = [r for r in regions if r.area >= TINY * sheet]
    for members in groups.values():
        if len(members) < GATHER:
            continue
        rows: dict = {}
        for member in members:
            for y, spans in member.rows.items():
                rows.setdefault(y, []).extend(spans)
        boxes = np.array([m.box for m in members])
        out.append(Region(0, rows, (int(boxes[:, 0].min()), int(boxes[:, 1].min()), int(boxes[:, 2].max()), int(boxes[:, 3].max())),
                          sum(m.area for m in members)))
    return out


def find(picture) -> tuple[list[Region], tuple]:
    """(regions largest first, (width, height)). White pixels, joined by row runs."""
    white = np.asarray(Image.open(picture).convert("L")) >= INK
    white, taken = hatching.remove(white)
    hatched = taken["specks"] + taken["hatch_lines"] >= HATCHED
    height, width = white.shape
    parent: list[int] = []

    def root(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    labelled, previous = [], []
    for y in range(height):
        current = []
        for x0, x1 in _runs(white[y]):
            label = None
            for px0, px1, other in previous:
                if px0 < x1 and x0 < px1:
                    if label is None:
                        label = root(other)
                    else:
                        parent[root(other)] = label
            if label is None:
                label = len(parent)
                parent.append(label)
            current.append((x0, x1, label))
            labelled.append((y, x0, x1, label))
        previous = current
    groups: dict = {}
    open_to_edge = set()
    for y, x0, x1, label in labelled:
        label = root(label)
        groups.setdefault(label, {}).setdefault(y, []).append((x0, x1))
        if y in (0, height - 1) or x0 == 0 or x1 == width:
            open_to_edge.add(label)
    regions = []
    for label, rows in groups.items():
        if label in open_to_edge:
            continue
        area = sum(b - a for spans in rows.values() for a, b in spans)
        if area < SMALLEST:
            continue
        xs = [v for spans in rows.values() for span in spans for v in span]
        regions.append(Region(0, rows, (min(xs), min(rows), max(xs), max(rows) + 1), area))
    if hatched:
        regions = _gathered(regions, width * height)
    regions.sort(key=lambda r: -r.area)
    for number, region in enumerate(regions, 1):
        region.id = number
    return regions, (width, height)


def with_ink(picture) -> tuple[list[Region], tuple]:
    """The regions, plus lines drawn heavy enough to be parts themselves: on a section, membranes and
    flashings are often solid black, with no white inside to find."""
    found, size = find(picture)
    ink = np.asarray(Image.open(picture).convert("L")) < INK
    thick = ink.copy()
    for _ in range(INK_RADIUS):
        thick = thick & np.roll(thick, 1, 0) & np.roll(thick, -1, 0) & np.roll(thick, 1, 1) & np.roll(thick, -1, 1)
    for _ in range(INK_RADIUS):
        thick = thick | np.roll(thick, 1, 0) | np.roll(thick, -1, 0) | np.roll(thick, 1, 1) | np.roll(thick, -1, 1)
    thick &= ink
    number = len(found)
    for ys, xs in hatching._components(thick):
        if len(ys) < INK_SMALLEST * size[0] * size[1]:
            continue
        rows: dict = {}
        for y in np.unique(ys):
            mask = np.zeros(size[0] + 2, bool)
            mask[xs[ys == y] + 1] = True
            rows[int(y)] = [(a - 1, b - 1) for a, b in _runs(mask)]
        number += 1
        found.append(Region(number, rows, (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1), len(ys)))
    return found, size
