"""Take hatching out of a drawing, so that what is left are the parts' outlines.

Hatching says what a material is, and it isn't an outline. Left in, each brick hatch line cuts a
brick into strips and each aggregate mark punches a hole in the concrete. Square lines are kept, since
outlines mostly run square. Of the rest, two kinds go: marks touching no outline, and long lines
that come as a big parallel family. A sloped edge stays, because it has no family.
"""
from __future__ import annotations

import numpy as np

RUN_SHARE = 0.012  # a square line, as a share of the drawing's longest side
SPECK_SHARE = 0.03  # a mark this small, touching no outline, is texture
FAMILY = 5  # parallel lines this many are a hatch
BIN = 12  # degrees


def _along(ink, length, axis):
    """Ink on a straight run at least `length` long, by shifting and ANDing."""
    kept = ink.copy()
    for _ in range(length - 1):
        kept = kept & np.roll(kept, 1, axis)
    out = kept.copy()
    for _ in range(length - 1):
        out = out | np.roll(out, -1, axis)
    return out


def _components(mask) -> list[tuple]:
    """(ys, xs) of each group of touching pixels, diagonals included, by row runs."""
    parent, previous, cells = [], [], []

    def root(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for y in np.flatnonzero(mask.any(1)):
        row = mask[y]
        edges = np.flatnonzero(np.diff(np.concatenate(([0], row.astype(np.int8), [0]))))
        current = []
        for x0, x1 in zip(edges[::2].tolist(), edges[1::2].tolist()):
            label = None
            for px0, px1, other, py in previous:
                if py == y - 1 and px0 <= x1 and x0 <= px1:
                    if label is None:
                        label = root(other)
                    else:
                        parent[root(other)] = label
            if label is None:
                label = len(parent)
                parent.append(label)
            current.append((x0, x1, label, y))
            cells.append((y, x0, x1, label))
        previous = current
    groups: dict = {}
    for y, x0, x1, label in cells:
        groups.setdefault(root(label), []).append((y, x0, x1))
    out = []
    for spans in groups.values():
        ys = np.concatenate([np.full(b - a, y) for y, a, b in spans])
        xs = np.concatenate([np.arange(a, b) for _, a, b in spans])
        out.append((ys, xs))
    return out


def _angle(ys, xs) -> float:
    """Direction of a stroke in degrees, 0 to 180."""
    a = np.stack([xs - xs.mean(), ys - ys.mean()])
    values, vectors = np.linalg.eigh(a @ a.T)
    vector = vectors[:, 1]
    return float(np.degrees(np.arctan2(vector[1], vector[0])) % 180)


def remove(white) -> tuple[np.ndarray, dict]:
    """(the picture without its hatching, what was taken). A drawing with none is returned as it is."""
    ink = ~white
    height, width = white.shape
    longest = max(height, width)
    run = max(8, int(longest * RUN_SHARE))
    square = _along(ink, run, 0) | _along(ink, run, 1)
    grown = square | np.roll(square, 1, 0) | np.roll(square, -1, 0) | np.roll(square, 1, 1) | np.roll(square, -1, 1)
    grown = grown | np.roll(grown, 1, 0) | np.roll(grown, -1, 0) | np.roll(grown, 1, 1) | np.roll(grown, -1, 1)
    loose = ink & ~grown
    reach = grown | np.roll(grown, 1, 0) | np.roll(grown, -1, 0) | np.roll(grown, 1, 1) | np.roll(grown, -1, 1)
    specks, strokes = 0, {}
    erase = np.zeros_like(ink)
    for ys, xs in _components(loose):
        size = max(np.ptp(ys), np.ptp(xs)) + 1
        if size < longest * SPECK_SHARE:
            if not reach[ys, xs].any():
                erase[ys, xs] = True
                specks += 1
            continue
        strokes.setdefault(int(_angle(ys, xs) // BIN) % (180 // BIN), []).append((ys, xs))
    hatched = 0
    for members in strokes.values():
        if len(members) >= FAMILY:
            for ys, xs in members:
                erase[ys, xs] = True
                hatched += 1
    return white | erase, {"specks": specks, "hatch_lines": hatched}
