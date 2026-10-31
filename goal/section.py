"""Cut a 2D section through a 3D assembly, as a drawing cuts one.

Every section is a slice of a real assembly, so its answer is known. The picture holds only what a
section shows: the cut. It never carries a piece's name or family, and it can't show what the plane
misses, which is the point of the exercise.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

SIDE = 1400
MARGIN = 40


def cut_triangles(triangles, axis, position) -> np.ndarray:
    """Segments where triangles cross the plane, as m×2×3. A triangle with an edge lying in the
    plane is left to its neighbours: nudging the plane keeps that rare and harmless."""
    position = float(position) + 1e-7
    side = triangles[:, :, axis] - position
    crossing = (side.min(1) < 0) & (side.max(1) > 0)
    out = np.empty((crossing.sum(), 2, 3))
    for row, (tri, s) in enumerate(zip(triangles[crossing], side[crossing])):
        points = []
        for a, b in ((0, 1), (1, 2), (2, 0)):
            if (s[a] < 0) != (s[b] < 0):
                points.append(tri[a] + (tri[b] - tri[a]) * s[a] / (s[a] - s[b]))
        out[row] = points[:2]
    return out


def best_plane(pieces, triangles) -> tuple[int, float]:
    """The upright plane cutting the most pieces, as a section is drawn to show most. x and y only:
    z would be a plan."""
    box = np.array([p["box"] for p in pieces])
    best = (-1, 0, 0.0)
    for axis in (0, 1):
        low, high = box[:, axis].min(), box[:, axis].max()
        for share in (0.2, 0.35, 0.5, 0.65, 0.8):
            position = low + (high - low) * share
            hits = int(np.count_nonzero((box[:, axis] < position) & (box[:, axis + 3] > position)))
            if hits > best[0]:
                best = (hits, axis, float(position))
    return best[1], best[2]


def section(pieces, triangles, axis, position) -> list[tuple[int, np.ndarray]]:
    """[(piece index, its segments)] for the pieces the plane cuts."""
    out = []
    for index, piece in enumerate(pieces):
        if piece["box"][axis] < position < piece["box"][axis + 3]:
            segments = cut_triangles(triangles[piece["tris"][0]:piece["tris"][1]], axis, position)
            if len(segments):
                out.append((index, segments))
    return out


def draw(cuts, axis, path) -> None:
    """Black lines on white. The up axis is z, the other is across the sheet."""
    across = 1 - axis
    every = np.vstack([segments for _, segments in cuts])[:, :, [across, 2]]
    low, high = every.reshape(-1, 2).min(0), every.reshape(-1, 2).max(0)
    scale = (SIDE - 2 * MARGIN) / max(high - low)
    size = ((high - low) * scale + 2 * MARGIN).astype(int) + 1
    image = Image.new("L", tuple(size), 255)
    pen = ImageDraw.Draw(image)
    points = (every - low) * scale + MARGIN
    points[:, :, 1] = size[1] - points[:, :, 1]
    for a, b in points:
        pen.line([tuple(a), tuple(b)], fill=0, width=2)
    image.save(path)


def extruded(cuts, axis, low, high) -> list[dict]:
    """The floor every method must beat: push each cut along the depth, knowing nothing. It is the
    prism that started this project."""
    out = []
    for _, segments in cuts:
        a, b = segments.copy(), segments.copy()
        a[:, :, axis], b[:, :, axis] = low, high
        walls = np.concatenate([np.stack([a[:, 0], a[:, 1], b[:, 1]], 1), np.stack([a[:, 0], b[:, 1], b[:, 0]], 1)])
        out.append({"triangles": walls, "family": "unknown"})
    return out
