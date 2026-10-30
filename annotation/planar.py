"""Planar faces from line segments, in numpy.

Given segments in drawing units, `Arrangement` cuts them at every crossing and touch,
peels off what bounds nothing, and traces the bounded faces with their holes.
`union_rings` gives the outline of any set of faces, which is how a region made of
several faces gets one outline, and how the review page merges and splits: both are
another arrangement over a region's rings.

The cutting is snap rounding (Hobby; Guibas and Marimont). Comparing floats within a
tolerance cannot be made consistent: two points level along a line can swap between
neighbouring segments, an edge ends up lying over a shorter chain, and the walk around a
vertex falls apart. So the topology is worked out on an integer grid of cell `eps`. Every
end and crossing makes its cell hot, every segment is routed through the hot cells it
touches, and the rounds repeat until the edges stop changing. Points move by under one
`eps`, which is number noise, far below any line width.

`gap_bridges` finds the short segments that would close a drafting gap at a free line
end. It adds them and moves nothing: two sides of a thin layer can be closer together
than any useful gap, so snapping ends together would fold thin parts flat.

No planar-graph library: the job is small (cut, peel, walk), and adding one would mean a
compiled dependency for about 300 lines of array code.

Rings are (n, 2) arrays, not closed by repeating the first vertex. An outer ring runs
counter-clockwise and a hole clockwise, so a region always lies on a ring's left.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

LEAF = 48  # segments in a quadtree cell before all its pairs are tested
MAX_DEPTH = 14
MAX_CUTS = 6  # rounds of snapping before a tangle is left as it is
BACKWARD = -0.1  # cosine limit: a free end may reach sideways, never back along its own line


def _cross(ax, ay, bx, by):
    return ax * by - ay * bx


def ring_area(ring) -> float:
    """Signed area: positive when counter-clockwise."""
    p = np.asarray(ring, dtype=float)
    q = np.roll(p, -1, axis=0)
    return float(np.sum(p[:, 0] * q[:, 1] - q[:, 0] * p[:, 1]) / 2.0)


def rings_area(rings) -> float:
    """Net area of a region's rings, holes taken off."""
    return sum(ring_area(ring) for ring in rings)


def _close_pairs(a, b, margin, split=None) -> tuple[np.ndarray, np.ndarray]:
    """Index pairs (i < j) of segments whose boxes come within `margin`, through a
    quadtree: long segments only enter the cells their line passes through. With
    `split`, only pairs with i below it and j at or above it."""
    n = len(a)
    if n < 2:
        empty = np.empty(0, dtype=np.int64)
        return empty, empty
    lo, hi = np.minimum(a, b) - margin, np.maximum(a, b) + margin
    found = []
    stack = [(np.arange(n), lo.min(axis=0), hi.max(axis=0), 0)]
    while stack:
        idx, low, high, depth = stack.pop()
        if len(idx) < 2:
            continue
        # a cell not much wider than the margin cannot part anything: every box overlaps all
        # four children, and going on would visit 4^depth cells to find the same pairs
        if len(idx) <= LEAF or depth >= MAX_DEPTH or np.max(high - low) <= 4.0 * margin:
            i, j = np.triu_indices(len(idx), 1)
            found.append(np.stack([idx[i], idx[j]], axis=1))
            continue
        mid = (low + high) / 2.0
        for qx in (0, 1):
            for qy in (0, 1):
                box_lo = np.array([low[0] if qx == 0 else mid[0], low[1] if qy == 0 else mid[1]])
                box_hi = np.array([mid[0] if qx == 0 else high[0], mid[1] if qy == 0 else high[1]])
                keep = np.all(lo[idx] <= box_hi, axis=1) & np.all(hi[idx] >= box_lo, axis=1)
                sub = idx[keep]
                if len(sub):
                    # the segment's line must pass the cell: its corners are not all on one side
                    d = b[sub] - a[sub]
                    corners = np.array([[box_lo[0], box_lo[1]], [box_hi[0], box_lo[1]],
                                        [box_hi[0], box_hi[1]], [box_lo[0], box_hi[1]]])
                    side = (_cross(d[:, 0, None], d[:, 1, None], corners[:, 0] - a[sub, 0, None],
                                   corners[:, 1] - a[sub, 1, None]))
                    reach = (np.abs(d[:, 0]) + np.abs(d[:, 1])) * margin * 2.0
                    passes = (side.min(axis=1) <= reach) & (side.max(axis=1) >= -reach)
                    stack.append((sub[passes], box_lo, box_hi, depth + 1))
    if not found:
        empty = np.empty(0, dtype=np.int64)
        return empty, empty
    pairs = np.concatenate(found)
    pairs.sort(axis=1)
    key = np.unique(pairs[:, 0] * n + pairs[:, 1])
    i, j = key // n, key % n
    near = np.all(lo[i] <= hi[j], axis=1) & np.all(lo[j] <= hi[i], axis=1)
    if split is not None:
        near &= (i < split) & (j >= split)
    return i[near], j[near]


def _hot_cells(a, b) -> tuple[np.ndarray, np.ndarray]:
    """(cells, cell of each point in a then b): the integer cells of every segment end and
    crossing, in grid units. Crossings get no entry of their own in the second array."""
    n = len(a)
    d = b - a
    i, j = _close_pairs(a, b, 1.0)
    crossings = np.empty((0, 2))
    if len(i):
        di, dj = d[i], d[j]
        den = _cross(di[:, 0], di[:, 1], dj[:, 0], dj[:, 1])
        size = np.hypot(di[:, 0], di[:, 1]) * np.hypot(dj[:, 0], dj[:, 1])
        usable = np.abs(den) > 1e-12 * size
        safe = np.where(usable, den, 1.0)
        qx, qy = a[j, 0] - a[i, 0], a[j, 1] - a[i, 1]
        t = _cross(qx, qy, dj[:, 0], dj[:, 1]) / safe
        u = _cross(qx, qy, di[:, 0], di[:, 1]) / safe
        hit = usable & (t >= 0) & (t <= 1) & (u >= 0) & (u <= 1)
        crossings = a[i[hit]] + t[hit, None] * di[hit]
    cells = np.rint(np.concatenate([a, b, crossings])).astype(np.int64)
    low = cells.min(axis=0)
    wide = int(cells[:, 1].max() - low[1]) + 1
    keys, which = np.unique((cells[:, 0] - low[0]) * wide + (cells[:, 1] - low[1]), return_inverse=True)
    hot = np.stack([keys // wide + low[0], keys % wide + low[1]], axis=1)
    return hot, which[: 2 * n]


def _through_cells(a, b, hot) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(segment, cell, t) for every hot cell a segment passes through, t being the middle
    of its stay in the cell. Stays in different cells never overlap, so sorting by t
    cannot fold a segment back on itself."""
    n = len(a)
    centres = hot.astype(float)
    seg, cell = _close_pairs(np.concatenate([a, centres]), np.concatenate([b, centres]), 0.5, split=n)
    cell = cell - n
    start, step = a[seg], b[seg] - a[seg]
    enter = np.full(len(seg), -np.inf)
    leave = np.full(len(seg), np.inf)
    inside = np.ones(len(seg), dtype=bool)
    for axis in (0, 1):
        moving = step[:, axis] != 0
        low, high = centres[cell, axis] - 0.5, centres[cell, axis] + 0.5
        inside &= moving | ((start[:, axis] >= low) & (start[:, axis] <= high))
        run = np.where(moving, step[:, axis], 1.0)
        first, second = (low - start[:, axis]) / run, (high - start[:, axis]) / run
        enter = np.where(moving, np.maximum(enter, np.minimum(first, second)), enter)
        leave = np.where(moving, np.minimum(leave, np.maximum(first, second)), leave)
    hit = inside & (enter <= leave) & (leave >= 0) & (enter <= 1)
    return seg[hit], cell[hit], np.clip((enter[hit] + leave[hit]) / 2.0, 0.0, 1.0)


def _merge_touching(hot) -> tuple[np.ndarray, np.ndarray]:
    """(cells kept, new index of each cell). Hot cells that touch are one point: two ends
    a hair apart can round either side of a cell edge, and rounding must not open a gap
    that the drawing does not have."""
    n = len(hot)
    low = hot.min(axis=0)
    wide = int(hot[:, 1].max() - low[1]) + 3  # room for y + 1 and y - 1
    key = (hot[:, 0] - low[0]) * wide + (hot[:, 1] - low[1]) + 1
    order = np.argsort(key)
    sorted_key = key[order]
    labels = np.arange(n)
    left, right = [], []
    for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
        probe = key + dx * wide + dy
        at = np.minimum(np.searchsorted(sorted_key, probe), n - 1)
        found = sorted_key[at] == probe
        left.append(np.flatnonzero(found))
        right.append(order[at[found]])
    left, right = np.concatenate(left), np.concatenate(right)
    while len(left):
        smaller = np.minimum(labels[left], labels[right])
        if np.array_equal(smaller, labels[left]) and np.array_equal(smaller, labels[right]):
            break
        np.minimum.at(labels, left, smaller)
        np.minimum.at(labels, right, smaller)
    kept, index = np.unique(labels, return_inverse=True)
    return hot[kept], index


def _snap(a, b) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """One round of snap rounding, in grid units.

    Returns (cells, u, v, segment): one row per piece, with u and v indices into cells,
    pieces of one cell gone and repeats kept.
    """
    n = len(a)
    hot, ends = _hot_cells(a, b)
    seg, cell, t = _through_cells(a, b, hot)
    every = np.arange(n)
    seg = np.concatenate([every, every, seg])
    t = np.concatenate([np.full(n, -1.0), np.full(n, 2.0), t])  # a segment starts and ends in its own cells
    cell = np.concatenate([ends[:n], ends[n:], cell])
    order = np.lexsort((t, seg))
    seg, cell = seg[order], cell[order]
    kept, index = _merge_touching(hot)
    cell = index[cell]
    same = seg[1:] == seg[:-1]
    u, v, s = cell[:-1][same], cell[1:][same], seg[:-1][same]
    keep = u != v
    return kept, u[keep], v[keep], s[keep]


@dataclass
class Face:
    """One bounded face: half-edge cycles, the first counter-clockwise."""

    index: int
    area: float  # net, holes taken off
    cycles: list = field(default_factory=list)


class Arrangement:
    """The planar subdivision of a set of segments.

    After construction: `vertices`, `edges` (E, 2), `alive` (the edges that bound a
    face), `faces`, and `sources_of(edge)`: the indices in `sources` that gave rise
    to an edge, so a caller can tell what drew a face's boundary.
    """

    def __init__(self, a, b, eps, sources=None):
        self.a = np.asarray(a, dtype=float).reshape(-1, 2)
        self.b = np.asarray(b, dtype=float).reshape(-1, 2)
        self.eps = float(eps)
        self.sources = (np.arange(len(self.a)) if sources is None
                        else np.asarray(sources, dtype=np.int64))
        real = np.any(self.a != self.b, axis=1)  # a point is not a segment
        self.a, self.b, self.sources = self.a[real], self.b[real], self.sources[real]
        if len(self.a) == 0:
            self.vertices = np.empty((0, 2))
            self.edges = np.empty((0, 2), dtype=np.int64)
            self.alive = np.empty(0, dtype=bool)
            self.faces = []
            self._edge_src = (np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64))
            return
        offset = np.minimum(self.a.min(axis=0), self.b.min(axis=0))
        a, b, sources = (self.a - offset) / self.eps, (self.b - offset) / self.eps, self.sources
        last = None
        for _ in range(MAX_CUTS):  # until no vertex lies on an edge it is not an end of
            hot, u, v, segment = _snap(a, b)
            rows = np.unique(np.stack([np.minimum(u, v), np.maximum(u, v), sources[segment]], axis=1), axis=0)
            state = (hot.tobytes(), rows.tobytes())
            if state == last:
                break
            last = state
            a, b, sources = hot[rows[:, 0]].astype(float), hot[rows[:, 1]].astype(float), rows[:, 2]
        self.vertices = offset + hot * self.eps
        count = max(len(hot), 1)
        keys, edge_of = np.unique(rows[:, 0] * count + rows[:, 1], return_inverse=True)
        self.edges = np.stack([keys // count, keys % count], axis=1)
        pairs = np.unique(np.stack([edge_of, rows[:, 2]], axis=1), axis=0)
        self._edge_src = (pairs[:, 0], pairs[:, 1])
        self.alive = np.ones(len(self.edges), dtype=bool)
        self._trace()

    # ---------------------------------------------------------- edges

    def sources_of(self, edge) -> np.ndarray:
        edges, sources = self._edge_src
        lo = np.searchsorted(edges, edge, side="left")
        hi = np.searchsorted(edges, edge, side="right")
        return sources[lo:hi]

    def edge_sources(self) -> tuple[np.ndarray, np.ndarray]:
        """(edge ids, source ids), one row per pair, sorted by edge."""
        return self._edge_src

    def edge_lengths(self) -> np.ndarray:
        d = self.vertices[self.edges[:, 1]] - self.vertices[self.edges[:, 0]]
        return np.hypot(d[:, 0], d[:, 1])

    def degree(self, alive_only=False) -> np.ndarray:
        edges = self.edges[self.alive] if alive_only else self.edges
        return np.bincount(edges.ravel(), minlength=len(self.vertices))

    def _peel(self):
        """Remove edges with a free end, repeatedly: they bound nothing."""
        edges = self.edges
        degree = self.degree(alive_only=True).tolist()
        incident = [[] for _ in degree]
        for k in np.flatnonzero(self.alive).tolist():
            u, v = edges[k]
            incident[u].append(k)
            incident[v].append(k)
        stack = [v for v, deg in enumerate(degree) if deg == 1]
        alive = self.alive.tolist()
        rows = edges.tolist()
        while stack:
            vertex = stack.pop()
            if degree[vertex] != 1:
                continue
            for k in incident[vertex]:
                if alive[k]:
                    alive[k] = False
                    u, v = rows[k]
                    other = v if u == vertex else u
                    degree[vertex] -= 1
                    degree[other] -= 1
                    if degree[other] == 1:
                        stack.append(other)
                    break
        self.alive = np.array(alive, dtype=bool)

    # ---------------------------------------------------------- faces

    def _walk(self):
        """Half-edges of the alive edges, then their cycles (face on the left)."""
        ids = np.flatnonzero(self.alive)
        count = len(ids)
        origin = np.concatenate([self.edges[ids, 0], self.edges[ids, 1]])
        dest = np.concatenate([self.edges[ids, 1], self.edges[ids, 0]])
        twin = np.concatenate([np.arange(count) + count, np.arange(count)])
        step = self.vertices[dest] - self.vertices[origin]
        angle = np.arctan2(step[:, 1], step[:, 0])
        order = np.lexsort((angle, origin))
        first = np.ones(len(order), dtype=bool)
        first[1:] = origin[order[1:]] != origin[order[:-1]]
        starts = np.flatnonzero(first)
        group = np.cumsum(first) - 1
        ends = np.append(starts[1:], len(order)) - 1
        # clockwise neighbour around the origin: the previous in sorted angle, wrapping
        position = np.arange(len(order))
        previous = position - 1
        previous[starts] = ends[group[starts]]
        clockwise = np.empty(len(order), dtype=np.int64)
        clockwise[order] = order[previous]
        following = clockwise[twin]
        cycle_of = np.full(len(order), -1, dtype=np.int64)
        cycles = []
        steps = following.tolist()
        for start in range(len(steps)):
            if cycle_of[start] >= 0:
                continue
            cycle, h = [], start
            label = len(cycles)
            while cycle_of[h] < 0:
                cycle_of[h] = label
                cycle.append(h)
                h = steps[h]
            cycles.append(cycle)
        return ids, origin, dest, twin, following, cycles, cycle_of

    def _bridges(self) -> list[int]:
        """Edges whose removal disconnects the graph, found by depth-first search (Tarjan).
        Read from the graph alone: noise the size of eps can twist the planar walk around
        a vertex, and that must never decide that a real edge bounds nothing."""
        adjacent: dict[int, list] = {}
        for edge in np.flatnonzero(self.alive).tolist():
            u, v = self.edges[edge].tolist()
            adjacent.setdefault(u, []).append((v, edge))
            adjacent.setdefault(v, []).append((u, edge))
        order: dict[int, int] = {}
        low: dict[int, int] = {}
        out = []
        for root in adjacent:
            if root in order:
                continue
            order[root] = low[root] = len(order)
            stack = [(root, -1, iter(adjacent[root]))]
            while stack:
                vertex, came_by, options = stack[-1]
                for other, edge in options:
                    if edge == came_by:
                        continue
                    if other in order:
                        low[vertex] = min(low[vertex], order[other])
                    else:
                        order[other] = low[other] = len(order)
                        stack.append((other, edge, iter(adjacent[other])))
                        break
                else:
                    stack.pop()
                    if stack:
                        parent = stack[-1][0]
                        low[parent] = min(low[parent], low[vertex])
                        if low[vertex] > order[parent]:
                            out.append(came_by)
        return out

    def _trace(self):
        self._peel()
        self.alive[self._bridges()] = False  # a line joining two loops bounds nothing
        self._peel()
        ids, origin, dest, twin, following, cycles, cycle_of = self._walk()
        self._origin, self._dest, self._twin, self._next = origin, dest, twin, following
        self._edge_ids = np.concatenate([ids, ids]) if len(ids) else ids
        self.faces = []
        self._face_of = np.full(len(origin), -1, dtype=np.int64)
        if not cycles:
            return
        areas = np.array([ring_area(self.vertices[origin[c]]) for c in cycles])
        component = self._components(origin, dest, cycles)
        tiny = self.eps * self.eps
        bounded = [k for k, area in enumerate(areas) if area > tiny]
        face_of_cycle = {k: f for f, k in enumerate(bounded)}
        faces = [Face(index=f, area=float(areas[k]), cycles=[cycles[k]]) for f, k in enumerate(bounded)]
        boxes = [self._box(cycles[k]) for k in bounded]
        for k, area in enumerate(areas):
            if area < -tiny:  # the outside of a connected piece: a hole of what contains it
                host = self._host(cycles, k, bounded, areas, component, boxes)
                if host is not None:
                    faces[host].cycles.append(cycles[k])
                    faces[host].area += float(area)
                    face_of_cycle[k] = host
        self.faces = faces
        for k, face in face_of_cycle.items():
            self._face_of[cycles[k]] = face

    def _components(self, origin, dest, cycles) -> list[int]:
        """The connected piece of each cycle."""
        parent = list(range(len(self.vertices)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for u, v in zip(origin.tolist(), dest.tolist()):
            ru, rv = find(u), find(v)
            if ru != rv:
                parent[ru] = rv
        starts = origin[[c[0] for c in cycles]].tolist()
        return [find(v) for v in starts]

    def _box(self, cycle):
        pts = self.vertices[self._origin[cycle]]
        return pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max()

    def _host(self, cycles, hole, bounded, areas, component, boxes):
        """The smallest face of another connected piece holding this cycle."""
        point = self.vertices[self._origin[cycles[hole][0]]]
        best, best_area = None, None
        for f, k in enumerate(bounded):
            x0, y0, x1, y1 = boxes[f]
            if component[k] == component[hole] or not (x0 <= point[0] <= x1 and y0 <= point[1] <= y1):
                continue
            if contains(self.vertices[self._origin[cycles[k]]], point):
                if best is None or areas[k] < best_area:
                    best, best_area = f, areas[k]
        return best

    def face_rings(self, face: Face) -> list[np.ndarray]:
        return [self.vertices[self._origin[c]] for c in face.cycles]

    def face_edges(self, face: Face) -> np.ndarray:
        """Edge ids around a face."""
        return np.unique(self._edge_ids[np.concatenate([np.asarray(c) for c in face.cycles])])

    def across(self, face: Face, edges) -> set:
        """The faces on the far side of a face's given edges; -1 is the open outside."""
        wanted = set(np.asarray(edges).tolist())
        out = set()
        for cycle in face.cycles:
            for h in cycle:
                if int(self._edge_ids[h]) in wanted:
                    out.add(int(self._face_of[self._twin[h]]))
        return out

    def drawing_box(self, smallest=0.0) -> tuple | None:
        """x0, y0, x1, y1 of the faces wider than `smallest`: the drawing proper. Overhanging
        lines bound nothing, and `smallest` leaves out symbols on the ends of lines. None
        when nothing that size is enclosed."""
        boxes = []
        for face in self.faces:
            points = np.concatenate(self.face_rings(face))
            low, high = points.min(axis=0), points.max(axis=0)
            if max(high - low) > smallest:
                boxes.append((*low, *high))
        if not boxes:
            return None
        boxes = np.array(boxes)
        return (float(boxes[:, 0].min()), float(boxes[:, 1].min()), float(boxes[:, 2].max()), float(boxes[:, 3].max()))

    def union_rings(self, face_ids) -> list[np.ndarray]:
        """Outline of a set of faces: outer rings counter-clockwise, holes clockwise.
        Edges inside the set disappear, so a line through a region leaves no trace."""
        chosen = np.zeros(len(self.faces) + 1, dtype=bool)  # index -1 is the outside: False
        chosen[list(face_ids)] = True
        inside = chosen[self._face_of]
        border = inside & ~inside[self._twin]
        following, twin = self._next.tolist(), self._twin.tolist()
        seen = np.zeros(len(border), dtype=bool)
        origin = self._origin
        rings = []
        for start in np.flatnonzero(border).tolist():
            if seen[start]:
                continue
            ring, h = [], start
            while not seen[h]:
                seen[h] = True
                ring.append(h)
                k = following[h]
                while not border[k]:  # turn around the vertex, staying inside the set
                    k = following[twin[k]]
                h = k
            rings.append(self.vertices[origin[ring]])
        return rings


def contains(ring, point) -> bool:
    """Even-odd point in ring."""
    return bool(points_in_rings([ring], np.asarray(point, dtype=float).reshape(1, 2))[0])


def points_in_rings(rings, points) -> np.ndarray:
    """A bool per point: inside `rings` by the even-odd rule, holes included."""
    points = np.asarray(points, dtype=float).reshape(-1, 2)
    odd = np.zeros(len(points), dtype=bool)
    for ring in rings:
        p = np.asarray(ring, dtype=float)
        q = np.roll(p, -1, axis=0)
        ax, ay, bx, by = p[:, 0], p[:, 1], q[:, 0], q[:, 1]
        px, py = points[:, 0, None], points[:, 1, None]
        crosses = (ay > py) != (by > py)
        run = np.where(by != ay, by - ay, 1.0)
        odd ^= np.count_nonzero(crosses & (px < ax + (py - ay) * (bx - ax) / run), axis=1) % 2 == 1
    return odd


def distance_to_rings(rings, points) -> np.ndarray:
    """Distance from each point to the nearest ring edge."""
    points = np.asarray(points, dtype=float).reshape(-1, 2)
    a = np.concatenate([np.asarray(r, dtype=float) for r in rings])
    b = np.concatenate([np.roll(np.asarray(r, dtype=float), -1, axis=0) for r in rings])
    return _distance_to_segments(points, a, b)


def _distance_to_segments(points, a, b) -> np.ndarray:
    d = b - a
    length2 = np.where(np.sum(d * d, axis=1) > 0, np.sum(d * d, axis=1), 1.0)
    wx = points[:, 0, None] - a[:, 0]
    wy = points[:, 1, None] - a[:, 1]
    t = np.clip((wx * d[:, 0] + wy * d[:, 1]) / length2, 0.0, 1.0)
    return np.sqrt(np.min((wx - t * d[:, 0]) ** 2 + (wy - t * d[:, 1]) ** 2, axis=1))


def interior_point(rings) -> np.ndarray:
    """A point strictly inside a region: half way across it from its longest edge."""
    outer = max(rings, key=lambda r: abs(ring_area(r)))
    p = np.asarray(outer, dtype=float)
    q = np.roll(p, -1, axis=0)
    d = q - p
    length = np.hypot(d[:, 0], d[:, 1])
    k = int(np.argmax(length))
    middle = (p[k] + q[k]) / 2.0
    normal = np.array([-d[k, 1], d[k, 0]]) / length[k]
    if ring_area(outer) < 0:
        normal = -normal
    a = np.concatenate([np.asarray(r, dtype=float) for r in rings])
    b = np.concatenate([np.roll(np.asarray(r, dtype=float), -1, axis=0) for r in rings])
    e = b - a
    den = _cross(normal[0], normal[1], e[:, 0], e[:, 1])
    usable = np.abs(den) > 1e-12
    wx, wy = a[:, 0] - middle[0], a[:, 1] - middle[1]
    safe = np.where(usable, den, 1.0)
    t = _cross(wx, wy, e[:, 0], e[:, 1]) / safe
    s = _cross(wx, wy, normal[0], normal[1]) / safe
    hits = t[usable & (t > 1e-12) & (s >= -1e-9) & (s <= 1 + 1e-9)]
    return middle + normal * (float(hits.min()) / 2.0 if len(hits) else 0.0)


def simplify_ring(ring, tolerance) -> np.ndarray:
    """Drop vertices that lie on the line through their neighbours."""
    p = np.asarray(ring, dtype=float)
    while len(p) > 3:
        before, after = np.roll(p, 1, axis=0), np.roll(p, -1, axis=0)
        chord = after - before
        length = np.hypot(chord[:, 0], chord[:, 1])
        off = np.abs(_cross(chord[:, 0], chord[:, 1], p[:, 0] - before[:, 0], p[:, 1] - before[:, 1]))
        between = np.sum((p - before) * chord, axis=1) >= 0
        flat = (off <= tolerance * np.where(length > 0, length, 1.0)) & between
        flat &= ~np.roll(flat, 1)  # never two neighbours in one pass
        if not np.any(flat):
            break
        p = p[~flat]
    return p


def gap_bridges(arrangement: Arrangement, near, far, min_stub=0.0) -> np.ndarray:
    """Short segments, as (k, 2, 2), that close gaps at free line ends.

    A free end may reach, cheapest first:
      - another free end straight ahead, up to `far` (a dash gap);
      - the point where its line meets another end's line, each up to `far`;
      - an edge straight ahead, up to `far`;
      - another free end beside it on a parallel line, up to `far` (a thin layer's open end);
      - another free end, or the nearest edge, up to `near`, but never back along its line.
    Each end is used once. A line end stops short for many reasons, so nothing that
    only `far` would allow is taken unless the line is pointing at its target.
    Ends on stubs shorter than `min_stub` are overshoots past a junction: they stay.
    """
    verts, edges = arrangement.vertices, arrangement.edges
    degree = arrangement.degree()
    free = np.flatnonzero(degree == 1)
    if not len(free) or far <= 0:
        return np.empty((0, 2, 2))
    ends, tangents = [], []
    neighbours: dict[int, list[int]] = {}
    for k, (u, v) in enumerate(edges.tolist()):
        neighbours.setdefault(u, []).append(k)
        neighbours.setdefault(v, []).append(k)
    for vertex in free.tolist():
        edge = neighbours[vertex][0]
        u, v = edges[edge]
        other = v if u == vertex else u
        d = verts[vertex] - verts[other]
        if min_stub > 0 and _stub_length(vertex, edges, verts, neighbours, degree, min_stub) < min_stub:
            continue
        ends.append(vertex)
        tangents.append(d / max(float(np.hypot(*d)), 1e-12))
    if not ends:
        return np.empty((0, 2, 2))
    ends = np.array(ends)
    tangents = np.array(tangents)
    points = verts[ends]
    aligned = np.cos(np.radians(25.0))
    candidates = []  # (cost, [end indices], [segments])
    i, j = _close_pairs(points, points, 1.5 * far)
    for a, c in zip(i.tolist(), j.tolist()):
        v = points[c] - points[a]
        length = float(np.hypot(*v))
        if length <= 1e-12:
            continue
        u = v / length
        facing = tangents[a] @ u > aligned and tangents[c] @ -u > aligned
        forward = tangents[a] @ u > BACKWARD and tangents[c] @ -u > BACKWARD
        side_by_side = tangents[a] @ tangents[c] > aligned and abs(tangents[a] @ u) < 0.35
        if length <= far and facing:
            candidates.append((length, [a, c], [(points[a], points[c])]))
        elif length <= far and side_by_side:  # the open end of a thin layer drawn as two lines
            candidates.append((2.5 * length, [a, c], [(points[a], points[c])]))
        elif length <= near and forward:
            candidates.append((2.0 * length, [a, c], [(points[a], points[c])]))
        den = _cross(tangents[a, 0], tangents[a, 1], tangents[c, 0], tangents[c, 1])
        if abs(den) > 0.2:  # a corner lost between two line ends
            s = _cross(v[0], v[1], tangents[c, 0], tangents[c, 1]) / den
            t = _cross(v[0], v[1], tangents[a, 0], tangents[a, 1]) / den
            if 0 < s <= far and 0 < t <= far:
                x = points[a] + s * tangents[a]
                candidates.append((s + t, [a, c], [(points[a], x), (points[c], x)]))
    candidates.extend(_edge_targets(arrangement, ends, points, tangents, near, far))
    candidates.sort(key=lambda row: row[0])
    used = set()
    out = []
    for _, who, segments in candidates:
        if used.intersection(who):
            continue
        used.update(who)
        out.extend(segments)
    return np.array(out, dtype=float).reshape(-1, 2, 2)


def _stub_length(vertex, edges, verts, neighbours, degree, limit) -> float:
    """Length from a free end to the first junction, stopping at `limit`. A line free
    at both ends has no junction: it is a dash or a whole line, never an overshoot."""
    total, here, came = 0.0, vertex, None
    while total < limit:
        came = next(k for k in neighbours[here] if k != came)
        u, v = edges[came]
        there = v if u == here else u
        total += float(np.hypot(*(verts[there] - verts[here])))
        here = there
        if degree[here] == 1:
            return float("inf")
        if degree[here] > 2:
            return total
    return total


def _edge_targets(arrangement, ends, points, tangents, near, far) -> list:
    """Candidates from a free end to an edge: ahead up to `far`, anywhere up to `near`."""
    verts, edges = arrangement.vertices, arrangement.edges
    a, b = verts[edges[:, 0]], verts[edges[:, 1]]
    tips = points + tangents * far
    count = len(points)
    pair_a, pair_b = _close_pairs(np.concatenate([points, a]), np.concatenate([tips, b]), near)
    mine = (pair_a < count) & (pair_b >= count)
    end_ids, edge_ids = pair_a[mine], pair_b[mine] - count
    out = []
    if not len(end_ids):
        return out
    e = b[edge_ids] - a[edge_ids]
    t = tangents[end_ids]
    den = _cross(t[:, 0], t[:, 1], e[:, 0], e[:, 1])
    wx = a[edge_ids, 0] - points[end_ids, 0]
    wy = a[edge_ids, 1] - points[end_ids, 1]
    safe = np.where(np.abs(den) > 1e-9, den, 1.0)
    s = _cross(wx, wy, e[:, 0], e[:, 1]) / safe
    u = _cross(wx, wy, t[:, 0], t[:, 1]) / safe
    length2 = np.sum(e * e, axis=1)
    along = np.clip(-(wx * e[:, 0] + wy * e[:, 1]) / np.where(length2 > 0, length2, 1.0), 0.0, 1.0)
    foot = a[edge_ids] + along[:, None] * e
    to_foot = foot - points[end_ids]
    gap = np.hypot(to_foot[:, 0], to_foot[:, 1])
    forward = np.sum(to_foot * t, axis=1) > BACKWARD * gap  # never back over its own line
    ahead = (np.abs(den) > 1e-9) & (s > 1e-12) & (s <= far) & (u >= 0) & (u <= 1)
    for k in range(len(end_ids)):
        edge = edges[edge_ids[k]]
        end = ends[end_ids[k]]
        if end in (edge[0], edge[1]):
            continue
        p = points[end_ids[k]]
        if ahead[k]:
            out.append((float(s[k]), [int(end_ids[k])], [(p, p + s[k] * t[k])]))
        elif 1e-12 < gap[k] <= near and forward[k]:
            out.append((3.0 * float(gap[k]), [int(end_ids[k])], [(p, foot[k])]))
    return out
