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

from .families import FAMILIES
from .reference import DB, extents

FAMILY_INDEX = {family: number for number, family in enumerate(FAMILIES)}
GRID = 72  # voxels along the longest side
ALIGN_GRID = 28
CANDIDATES = 8  # turns compared as solids
SAME_FIT = 0.05  # poses this close in overlap are a tie, broken by materials
SCALES = (0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15)  # a generator's overall size is a choice too
RAYS = 240  # per axis
BARS = {"pieces": 0.7, "iou": 0.6, "layers": 0.7, "families": 0.7, "repetition": 0.7}


def load(name) -> list[dict]:
    from .families import spread
    record = json.loads((DB / f"{name}.json").read_text())
    triangles = np.load(DB / f"{name}.npz")["triangles"].astype(float)
    families = spread(record["pieces"])
    return [{"triangles": triangles[p["tris"][0]:p["tris"][1]], "family": family}
            for p, family in zip(record["pieces"], families)]


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


def _best_shift(mine, target):
    """The whole-voxel shift of `mine` that overlaps `target` most, every shift at once by FFT."""
    size = [a + b for a, b in zip(mine.shape, target.shape)]
    match = np.fft.irfftn(np.fft.rfftn(target.astype(float), size) * np.conj(np.fft.rfftn(mine.astype(float), size)), size)
    peak = np.array(np.unravel_index(np.argmax(match), match.shape))
    return np.where(peak > np.array(size) // 2, peak - np.array(size), peak)


def _moved(grid, shift):
    out = np.zeros_like(grid)
    source = tuple(slice(max(-s, 0), max(n - s, 0) if s > 0 else n) for s, n in zip(shift, grid.shape))
    place = tuple(slice(max(s, 0), n + min(s, 0)) for s, n in zip(shift, grid.shape))
    out[place] = grid[source]
    return out


def align(built, reference):
    """The turn or mirror, one scale and the shift that overlap best. Fair to any frame a generator
    picks: only the drawing fixes a frame, and a section has no north. Centring by bounding boxes
    pushed anything shorter than the reference off by several voxels, so the shift is searched."""
    low, high = _bounds(reference)
    spacing, shape = _grid_for(low, high, ALIGN_GRID)
    target = _skin(reference, low, spacing, shape)
    poses = []
    for number, matrix in enumerate(rotations()):
        turned = posed(built, matrix)
        a, b = _bounds(turned)
        for stretch in SCALES:
            scale = stretch * max(high - low) / max(max(b - a), 1e-9)
            pose = posed(turned, np.eye(3), scale, (low + high) / 2 - (a + b) / 2 * scale)
            mine = _skin(pose, low, spacing, shape)
            shift = _best_shift(mine, target)
            poses.append((overlap(_moved(mine, shift), target), number, stretch, pose, shift * spacing))
    fine, cells = _grid_for(low, high, GRID)
    theirs, their_families = voxels(reference, low, fine, cells)
    solid = theirs > 0
    scored = []
    each = {}  # the best scale of each turn: a near-symmetric detail ties many, so scales can't crowd out turns
    for item in sorted(poses, key=lambda item: item[0]):
        each[item[1]] = item
    ranked = sorted(each.values(), key=lambda item: item[0], reverse=True)
    # Turns that fit equally go through together: on a symmetric detail the true one is among many.
    turns = [item[1] for k, item in enumerate(ranked) if k < CANDIDATES or item[0] >= ranked[0][0] - SAME_FIT]
    # Skins tie across scales, and the true size must not lose a tie, so it is tried first.
    trials = [item for turn in turns for item in poses if item[1] == turn and item[2] == 1.0]
    trials += [each[turn] for turn in turns if each[turn][2] != 1.0]
    for _, _, _, pose, shift in trials:
        pose = posed(pose, np.eye(3), 1.0, shift)
        grid, families = voxels(pose, low, fine, cells)
        step = _best_shift(grid > 0, solid)
        grid = _moved(grid, step)
        scored.append((overlap(grid > 0, solid), _agreement(grid, families, theirs, their_families),
                       posed(pose, np.eye(3), 1.0, step * fine)))
    # A wall overlaps its own mirror image almost perfectly, with every material on the wrong side.
    # So among poses that fit about equally, the one that puts materials in the right place wins.
    top = max(item[0] for item in scored)
    close = [item for item in scored if item[0] >= top - SAME_FIT]
    return max(close, key=lambda item: (item[1], item[0]))[2]


def _agreement(grid, families, other, other_families) -> float:
    """Of the voxels where the reference has a known family, the share holding that family."""
    if not families or not other_families:
        return 0.0
    mine = np.array([-1, *[FAMILY_INDEX[f] for f in families]])[grid]
    theirs = np.array([-1, *[FAMILY_INDEX[f] for f in other_families]])[other]
    known = theirs >= 0
    known &= theirs != FAMILY_INDEX["unknown"]
    return float(np.count_nonzero(mine[known] == theirs[known]) / max(np.count_nonzero(known), 1))


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


def soft_layer_score(grid, families, other, other_families) -> float:
    """A diagnostic, not a bar: a ray may match its neighbours, a voxel off. A perfect copy moved one
    voxel reads 0.65 to 0.87 on the strict measure, so that one partly measures the grid."""
    rng, scores = np.random.default_rng(0), []
    for axis in range(3):
        rest = [k for k in range(3) if k != axis]
        for _ in range(RAYS // 3):
            a, b = rng.integers(grid.shape[rest[0]]), rng.integers(grid.shape[rest[1]])
            pick = [slice(None)] * 3
            pick[rest[0]], pick[rest[1]] = a, b
            theirs = _order(other[tuple(pick)], other_families)
            if not theirs:
                continue
            best = 0.0
            for da, db in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
                near = list(pick)
                near[rest[0]] = int(np.clip(a + da, 0, grid.shape[rest[0]] - 1))
                near[rest[1]] = int(np.clip(b + db, 0, grid.shape[rest[1]] - 1))
                mine = _order(grid[tuple(near)], families)
                best = max(best, 2 * _common(mine, theirs) / (len(mine) + len(theirs)))
            scores.append(best)
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
    return result | {"layers_soft": round(soft_layer_score(mine, mine_families, theirs, their_families), 3), "precision": round(len(wanted & made) / len(made), 3) if made else 0.0,
                     "passes": all(result[key] >= bar for key, bar in BARS.items())}
