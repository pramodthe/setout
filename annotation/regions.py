"""Regions of a sheet: every closed area a person might want to name.

One planar arrangement holds all the linework: hatch boundaries, closed polylines and
plain lines, with annotation left out so it cannot chop anything. The arrangement's
faces are the atoms. A hatch or closed polyline owns the faces inside it (the smallest
one wins), so a stray line across a hatch splits nothing, a closed part inside another
leaves a hole, and a hatch laid over its own outline counts once. A face no closed area
owns is a region of its own when a plain line helps bound it. Lines left over, bounding
nothing, are single-line layers: their stroke becomes the region.

Rules never delete. A region they doubt stays in the list with `dropped` set to the
reason, so a reviewer can bring it back with one key.

Every tolerance is a share of text height: text is the one thing drawn at a known paper
size, so this holds on not-to-scale sheets. Nothing reads a part's name, or a layer.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from app import synthetic

from . import planar
from .drawing import Curve, Drawing

# All as shares of text height.
EPS = 0.001  # the same point, give or take number noise
NEAR = 0.2  # a drafting miss: an end that stops short in any direction
FAR = 1.0  # a gap along a line: a dash gap, or an end pointing at its target
MARK = 3.0  # the largest a drawing symbol gets (level triangle, tag)
TIP_MARK = 0.6  # the largest a leader's end marker gets
MIN_STROKE = 2.0  # a single line shorter than this is a tick, not a layer
SPECK = 0.25  # a face with a smaller side than this, squared, is dust at a corner
UNDER_THE_PEN = 0.75  # a face narrower than this share of its line's stroke is hidden by ink
LONG_SIDE = 0.25  # a line must bound this share of a face's outline to hide it
MIN_PEN_MM = 0.13  # the thinnest line a render draws: one pixel at 200 dpi
FALLBACK_TEXT_OF_SPAN = 1.0 / 150.0  # a sheet with no text: text height as a share of its span

FRAME = "a frame"  # how the rules name one, so the detail step can read it

MAX_WIDTH_EDGES = 600  # widths are measured on at most this many edges
GAP_ROUNDS = 5  # rounds of gap closing, stopping early when one adds nothing


@dataclass
class Tolerance:
    eps: float
    near: float
    far: float
    mark: float
    tip_mark: float
    min_stroke: float
    speck: float

    @classmethod
    def of(cls, drawing: Drawing) -> Tolerance:
        height = drawing.text_height
        if not height:
            box = drawing.geometry_box()
            span = max(box[2] - box[0], box[3] - box[1]) if box else 1.0
            height = max(span, 1e-6) * FALLBACK_TEXT_OF_SPAN
        return cls(height * EPS, height * NEAR, height * FAR, height * MARK, height * TIP_MARK,
                   height * MIN_STROKE, height * SPECK)


@dataclass
class Region:
    id: str
    kind: str  # hatch | loop | lines | stroke
    rings: list  # drawing units: outer rings counter-clockwise, holes clockwise
    handles: list  # the DXF entities it came from
    hatch: dict | None = None  # {"pattern", "scale", "angle"}, verbatim
    linetypes: list = field(default_factory=list)
    lineweight: float | None = None  # mm
    anchor: tuple = (0.0, 0.0)  # the middle of its largest inscribed circle
    width: float = 0.0  # median cross-section, drawing units
    area: float = 0.0
    dropped: str | None = None  # why the rules set it aside, or None


@dataclass
class Result:
    regions: list
    tolerance: Tolerance
    bridges: int  # gap-closing segments added
    excluded: dict  # {reason: entity handles} left out of the arrangement


# ---------------------------------------------------------------- what is not linework

def _is_circular(ring) -> bool:
    area = abs(planar.ring_area(ring))
    perimeter = float(np.sum(np.hypot(*(np.roll(ring, -1, axis=0) - ring).T)))
    return perimeter > 0 and 4.0 * math.pi * area / perimeter ** 2 > 0.9


def _tip_marks(curves, hatches, callouts, tol) -> tuple[set, set]:
    """Curve and hatch indices that are a leader's end marker: a dot centred on the tip,
    or a short tick centred on it. An arrowhead is a SOLID, which is never linework. A
    filled part under a tip stays: a marker is round or a single line, and small."""
    tips = np.array([c.tip for c in callouts], dtype=float).reshape(-1, 2)
    curve_marks, hatch_marks = set(), set()
    if not len(tips):
        return curve_marks, hatch_marks
    for k, hatch in enumerate(hatches):
        if hatch.pattern.upper() != "SOLID" or len(hatch.rings) != 1:
            continue
        ring = hatch.rings[0]
        low, high = ring.min(axis=0), ring.max(axis=0)
        if max(high - low) > tol.tip_mark * 2.0 or not _is_circular(ring):
            continue
        centre = (low + high) / 2.0
        if np.min(np.hypot(*(tips - centre).T)) <= max(high - low) * 0.25:
            hatch_marks.add(k)
    for k, curve in enumerate(curves):
        if curve.closed or len(curve.points) != 2:
            continue
        middle = curve.points.mean(axis=0)
        length = float(np.hypot(*(curve.points[1] - curve.points[0])))
        if length <= tol.tip_mark * 3.0 and np.min(np.hypot(*(tips - middle).T)) <= length * 0.25:
            curve_marks.add(k)
    return curve_marks, hatch_marks


def _is_break_symbol(points) -> bool:
    """A break line: a straight run that leaves its line in a sharp zigzag and comes back
    to the same line. A stepped profile never comes back; a hat section has no sharp turn."""
    if len(points) < 5:
        return False
    first = points[1] - points[0]
    last = points[-1] - points[-2]
    chord = points[-1] - points[0]
    length = float(np.hypot(*chord))
    a, b = float(np.hypot(*first)), float(np.hypot(*last))
    if length <= 0 or a <= 0 or b <= 0:
        return False
    unit = chord / length
    if abs(unit @ (first / a)) < 0.995 or abs(unit @ (last / b)) < 0.995:
        return False  # ends must run along the same line
    off = (points - points[0]) @ np.array([-unit[1], unit[0]])
    if abs(off[1]) > length * 0.02 or abs(off[-2]) > length * 0.02 or np.max(np.abs(off)) > length * 0.4:
        return False
    steps = np.diff(points, axis=0)
    lengths = np.hypot(steps[:, 0], steps[:, 1])
    if np.any(lengths <= 0):
        return False
    units = steps / lengths[:, None]
    turns = np.sum(units[:-1] * units[1:], axis=1)
    return bool(np.any(turns < -0.2))  # sharper than about 100 degrees


def _segments(points, closed):
    return (points, np.roll(points, -1, axis=0)) if closed else (points[:-1], points[1:])


def _through_lines(curves, tol) -> set:
    """Open lines that cross several others and run on past them at both ends: level,
    centre and section lines, which must not split what they cross.

    A part's edge stops at what it meets, so a crossing only counts where the other line
    carries on both ways: a neighbour overshooting an edge by a drafting miss is no
    crossing. The worst offender goes first, so two level lines across one edge never
    condemn the edge.
    """
    pieces = [_segments(c.points, c.closed) for c in curves]
    if not pieces:
        return set()
    a = np.concatenate([p for p, _ in pieces])
    b = np.concatenate([q for _, q in pieces])
    owner = np.concatenate([np.full(len(p), k) for k, (p, _) in enumerate(pieces)])
    lengths = np.hypot(*(b - a).T)
    start = np.cumsum([0] + [len(p) for p, _ in pieces])
    before = np.concatenate([np.concatenate([[0.0], np.cumsum(lengths[s:e])[:-1]])
                             for s, e in zip(start[:-1], start[1:])])
    total = np.array([lengths[s:e].sum() for s, e in zip(start[:-1], start[1:])])
    closed = np.array([c.closed for c in curves])
    i, j = planar._close_pairs(a, b, 0.0)
    if not len(i):
        return set()
    keep = owner[i] != owner[j]
    i, j = i[keep], j[keep]
    di, dj = b[i] - a[i], b[j] - a[j]
    den = di[:, 0] * dj[:, 1] - di[:, 1] * dj[:, 0]
    safe = np.where(np.abs(den) > 1e-12, den, 1.0)
    wx, wy = a[j, 0] - a[i, 0], a[j, 1] - a[i, 1]
    t = (wx * dj[:, 1] - wy * dj[:, 0]) / safe
    u = (wx * di[:, 1] - wy * di[:, 0]) / safe
    crossing = (np.abs(den) > 1e-12) & (t > 0) & (t < 1) & (u > 0) & (u < 1)
    i, j, t, u = i[crossing], j[crossing], t[crossing], u[crossing]
    at_i = before[i] + t * lengths[i]
    at_j = before[j] + u * lengths[j]

    def carries_on(segment, at):
        curve = owner[segment]
        return closed[curve] | ((at >= tol.far) & (total[curve] - at >= tol.far))

    rows = [(owner[i], owner[j], at_i, carries_on(j, at_j)), (owner[j], owner[i], at_j, carries_on(i, at_i))]
    hits = {}
    for curve, other, at, counts in rows:
        for c, o, position in zip(curve[counts].tolist(), other[counts].tolist(), at[counts].tolist()):
            if not curves[c].closed:
                hits.setdefault(c, []).append((position, o))
    out = set()
    while True:
        worst, most = None, 1
        for c, found in hits.items():
            if c in out:
                continue
            live = [position for position, o in found if o not in out]
            if len(live) > most and min(live) >= tol.far and total[c] - max(live) >= tol.far:
                worst, most = c, len(live)
        if worst is None:
            return out
        out.add(worst)


def _past_the_drawing(curves, arrangement, tol) -> set:
    """Open lines that leave the drawing proper on opposite sides: a level or datum line
    that lies along edges crosses nothing, but no part runs right past everything. A line
    off to one side stays: it may be a part. The drawing proper is what encloses something
    once gaps are closed, so overhangs do not stretch it."""
    box = arrangement.drawing_box(tol.mark)
    if box is None:
        return set()
    low, high = np.array(box[:2]) - tol.near, np.array(box[2:]) + tol.near

    def spans(a, b) -> bool:
        return bool(np.any((a < low) & (b > high)) or np.any((b < low) & (a > high)))

    return {k for k, c in enumerate(curves) if not c.closed and spans(c.points[0], c.points[-1])}


# ---------------------------------------------------------------- closed areas

@dataclass
class _Area:
    rings: list
    size: float  # even-odd area
    hatches: list = field(default_factory=list)  # indices into the drawing's hatches
    loops: list = field(default_factory=list)  # indices into the drawing's curves
    box: tuple = (0.0, 0.0, 0.0, 0.0)


def _even_odd_area(rings) -> float:
    if len(rings) == 1:
        return abs(planar.ring_area(rings[0]))
    total = 0.0
    for k, ring in enumerate(rings):
        others = [r for m, r in enumerate(rings) if m != k]
        depth = sum(1 for other in others if planar.contains(other, ring[0]))
        total += abs(planar.ring_area(ring)) * (1 if depth % 2 == 0 else -1)
    return abs(total)


def _box(rings) -> tuple:
    pts = np.concatenate(rings)
    return tuple(float(v) for v in (*pts.min(axis=0), *pts.max(axis=0)))


def _areas(curves, hatches, tol) -> list[_Area]:
    """Closed areas, smallest first. A hatch and the outline it fills are one area."""
    areas = []
    for k, hatch in enumerate(hatches):
        size = _even_odd_area(hatch.rings)
        if size > 0:
            areas.append(_Area(hatch.rings, size, hatches=[k], box=_box(hatch.rings)))
    for k, curve in enumerate(curves):
        if curve.closed and len(curve.points) >= 3:
            size = abs(planar.ring_area(curve.points))
            if size > 0:
                areas.append(_Area([curve.points], size, loops=[k], box=_box([curve.points])))
    areas.sort(key=lambda a: a.size)
    merged: list[_Area] = []
    for area in areas:
        same = next((m for m in merged[-8:] if abs(m.size - area.size) <= tol.eps * math.sqrt(m.size) * 4.0
                     and all(abs(p - q) <= tol.eps * 2.0 for p, q in zip(m.box, area.box))), None)
        if same is None:
            merged.append(area)
        else:
            same.hatches += area.hatches
            same.loops += area.loops
    return merged


# ---------------------------------------------------------------- the arrangement

def _inputs(curves, hatches):
    """Segments of everything. A source id is a curve index, or len(curves) + hatch index."""
    starts, ends, sources = [], [], []
    for k, curve in enumerate(curves):
        p, q = _segments(curve.points, curve.closed)
        starts.append(p)
        ends.append(q)
        sources.append(np.full(len(p), k))
    for k, hatch in enumerate(hatches):
        for ring in hatch.rings:
            p, q = _segments(ring, True)
            starts.append(p)
            ends.append(q)
            sources.append(np.full(len(p), len(curves) + k))
    if not starts:
        return np.empty((0, 2)), np.empty((0, 2)), np.empty(0, dtype=np.int64)
    return np.concatenate(starts), np.concatenate(ends), np.concatenate(sources)


def _arrange(curves, hatches, tol):
    """The arrangement, with drafting gaps closed by extra segments (source -1)."""
    a, b, src = _inputs(curves, hatches)
    arrangement = planar.Arrangement(a, b, tol.eps, src)
    bridges = 0
    for _ in range(GAP_ROUNDS):  # three ends round a gap: two must join before the third has a target
        extra = planar.gap_bridges(arrangement, tol.near, tol.far, min_stub=tol.near)
        if not len(extra):
            break
        bridges += len(extra)
        a = np.concatenate([a, extra[:, 0]])
        b = np.concatenate([b, extra[:, 1]])
        src = np.concatenate([src, np.full(len(extra), -1)])
        arrangement = planar.Arrangement(a, b, tol.eps, src)
    return arrangement, bridges


def _owners(atoms_at, areas) -> np.ndarray:
    """Per face, the index of the smallest area holding it, or -1."""
    owner = np.full(len(atoms_at), -1, dtype=np.int64)
    if not len(atoms_at):
        return owner
    for index, area in enumerate(areas):  # smallest first: the first to claim a face keeps it
        x0, y0, x1, y1 = area.box
        free = np.flatnonzero((owner < 0) & (atoms_at[:, 0] >= x0) & (atoms_at[:, 0] <= x1)
                              & (atoms_at[:, 1] >= y0) & (atoms_at[:, 1] <= y1))
        if len(free):
            owner[free[planar.points_in_rings(area.rings, atoms_at[free])]] = index
    return owner


# ---------------------------------------------------------------- single lines

def _stroke_ring(points, half) -> np.ndarray | None:
    """A polyline thickened to a polygon: both sides offset, with mitred joints."""
    keep = np.append(True, np.hypot(*np.diff(points, axis=0).T) > 1e-12)
    points = points[keep]
    if len(points) < 2:
        return None
    steps = np.diff(points, axis=0)
    units = steps / np.hypot(steps[:, 0], steps[:, 1])[:, None]
    normals = np.stack([-units[:, 1], units[:, 0]], axis=1)
    joints = np.empty_like(points)
    joints[0], joints[-1] = normals[0], normals[-1]
    if len(points) > 2:
        mean = normals[:-1] + normals[1:]
        size = np.hypot(mean[:, 0], mean[:, 1])
        mean = mean / np.where(size > 1e-9, size, 1.0)[:, None]
        along = np.sum(mean * normals[1:], axis=1)
        joints[1:-1] = mean / np.maximum(along, 0.4)[:, None]  # limit the mitre at sharp folds
    left, right = points + joints * half, points - joints * half
    return np.concatenate([right, left[::-1]])


def _stroke_paths(curves, candidates, tol) -> list[list[int]]:
    """Group line pieces drawn end to end in one style, so a layer drawn as many LINEs
    comes out as one path. Pieces meet at a shared end, or across a gap they point along.
    Two ends join only when each is the other's nearest, so a junction of three stays
    apart: separate things meet there."""
    if not candidates:
        return []
    style = {k: (curves[k].linetype, round(curves[k].lineweight, 3), round(curves[k].drawn_width, 6))
             for k in candidates}
    ends = {k: (curves[k].points[0], curves[k].points[-1]) for k in candidates}
    parent = {k: k for k in candidates}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    join = tol.far * 1.05
    points = np.array([p for k in candidates for p in ends[k]])
    owners = [k for k in candidates for _ in (0, 1)]
    i, j = planar._close_pairs(points, points, join)
    links = {}
    for a, b in zip(i.tolist(), j.tolist()):
        if owners[a] == owners[b] or style[owners[a]] != style[owners[b]]:
            continue
        gap = float(np.hypot(*(points[a] - points[b])))
        if gap > join:
            continue
        step = tol.eps * 2.0
        if gap > step and not _aligned(curves[owners[a]], a % 2, curves[owners[b]], b % 2, points[a], points[b]):
            continue
        links.setdefault(a, []).append((gap, b))
        links.setdefault(b, []).append((gap, a))
    for a, options in links.items():
        best = min(options)[1]
        if min(links.get(best, [(0, -1)]))[1] == a:  # both ends pick each other
            parent[find(owners[a])] = find(owners[best])
    groups: dict[int, list[int]] = {}
    for k in candidates:
        groups.setdefault(find(k), []).append(k)
    return list(groups.values())


def _tangent(curve, end):
    p = curve.points
    d = (p[-1] - p[-2]) if end else (p[0] - p[1])
    return d / max(float(np.hypot(*d)), 1e-12)


def _aligned(curve_a, end_a, curve_b, end_b, a, b) -> bool:
    gap = b - a
    size = float(np.hypot(*gap))
    if size <= 0:
        return True
    unit = gap / size
    return bool(_tangent(curve_a, end_a) @ unit > 0.9 and _tangent(curve_b, end_b) @ -unit > 0.9)


def _order_path(curves, group, tol) -> np.ndarray:
    """The pieces of a group as one point list, nearest end following nearest end."""
    left = list(group)
    if len(left) == 1:
        return curves[left[0]].points
    ends = np.array([p for k in left for p in (curves[k].points[0], curves[k].points[-1])])
    apart = np.hypot(*(ends[:, None] - ends[None, :]).transpose(2, 0, 1))
    np.fill_diagonal(apart, np.inf)
    lonely = np.flatnonzero(apart.min(axis=1) > tol.far * 1.05)  # the path's two ends
    first = int(lonely[0]) if len(lonely) else 0
    piece = left.pop(first // 2)
    path = curves[piece].points if first % 2 == 0 else curves[piece].points[::-1]
    while left:
        tail = path[-1]
        best, best_end, best_gap = None, 0, None
        for k in left:
            for end in (0, 1):
                gap = float(np.hypot(*(curves[k].points[-end] - tail)))
                if best_gap is None or gap < best_gap:
                    best, best_end, best_gap = k, end, gap
        left.remove(best)
        points = curves[best].points if best_end == 0 else curves[best].points[::-1]
        path = np.concatenate([path, points[1:] if best_gap <= tol.eps * 2.0 else points])
    return path


def _pen(curve, drawing) -> float:
    """How wide a line is drawn, in drawing units."""
    return curve.drawn_width or max(curve.lineweight, MIN_PEN_MM) * drawing.units_per_paper_mm


def _hidden_by(face, width, widths, curves, drawing, arrangement, lengths) -> Curve | None:
    """The line whose ink covers this face, if any. A face is hidden when it is narrower
    than the finest line around it, or when a line along one of its long sides has narrow
    faces on both sides: that is one line drawn across a narrow gap, and both faces are
    really its stroke. A thin part beside a heavy neighbour stays, because the neighbour's
    line has the neighbour on its other side."""
    edges = arrangement.face_edges(face)
    shares: dict[int, list] = {}
    for edge in edges.tolist():
        for source in arrangement.sources_of(edge).tolist():
            if 0 <= source < len(curves):
                shares.setdefault(source, []).append(edge)
    if not shares:
        return None
    finest = min(shares, key=lambda k: _pen(curves[k], drawing))
    if width <= UNDER_THE_PEN * _pen(curves[finest], drawing):
        return curves[finest]
    perimeter = float(lengths[edges].sum())
    for source in sorted(shares, key=lambda k: -_pen(curves[k], drawing)):
        reach = UNDER_THE_PEN * _pen(curves[source], drawing)
        if curves[source].closed or width > reach:
            continue
        if float(lengths[shares[source]].sum()) < LONG_SIDE * perimeter:
            continue
        beside = arrangement.across(face, shares[source])
        if all(other < 0 or widths.get(other, math.inf) <= reach for other in beside):
            return curves[source]
    return None


def _strokes(curves, used_edge, arrangement, drawing, tol) -> list[Region]:
    """Lines left over: mostly not part of any region's outline."""
    edges_of: dict[int, list[int]] = {}
    edge_ids, sources = arrangement.edge_sources()
    for edge, source in zip(edge_ids.tolist(), sources.tolist()):
        if 0 <= source < len(curves):
            edges_of.setdefault(source, []).append(edge)
    lengths = arrangement.edge_lengths() if len(arrangement.edges) else np.empty(0)
    candidates = []
    for k, curve in enumerate(curves):
        if curve.closed or k not in edges_of:
            continue
        ids = np.array(edges_of[k])
        total = float(lengths[ids].sum())
        free = float(lengths[ids[~used_edge[ids]]].sum())
        if total > 0 and free >= 0.5 * total:
            candidates.append(k)
    regions = []
    for group in _stroke_paths(curves, candidates, tol):
        path = _order_path(curves, group, tol)
        length = float(np.sum(np.hypot(*np.diff(path, axis=0).T)))
        if length < tol.min_stroke:
            continue
        sample = curves[group[0]]
        width = _pen(sample, drawing)
        ring = _stroke_ring(path, width / 2.0)
        if ring is None:
            continue
        region = Region(id="", kind="stroke", rings=[ring], handles=sorted({curves[k].handle for k in group}),
                        linetypes=sorted({curves[k].linetype for k in group}), lineweight=sample.lineweight)
        region.area = abs(planar.ring_area(ring))
        region.width = width
        region.anchor = tuple(path[len(path) // 2]) if len(path) % 2 else tuple(
            (path[len(path) // 2 - 1] + path[len(path) // 2]) / 2.0)
        regions.append(region)
    return regions


# ---------------------------------------------------------------- triage

def _triage(regions, drawing, tol) -> None:
    """Set aside what is plainly not a part, and say why."""
    tips = np.array([c.tip for c in drawing.callouts], dtype=float).reshape(-1, 2)
    live = [r for r in regions if not r.dropped]
    anchors = np.array([r.anchor for r in live], dtype=float).reshape(-1, 2)
    for region in live:
        if region.hatch or region.kind == "stroke":
            continue
        outer = max(region.rings, key=lambda r: abs(planar.ring_area(r)))
        held = int(np.count_nonzero(planar.points_in_rings([outer], anchors))) - 1
        if held >= 3 and (held >= 0.5 * (len(live) - 1) or _is_box(outer)):
            region.dropped = f"{FRAME}: it encloses {held} of the {len(live) - 1} other regions"
    for region in live:
        if region.dropped or region.hatch or region.kind == "stroke":
            continue
        low, high = np.concatenate(region.rings).min(axis=0), np.concatenate(region.rings).max(axis=0)
        if max(high - low) > tol.mark:
            continue
        if len(tips) and np.any(planar.points_in_rings(region.rings, tips)):
            continue
        others = [r for r in live if r is not region and not r.dropped]
        if others:
            rings = [ring for r in others for ring in r.rings]
            clear = planar.distance_to_rings(rings, np.concatenate(region.rings))
            inside = planar.points_in_rings(rings, np.concatenate(region.rings))
            if np.min(clear) <= tol.near or np.any(inside):
                continue
        region.dropped = "a drawing symbol: small, closed and touching nothing"


def _is_box(ring) -> bool:
    """Four corners, square to the sheet."""
    ring = planar.simplify_ring(ring, 1e-9)
    if len(ring) != 4:
        return False
    steps = np.roll(ring, -1, axis=0) - ring
    return bool(np.all(np.min(np.abs(steps), axis=1) <= 1e-6 * np.max(np.abs(steps))))


def width_of(rings) -> float:
    """The region's median cross-section (the thin rule's measure), on at most
    MAX_WIDTH_EDGES edges so a long curved outline stays cheap."""
    total = sum(len(r) for r in rings)
    if total > MAX_WIDTH_EDGES:
        step = math.ceil(total / MAX_WIDTH_EDGES)
        rings = [r[::step] if len(r) // step >= 3 else r for r in rings]
    return float(synthetic.region_width([np.asarray(r, dtype=float) for r in rings])[0])


def anchor_of(rings) -> tuple:
    x, y, _ = synthetic.inscribed_circle([np.asarray(r, dtype=float) for r in rings])
    return (x, y)


def _finish(region: Region, tol) -> None:
    region.rings = [planar.simplify_ring(r, tol.eps) for r in region.rings]
    region.rings = [r for r in region.rings if len(r) >= 3]
    if region.kind != "stroke" and region.rings:
        region.area = abs(planar.rings_area(region.rings))
        region.anchor = anchor_of(region.rings)
        region.width = width_of(region.rings)


# ---------------------------------------------------------------- the whole step

def find_regions(drawing: Drawing) -> Result:
    tol = Tolerance.of(drawing)
    curves, hatches = list(drawing.curves), list(drawing.hatches)
    excluded = {}
    tick_ids, dot_ids = _tip_marks(curves, hatches, drawing.callouts, tol)
    if dot_ids:
        excluded["leader end marker"] = sorted({hatches[k].handle for k in dot_ids})
    if tick_ids:
        excluded.setdefault("leader end marker", []).extend(sorted({curves[k].handle for k in tick_ids}))
    breaks = {k for k, c in enumerate(curves) if not c.closed and _is_break_symbol(c.points)}
    if breaks:
        excluded["break symbol"] = sorted({curves[k].handle for k in breaks})
    hatches = [h for k, h in enumerate(hatches) if k not in dot_ids]
    curves = [c for k, c in enumerate(curves) if k not in tick_ids and k not in breaks]
    through = _through_lines(curves, tol)
    if through:
        excluded["line crossing others, running on past them"] = sorted({curves[k].handle for k in through})
    curves = [c for k, c in enumerate(curves) if k not in through]
    arrangement, bridges = _arrange(curves, hatches, tol)
    past = _past_the_drawing(curves, arrangement, tol)
    if past:
        excluded["line running past the whole drawing at both ends"] = sorted({curves[k].handle for k in past})
        curves = [c for k, c in enumerate(curves) if k not in past]
        arrangement, bridges = _arrange(curves, hatches, tol)

    areas = _areas(curves, hatches, tol)
    faces = arrangement.faces
    atoms_at = (np.array([planar.interior_point(arrangement.face_rings(f)) for f in faces], dtype=float)
                if faces else np.empty((0, 2)))
    owner = _owners(atoms_at, areas)

    regions: list[Region] = []
    used_edge = np.zeros(len(arrangement.edges), dtype=bool)
    lengths = arrangement.edge_lengths() if len(arrangement.edges) else np.empty(0)
    for index, area in enumerate(areas):
        atoms = np.flatnonzero(owner == index)
        if not len(atoms):
            continue
        for face in atoms.tolist():
            used_edge[arrangement.face_edges(faces[face])] = True
        rings = arrangement.union_rings(atoms.tolist())
        regions.append(_area_region(area, rings, curves, hatches))
    free_faces = np.flatnonzero(owner < 0).tolist()
    widths = {face: width_of(arrangement.face_rings(faces[face])) for face in free_faces}
    for face in free_faces:
        edges = arrangement.face_edges(faces[face])
        drawn = _face_sources(arrangement, edges, len(curves))
        rings = arrangement.face_rings(faces[face])
        lines = [curves[k] for k in drawn if not curves[k].closed]
        region = Region(id="", kind="lines", rings=rings, handles=sorted({curves[k].handle for k in drawn}),
                        linetypes=sorted({c.linetype for c in lines}),
                        lineweight=max((c.lineweight for c in lines), default=None))
        pen = _hidden_by(faces[face], widths[face], widths, curves, drawing, arrangement, lengths)
        if not lines and not _has_bridge(arrangement, edges):
            region.dropped = "a gap between other parts: no line of its own bounds it"
        elif abs(planar.rings_area(rings)) < tol.speck ** 2:
            region.dropped = "a speck where lines miss each other"
        elif pen is not None:
            region.dropped = f"under the ink of a {pen.lineweight:g} mm line"
        else:
            used_edge[edges] = True
        regions.append(region)

    regions += _strokes(curves, used_edge, arrangement, drawing, tol)
    for region in regions:
        _finish(region, tol)
    regions = [r for r in regions if r.rings and r.area > 0]
    _triage(regions, drawing, tol)
    band = max(tol.mark, 1e-9)
    regions.sort(key=lambda r: (r.dropped is not None, -round(r.anchor[1] / band), r.anchor[0]))
    for k, region in enumerate(regions, 1):
        region.id = f"r{k}"
    return Result(regions, tol, bridges, excluded)


def _area_region(area: _Area, rings, curves, hatches) -> Region:
    kind = "hatch" if area.hatches else "loop"
    patterned = sorted((hatches[k] for k in area.hatches), key=lambda h: h.pattern.upper() == "SOLID")
    hatch = None
    if patterned:
        top = patterned[0]
        hatch = {"pattern": top.pattern, "scale": top.scale, "angle": top.angle}
    loops = [curves[k] for k in area.loops]
    return Region(id="", kind=kind, rings=rings,
                  handles=sorted({h.handle for h in patterned} | {c.handle for c in loops}), hatch=hatch,
                  linetypes=sorted({c.linetype for c in loops}),
                  lineweight=max((c.lineweight for c in loops), default=None))


def _face_sources(arrangement, edges, curve_count) -> list[int]:
    found = set()
    for edge in edges.tolist():
        found.update(s for s in arrangement.sources_of(edge).tolist() if 0 <= s < curve_count)
    return sorted(found)


def _has_bridge(arrangement, edges) -> bool:
    return any(-1 in arrangement.sources_of(edge).tolist() for edge in edges.tolist())

