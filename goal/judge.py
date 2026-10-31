"""Score an assembly against a reference, on the five measures in docs/GOAL.md.

An assembly is a list of pieces, each {"triangles": n×3×3 metres, "family": str}. The judge only ever
sees built geometry, so it can't be told a story. Nothing here is imported by a generator: a
generator that read a reference would score well and mean nothing.
"""
from __future__ import annotations

import json
from collections import Counter
from itertools import permutations, product

import numpy as np

from .reference import DB, extents

GRID = 72  # voxels along the longest side
ALIGN_GRID = 28
RAYS = 240  # per axis
BARS = {"pieces": 0.7, "iou": 0.6, "layers": 0.7, "families": 0.7, "repetition": 0.7}


def load(name) -> list[dict]:
    from .families import family_of
    record = json.loads((DB / f"{name}.json").read_text())
    triangles = np.load(DB / f"{name}.npz")["triangles"].astype(float)
    return [{"triangles": triangles[p["tris"][0]:p["tris"][1]],
             "family": family_of(p["path"][::-1], sorted(p["materials"], key=p["materials"].get, reverse=True))}
            for p in record["pieces"]]


def _bounds(pieces):
    points = np.vstack([p["triangles"].reshape(-1, 3) for p in pieces])
    return points.min(0), points.max(0)


def _surface(triangles, spacing, rng):
    """Points over the triangles, close enough that no voxel of the skin is missed."""
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    area = np.linalg.norm(np.cross(b - a, c - a), axis=1) / 2
    counts = np.clip((area / spacing ** 2 * 6).astype(int), 1, 4000) + 2
    index = np.repeat(np.arange(len(triangles)), counts)
    u, v = rng.random(len(index)), rng.random(len(index))
    flip = u + v > 1
    u[flip], v[flip] = 1 - u[flip], 1 - v[flip]
    return a[index] + (b - a)[index] * u[:, None] + (c - a)[index] * v[:, None]


def _fill(shell):
    """The shell and what it encloses: anything not reachable from the outside. A piece too open to
    enclose anything stays a skin, which is all the model says about it."""
    pad = np.pad(shell, 1)
    outside = np.zeros_like(pad)
    outside[0, :, :] = outside[-1, :, :] = True
    outside[:, 0, :] = outside[:, -1, :] = True
    outside[:, :, 0] = outside[:, :, -1] = True
    outside &= ~pad
    while True:
        grown = outside.copy()
        grown[1:] |= outside[:-1]; grown[:-1] |= outside[1:]
        grown[:, 1:] |= outside[:, :-1]; grown[:, :-1] |= outside[:, 1:]
        grown[:, :, 1:] |= outside[:, :, :-1]; grown[:, :, :-1] |= outside[:, :, 1:]
        grown &= ~pad
        if (grown == outside).all():
            break
        outside = grown
    return (~outside)[1:-1, 1:-1, 1:-1]


def voxels(pieces, low, spacing, shape) -> tuple[np.ndarray, list]:
    """(grid of piece index + 1, 0 is empty, the families). A later piece overwrites an earlier."""
    grid, rng = np.zeros(shape, np.int32), np.random.default_rng(0)
    for index, piece in enumerate(pieces, 1):
        cells = ((_surface(piece["triangles"], spacing, rng) - low) / spacing).astype(int)
        keep = ((cells >= 0) & (cells < shape)).all(1)
        cells = cells[keep]
        if not len(cells):
            continue
        start, stop = cells.min(0), cells.max(0) + 1
        shell = np.zeros(stop - start, bool)
        shell[tuple((cells - start).T)] = True
        region = tuple(slice(a, b) for a, b in zip(start, stop))
        grid[region][_fill(shell)] = index
    return grid, [p["family"] for p in pieces]


def _grid_for(low, high, cells):
    spacing = max(high - low) / cells
    return spacing, np.maximum(np.ceil((high - low) / spacing).astype(int), 1)


def _skin(pieces, low, spacing, shape):
    """Surfaces only, all at once: enough to compare poses, and far cheaper than solids."""
    cells = ((_surface(np.vstack([p["triangles"] for p in pieces]), spacing, np.random.default_rng(0)) - low) / spacing).astype(int)
    grid = np.zeros(shape, bool)
    grid[tuple(cells[((cells >= 0) & (cells < shape)).all(1)].T)] = True
    return grid


def _grown(grid):
    out = grid.copy()
    out[1:] |= grid[:-1]; out[:-1] |= grid[1:]
    out[:, 1:] |= grid[:, :-1]; out[:, :-1] |= grid[:, 1:]
    out[:, :, 1:] |= grid[:, :, :-1]; out[:, :, :-1] |= grid[:, :, 1:]
    return out


def overlap(a, b) -> float:
    """IoU, forgiving one voxel. A sub-voxel shift moves a thin layer a whole cell, and a perfect
    copy read 0.86 without this. Matches are counted both ways as an F score, then turned back
    into IoU, F / (2 - F), so the bar keeps its meaning."""
    total = np.count_nonzero(a) + np.count_nonzero(b)
    if not total:
        return 0.0
    f = (np.count_nonzero(a & _grown(b)) + np.count_nonzero(b & _grown(a))) / total
    return f / (2 - f)


def rotations():
    for order in permutations(range(3)):
        for signs in product((1, -1), repeat=3):
            matrix = np.zeros((3, 3))
            matrix[range(3), order] = signs
            yield matrix


def posed(pieces, matrix, scale=1.0, shift=0.0):
    return [{**p, "triangles": (p["triangles"] @ matrix.T) * scale + shift} for p in pieces]


def align(built, reference):
    """The turn or mirror, one scale and the shift that overlap best. Fair to any frame a generator
    picks: only the drawing fixes a frame, and a section has no north."""
    low, high = _bounds(reference)
    spacing, shape = _grid_for(low, high, ALIGN_GRID)
    target = _skin(reference, low, spacing, shape)
    best, best_score = built, -1.0
    for matrix in rotations():
        turned = posed(built, matrix)
        a, b = _bounds(turned)
        scale = max(high - low) / max(max(b - a), 1e-9)
        shifted = posed(turned, np.eye(3), scale, (low + high) / 2 - (a + b) / 2 * scale)
        mine = _skin(shifted, low, spacing, shape)
        union = np.count_nonzero(mine | target)
        score = np.count_nonzero(mine & target) / union if union else 0.0
        if score > best_score:
            best, best_score = shifted, score
    return best


def _order(column, families):
    """A ray's materials in order, runs collapsed. Air is skipped: a gap has no family."""
    out = []
    for index in column[column > 0]:
        family = families[index - 1]
        if family != "unknown" and (not out or out[-1] != family):
            out.append(family)
    return out


def _common(a, b):
    row = [0] * (len(b) + 1)
    for item in a:
        previous = 0
        for j, other in enumerate(b, 1):
            previous, row[j] = row[j], previous + 1 if item == other else max(row[j], row[j - 1])
    return row[-1]


def layer_score(grid, families, other, other_families) -> float:
    rng, scores = np.random.default_rng(0), []
    for axis in range(3):
        rest = [k for k in range(3) if k != axis]
        for _ in range(RAYS):
            where = [rng.integers(grid.shape[k]) for k in rest]
            pick = [slice(None)] * 3
            pick[rest[0]], pick[rest[1]] = where[0], where[1]
            mine, theirs = _order(grid[tuple(pick)], families), _order(other[tuple(pick)], other_families)
            if theirs:  # scored where the reference says something is
                scores.append(2 * _common(mine, theirs) / (len(mine) + len(theirs)))
    return float(np.mean(scores)) if scores else 0.0


def groups(pieces) -> list[int]:
    """How many pieces share each shape, largest first, counting only real repeats."""
    tally = Counter()
    for piece in pieces:
        sizes = extents(piece["triangles"])[1]
        tally[tuple(round(s * 1000 / 3) for s in sizes)] += 1  # within about 3 mm
    return sorted((n for n in tally.values() if n >= 3), reverse=True)


def repetition_score(built, reference) -> float:
    a, b = groups(built), groups(reference)
    if not b:
        return 1.0 if not a else 0.0
    size = max(len(a), len(b))
    a, b = a + [0] * (size - len(a)), b + [0] * (size - len(b))
    return sum(map(min, a, b)) / sum(map(max, a, b))


def score(built, reference) -> dict:
    if not built:
        return {key: 0.0 for key in BARS} | {"passes": False}
    fitted = align(built, reference)
    low, high = _bounds(reference)
    spacing, shape = _grid_for(low, high, GRID)
    mine, mine_families = voxels(fitted, low, spacing, shape)
    theirs, their_families = voxels(reference, low, spacing, shape)
    wanted = set(their_families) - {"unknown"}
    made = set(mine_families) - {"unknown"}
    result = {"pieces": min(len(built), len(reference)) / max(len(built), len(reference)),
              "iou": overlap(mine > 0, theirs > 0),
              "layers": layer_score(mine, mine_families, theirs, their_families),
              "families": len(wanted & made) / len(wanted) if wanted else 1.0,
              "repetition": repetition_score(built, reference)}
    result = {key: round(float(value), 3) for key, value in result.items()}
    return result | {"precision": round(len(wanted & made) / len(made), 3) if made else 0.0,
                     "passes": all(result[key] >= bar for key, bar in BARS.items())}
