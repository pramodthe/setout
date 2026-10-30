"""Section cuts of built assemblies, drafted as DXF.

Steps 2 and 3 of the synthetic data engine: a built assembly
(`geometry.build_assembly`) is cut by a plane, every part the plane crosses
becomes closed 2D polylines in plane coordinates, and the section is written
as a DXF in a house style passed in as data. Parts the plane misses drop out
of the cut, which is what gives hidden parts with known ground truth.

Honesty rules of this module:
- The cut reports only geometry the meshes contain; nothing is fitted.
- Dimension text is only ever a param value with `drawing` provenance, and a
  DIMENSION is drawn only between two section points whose separation
  measures that value (`annotate` lists the rest in `unplaced`).
- Labels are `source_label` verbatim, or absent.
- Nothing here knows what a part is. Layers, linetypes, colours and hatches
  come from the `Style` the caller passes, keyed by `class_id`, then role,
  then a default: library data, trade-agnostic.

Plane convention: the normal points at the viewer. For the usual z = const cut
with normal (0, 0, 1), plane u is world x and plane v is world y. Vertices
within `tol` of the plane count as being on the normal's side, so the plane
effectively cuts an infinitesimal distance toward the negative side: a face
lying in the plane shows as its outline when its solid is behind the plane
and not at all when the solid is in front.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Literal

import ezdxf
from ezdxf import units as dxf_units
from ezdxf.enums import MTextEntityAlignment
from ezdxf.tools import pattern as dxf_pattern

from .geometry import Mesh
from .spec import DetailSpec, ParamRole, Provenance

Vec3 = tuple[float, float, float]
Point2 = tuple[float, float]
Polyline = list[Point2]

CHAIN_TOL_MM = 1e-3  # endpoints closer than this are one point
MIN_LOOP_AREA_MM2 = 1e-6  # loops below this are degenerate contact, dropped


# ---------------------------------------------------------------- section data

@dataclass
class Dimension:
    """A printed value attached to two section points that measure it."""

    part_id: str
    name: str  # param name
    value: float
    text: str  # the printed value: DIMENSION override text
    p1: Point2
    p2: Point2
    axis: Literal["u", "v"]  # measured along plane u (horizontal) or v
    quote: str | None = None  # the param's source_quote, for traceability


@dataclass
class SectionPart:
    id: str
    color: str
    loops: list[Polyline] = field(default_factory=list)  # outer CCW, holes CW
    open_paths: list[Polyline] = field(default_factory=list)  # open meshes
    dropped: int = 0  # loops below the area threshold
    label: str | None = None  # verbatim drawing label, or None
    class_id: str | None = None
    role: str | None = None
    dimensions: list[Dimension] = field(default_factory=list)

    def vertices(self) -> list[Point2]:
        return [p for path in self.loops + self.open_paths for p in path]

    def bounds(self) -> tuple[float, float, float, float] | None:
        pts = self.vertices()
        if not pts:
            return None
        return (min(p[0] for p in pts), min(p[1] for p in pts),
                max(p[0] for p in pts), max(p[1] for p in pts))

    def area(self) -> float:
        """Net section area: outer loops count positive, holes negative."""
        return sum(_area2(loop) for loop in self.loops) / 2.0


@dataclass
class SectionDrawing:
    origin: Vec3
    normal: Vec3
    u: Vec3  # in-plane basis, u x v == normal
    v: Vec3
    parts: dict[str, SectionPart] = field(default_factory=dict)  # crossed parts, mesh order
    missed: list[str] = field(default_factory=list)  # part ids the plane does not cross
    unplaced: list[tuple[str, str, str]] = field(default_factory=list)  # (part, param, why)
    notes: list[str] = field(default_factory=list)

    def bounds(self) -> tuple[float, float, float, float] | None:
        boxes = [b for b in (p.bounds() for p in self.parts.values()) if b]
        if not boxes:
            return None
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    def dimensions(self) -> list[Dimension]:
        return [d for p in self.parts.values() for d in p.dimensions]

    def to_dict(self) -> dict:
        """JSON-ready: the visible graph of the cut."""
        return {
            "origin": list(self.origin), "normal": list(self.normal),
            "u": list(self.u), "v": list(self.v), "units": "mm",
            "parts": [{
                "id": p.id, "label": p.label, "class_id": p.class_id, "role": p.role,
                "color": p.color,
                "loops": [[list(q) for q in loop] for loop in p.loops],
                "open_paths": [[list(q) for q in path] for path in p.open_paths],
                "dimensions": [asdict(d) for d in p.dimensions],
            } for p in self.parts.values()],
            "missed": list(self.missed),
            "unplaced": [list(u) for u in self.unplaced],
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------- vectors

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _unit(a) -> Vec3:
    n = math.sqrt(_dot(a, a))
    if n < 1e-12:
        raise ValueError("plane normal must not be zero")
    return (a[0] / n, a[1] / n, a[2] / n)


def _area2(poly: Polyline) -> float:
    """Twice the signed area (positive = counter-clockwise)."""
    return sum(x0 * y1 - x1 * y0
               for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]))


def plane_basis(normal) -> tuple[Vec3, Vec3, Vec3]:
    """Unit normal and a right-handed in-plane basis (u, v) with u x v == n.

    v is world +y projected onto the plane (the section's up); for a plan
    cut along y it is world -z, so plans read x right and -z up. For normal
    (0, 0, 1), u is world x and v is world y unchanged; (0, 0, -1) is the
    same section seen from the other side, mirrored.
    """
    n = _unit(normal)
    up = (0.0, 1.0, 0.0) if abs(n[1]) < 0.999 else (0.0, 0.0, -1.0)
    v = _unit(_sub(up, tuple(c * _dot(up, n) for c in n)))
    u = _cross(v, n)
    return n, u, v


# ---------------------------------------------------------------- the cut

def cut(meshes: dict[str, Mesh], origin=(0.0, 0.0, 0.0), normal=(0.0, 0.0, 1.0),
        tol: float = CHAIN_TOL_MM, min_area: float = MIN_LOOP_AREA_MM2) -> SectionDrawing:
    """Intersect every part's triangles with the plane and chain the pieces
    into closed polylines in plane coordinates (mm). Parts the plane does not
    cross are listed in `missed`; degenerate loops are dropped and noted."""
    n, u, v = plane_basis(normal)
    o = (float(origin[0]), float(origin[1]), float(origin[2]))
    section = SectionDrawing(origin=o, normal=n, u=u, v=v)
    for pid, mesh in meshes.items():
        segments = _segments(mesh, o, n, u, v, tol)
        loops, open_paths = _chain(segments, tol) if segments else ([], [])
        part = SectionPart(id=pid, color=mesh.color)
        for loop in loops:
            loop = _simplify(loop, tol, closed=True)
            if len(loop) < 3 or abs(_area2(loop)) < 2.0 * min_area:
                part.dropped += 1
                continue
            part.loops.append(loop)
        part.loops = _orient(part.loops)
        part.open_paths = [p for p in (_simplify(p, tol, closed=False) for p in open_paths)
                           if len(p) >= 2]
        if part.dropped:
            section.notes.append(f"{pid}: dropped {part.dropped} loop(s) under "
                                 f"{min_area} mm² (degenerate contact)")
        if part.open_paths:
            section.notes.append(f"{pid}: {len(part.open_paths)} open chain(s): "
                                 "the mesh is not closed where the plane cuts it")
        if part.loops or part.open_paths:
            section.parts[pid] = part
        else:
            section.missed.append(pid)
    return section


def _segments(mesh: Mesh, o, n, u, v, tol) -> list[tuple[Point2, Point2]]:
    """Plane/triangle intersections as 2D segments, one per crossing triangle."""
    pos = mesh.positions
    out: list[tuple[Point2, Point2]] = []
    for t in range(0, len(pos), 9):
        tri = [(pos[t + 3 * k], pos[t + 3 * k + 1], pos[t + 3 * k + 2]) for k in range(3)]
        d = [_dot(n, _sub(p, o)) for p in tri]
        d = [0.0 if abs(x) <= tol else x for x in d]  # on-plane vertices: normal's side
        neg = [x < 0 for x in d]
        if all(neg) or not any(neg):
            continue  # coplanar, touching, or entirely to one side
        pts: list[Point2] = []
        for i in range(3):
            j = (i + 1) % 3
            if neg[i] == neg[j]:
                continue
            a, b = tri[i], tri[j]
            s = d[i] / (d[i] - d[j])
            p = (a[0] + s * (b[0] - a[0]), a[1] + s * (b[1] - a[1]), a[2] + s * (b[2] - a[2]))
            rel = _sub(p, o)
            pts.append((_dot(rel, u), _dot(rel, v)))
        if len(pts) == 2 and math.dist(pts[0], pts[1]) > tol:
            out.append((pts[0], pts[1]))
    return out


def _merge_points(points: list[Point2], tol: float) -> tuple[list[int], list[Point2]]:
    """Index every point, merging points within tol via a grid of tol cells."""
    ids: list[int] = []
    uniq: list[Point2] = []
    grid: dict[tuple[int, int], list[int]] = {}
    for p in points:
        cx, cy = math.floor(p[0] / tol), math.floor(p[1] / tol)
        found = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for k in grid.get((cx + dx, cy + dy), ()):
                    q = uniq[k]
                    if abs(q[0] - p[0]) <= tol and abs(q[1] - p[1]) <= tol:
                        found = k
                        break
                if found is not None:
                    break
            if found is not None:
                break
        if found is None:
            found = len(uniq)
            uniq.append(p)
            grid.setdefault((cx, cy), []).append(found)
        ids.append(found)
    return ids, uniq


def _chain(segments, tol) -> tuple[list[Polyline], list[Polyline]]:
    """Join segments end to end into closed loops and open paths.

    Segments are undirected (generator winding is not consistent across the
    registry). A segment the cut produced twice is a ridge the plane only
    touches and cancels out. At a junction of three or more unused segments
    the walk takes the most-left turn, so two loops that touch at a single
    point may come back as one pinched loop.
    """
    ids, pts = _merge_points([p for seg in segments for p in seg], tol)
    count: dict[tuple[int, int], int] = {}
    for k in range(0, len(ids), 2):
        a, b = ids[k], ids[k + 1]
        if a != b:
            key = (a, b) if a < b else (b, a)
            count[key] = count.get(key, 0) + 1
    adj: dict[int, set[int]] = {}
    for (a, b), c in count.items():
        if c % 2:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)

    def take(a: int, b: int) -> None:
        adj[a].discard(b)
        adj[b].discard(a)

    def turn(prev: int, cur: int, nxt: int) -> float:
        (px, py), (cx, cy), (nx, ny) = pts[prev], pts[cur], pts[nxt]
        dx, dy, ex, ey = cx - px, cy - py, nx - cx, ny - cy
        return math.atan2(dx * ey - dy * ex, dx * ex + dy * ey)

    loops: list[Polyline] = []
    open_paths: list[Polyline] = []
    while True:
        live = [p for p in sorted(adj) if adj[p]]
        if not live:
            break
        start = next((p for p in live if len(adj[p]) % 2), live[0])  # ends first
        cur = min(adj[start])
        take(start, cur)
        path = [start, cur]
        prev = start
        closed = False
        while cur != start:
            choices = adj[cur]
            if not choices:
                break
            nxt = max(choices, key=lambda c: (turn(prev, cur, c), -c))
            take(cur, nxt)
            path.append(nxt)
            prev, cur = cur, nxt
        else:
            closed = True
            path.pop()
        (loops if closed else open_paths).append([pts[i] for i in path])
    return loops, open_paths


def _simplify(path: Polyline, tol: float, closed: bool) -> Polyline:
    """Drop repeated points and vertices that lie on the line through their
    neighbours (a flat face split into two triangles adds one per cut)."""
    pts = list(path)
    changed = True
    while changed and len(pts) > (3 if closed else 2):
        changed = False
        n = len(pts)
        for i in (range(n) if closed else range(1, n - 1)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
            if _off_line(b, a, c) <= tol:
                del pts[i]
                changed = True
                break
    if not closed and len(pts) == 2 and math.dist(pts[0], pts[1]) <= tol:
        return pts[:1]
    return pts


def _off_line(b: Point2, a: Point2, c: Point2) -> float:
    """Distance of b from the line a-c (from a when a and c coincide)."""
    ax, ay = c[0] - a[0], c[1] - a[1]
    length = math.hypot(ax, ay)
    if length < 1e-12:
        return math.dist(a, b)
    return abs(ax * (b[1] - a[1]) - ay * (b[0] - a[0])) / length


def _contains(poly: Polyline, p: Point2) -> bool:
    """Ray-casting point in polygon."""
    inside = False
    x, y = p
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def _orient(loops: list[Polyline]) -> list[Polyline]:
    """Outer loops counter-clockwise, loops nested at odd depth clockwise."""
    out = []
    for i, loop in enumerate(loops):
        depth = sum(1 for j, other in enumerate(loops)
                    if j != i and _contains(other, loop[0]))
        ccw = _area2(loop) > 0
        out.append(loop if ccw == (depth % 2 == 0) else loop[::-1])
    return out


# ---------------------------------------------------------------- annotation

def annotate(section: SectionDrawing, spec: DetailSpec,
             roles: dict[str, str] | None = None,
             class_ids: dict[str, str] | None = None,
             tol: float = CHAIN_TOL_MM) -> SectionDrawing:
    """Attach the spec's labels, classes, roles and printed values to the cut.

    Labels are `source_label` verbatim. A dimension is placed only for a
    physical `drawing` param in mm whose value is the u- or v-separation of
    two section points (the part's own first, then the part against the
    rest); everything else goes to `section.unplaced` with the reason. A
    compiled spec carries no part role or class yet, so callers pass them
    from the program; `Component` attributes of those names are used when
    present.
    """
    all_pts = [p for part in section.parts.values() for p in part.vertices()]
    for comp in spec.components:
        part = section.parts.get(comp.id)
        if part is None:
            continue
        part.label = comp.source_label
        part.class_id = (class_ids or {}).get(comp.id, getattr(comp, "class_id", None))
        part.role = (roles or {}).get(comp.id, getattr(comp, "role", None))
        own = part.vertices()
        for q in comp.params:
            if q.provenance != Provenance.DRAWING:
                continue
            if q.role != ParamRole.PHYSICAL:
                section.unplaced.append((comp.id, q.name, f"role {q.role.value}: not a drawn size"))
                continue
            if q.unit != "mm":
                section.unplaced.append((comp.id, q.name, f"unit {q.unit!r} is not a length"))
                continue
            pair = _measuring_pair(own, own, q.value, tol) or _measuring_pair(own, all_pts, q.value, tol)
            if pair is None:
                section.unplaced.append((comp.id, q.name,
                                         f"no two section points are {q.value} apart"))
                continue
            axis, p1, p2 = pair
            part.dimensions.append(Dimension(comp.id, q.name, q.value, printed(q.value),
                                             p1, p2, axis, q.source_quote))
    return section


def printed(value: float) -> str:
    """The value as a drawing prints it: no trailing zeros, no unit."""
    return f"{value:.3f}".rstrip("0").rstrip(".") if value != int(value) else str(int(value))


def _measuring_pair(a_pts, b_pts, value, tol):
    """Two points whose u- or v-separation is `value`, preferring the pair
    with the smallest offset in the other axis (an aligned extent)."""
    best = None
    for axis, k in (("u", 0), ("v", 1)):
        o = 1 - k
        for a in a_pts:
            for b in b_pts:
                if abs(abs(a[k] - b[k]) - value) > tol:
                    continue
                off = abs(a[o] - b[o])
                lo, hi = (a, b) if a[k] <= b[k] else (b, a)
                if best is None or off < best[0]:
                    best = (off, axis, lo, hi)
    return None if best is None else best[1:]


# ---------------------------------------------------------------- house style

@dataclass(frozen=True)
class LineStyle:
    """How one class (or role) of part is drawn. Library data."""

    layer: str
    linetype: str = "Continuous"
    color: int = 7  # ACI colour index
    lineweight: int = -1  # 1/100 mm; -1 = by layer default
    hatch: str | None = None  # predefined DXF pattern name for cut faces
    hatch_scale: float = 1.0
    hatch_angle: float = 0.0
    # linetype definition when the name is not a standard one, in drawing
    # units: [total length, dash, -gap, dash, ...]
    pattern: tuple[float, ...] | None = None


@dataclass
class Style:
    """Drafting style: per-class, then per-role, then a default. Keyed by
    `class_id` first because the owner's drawings tell parts apart by their
    linetype, not by layer name. Loaded from library data (`from_dict`)."""

    by_class: dict[str, LineStyle] = field(default_factory=dict)
    by_role: dict[str, LineStyle] = field(default_factory=dict)
    default: LineStyle = field(default_factory=lambda: LineStyle(layer="CUT"))
    text_layer: str = "TEXT"
    dim_layer: str = "DIM"
    text_height: float = 10.0  # model mm (paper height x drawing scale)
    dim_offset: float = 40.0  # model mm between stacked dimension lines
    dimstyle: str = "EZDXF"

    def for_part(self, part: SectionPart) -> LineStyle:
        if part.class_id and part.class_id in self.by_class:
            return self.by_class[part.class_id]
        if part.role and part.role in self.by_role:
            return self.by_role[part.role]
        return self.default

    @classmethod
    def from_dict(cls, data: dict) -> Style:
        def line(d: dict) -> LineStyle:
            d = dict(d)
            if d.get("pattern") is not None:
                d["pattern"] = tuple(float(x) for x in d["pattern"])
            return LineStyle(**d)

        return cls(
            by_class={k: line(v) for k, v in (data.get("by_class") or {}).items()},
            by_role={k: line(v) for k, v in (data.get("by_role") or {}).items()},
            default=line(data["default"]) if data.get("default") else LineStyle(layer="CUT"),
            **{k: data[k] for k in ("text_layer", "dim_layer", "text_height",
                                     "dim_offset", "dimstyle") if k in data})


# ---------------------------------------------------------------- layout

@dataclass
class _Layout:
    """Where the annotations go, in drawing coordinates. Shared by the DXF and
    PNG writers so both place things identically. This placement is a
    plain deterministic choice, not the owner's house style: strings
    stacked below (horizontal) and right (vertical), callouts down the left
    edge with leaders landing on the nearest outline edge."""

    dims: list[tuple[Dimension, Point2, Point2, Point2, int]]  # dim, p1, p2, base, angle
    labels: list[tuple[SectionPart, Point2, Point2]]  # part, landing, anchor
    extent: tuple[float, float, float, float]  # everything drawn


def _layout(section: SectionDrawing, style: Style, su: float, sv: float) -> _Layout | None:
    bounds = section.bounds()
    if bounds is None:
        return None

    def pt(p: Point2) -> Point2:
        return (p[0] * su, p[1] * sv)

    x0, y0 = pt((bounds[0], bounds[1]))
    x1, y1 = pt((bounds[2], bounds[3]))
    off = style.dim_offset
    dims = []
    stacks = {"u": 0, "v": 0}
    for dim in section.dimensions():
        k = stacks[dim.axis] = stacks[dim.axis] + 1
        p1, p2 = pt(dim.p1), pt(dim.p2)
        if dim.axis == "u":
            dims.append((dim, p1, p2, ((p1[0] + p2[0]) / 2, y0 - k * off), 0))
        else:
            dims.append((dim, p1, p2, (x1 + k * off, (p1[1] + p2[1]) / 2), 90))
    labels = []
    for i, part in enumerate(p for p in section.parts.values() if p.label):
        anchor = (x0 - 2 * off, y1 - i * 2 * style.text_height)
        labels.append((part, _landing(part, anchor, pt), anchor))
    xs = [x0, x1] + [b[0] for *_, b, _ in dims] + [a[0] for *_, a in labels]
    ys = [y0, y1] + [b[1] for *_, b, _ in dims] + [a[1] for *_, a in labels]
    return _Layout(dims, labels, (min(xs), min(ys), max(xs), max(ys)))


def _landing(part: SectionPart, anchor: Point2, pt) -> Point2:
    """The midpoint of the part's outline edge nearest the callout: leaders
    land on the outline, as drafters point them, and stay short."""
    edges = [e for loop in part.loops for e in zip(loop, loop[1:] + loop[:1])]
    edges += [e for path in part.open_paths for e in zip(path, path[1:])]
    mids = [pt(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)) for a, b in edges]
    return min(mids, key=lambda m: math.dist(m, anchor))


# ---------------------------------------------------------------- DXF

def to_dxf(section: SectionDrawing, path, style: Style, scale=1.0):
    """Write the section as DXF (mm): one LWPOLYLINE per loop on the part's
    layer, hatches where the style says, a DIMENSION per placed printed
    value with its text as override, and a leader + MTEXT per label.

    `scale` is a factor or an (su, sv) pair applied to the geometry only.
    Dimension text stays the printed value, so a scale other than 1 gives a
    not-to-scale drawing whose measured/printed ratio exposes the distortion.
    Returns the ezdxf document.
    """
    su, sv = (scale, scale) if isinstance(scale, (int, float)) else scale
    doc = ezdxf.new("R2010", setup=True)
    doc.units = dxf_units.MM
    msp = doc.modelspace()
    for name in (style.text_layer, style.dim_layer):
        if name not in doc.layers:
            doc.layers.add(name)

    def pt(p: Point2) -> Point2:
        return (p[0] * su, p[1] * sv)

    for part in section.parts.values():
        ls = style.for_part(part)
        attribs = _entity_attribs(doc, ls)
        for loop in part.loops:
            msp.add_lwpolyline([pt(p) for p in loop], close=True, dxfattribs=attribs)
        for open_path in part.open_paths:
            msp.add_lwpolyline([pt(p) for p in open_path], close=False, dxfattribs=attribs)
        if ls.hatch and part.loops:
            if ls.hatch.upper() not in dxf_pattern.ISO_PATTERN:
                raise ValueError(f"hatch pattern {ls.hatch!r} is not a predefined DXF pattern")
            hatch = msp.add_hatch(color=ls.color, dxfattribs={"layer": ls.layer})
            hatch.set_pattern_fill(ls.hatch.upper(), color=ls.color, angle=ls.hatch_angle,
                                   scale=ls.hatch_scale)
            for loop in part.loops:  # all loops: holes fall out of the odd parity
                hatch.paths.add_polyline_path([pt(p) for p in loop], is_closed=True)

    layout = _layout(section, style, su, sv)
    if layout is None:
        doc.saveas(path)
        return doc
    for dim, p1, p2, base, angle in layout.dims:
        msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=angle, text=dim.text,
                           dimstyle=style.dimstyle,
                           override={"dimtxt": style.text_height, "dimasz": style.text_height},
                           dxfattribs={"layer": style.dim_layer}).render()
    for part, landing, anchor in layout.labels:
        msp.add_leader([landing, anchor], dimstyle=style.dimstyle,
                       dxfattribs={"layer": style.text_layer})
        text = msp.add_mtext(part.label, dxfattribs={"layer": style.text_layer,
                                                     "char_height": style.text_height})
        text.set_location(anchor, attachment_point=MTextEntityAlignment.MIDDLE_RIGHT)
    doc.saveas(path)
    return doc


def _entity_attribs(doc, ls: LineStyle) -> dict:
    """Make sure the style's linetype and layer exist; entities draw by layer
    and carry an attribute explicitly only where it differs from the layer
    (two styles may share a layer name)."""
    if not doc.linetypes.has_entry(ls.linetype):
        if ls.pattern is None:
            raise ValueError(f"linetype {ls.linetype!r} is not in the document; give "
                             "its pattern in the style")
        doc.linetypes.add(ls.linetype, pattern=list(ls.pattern), description=ls.linetype)
    if ls.layer not in doc.layers:
        doc.layers.add(ls.layer, color=ls.color, linetype=ls.linetype,
                       lineweight=ls.lineweight)
    layer = doc.layers.get(ls.layer)
    attribs = {"layer": ls.layer}
    if layer.dxf.linetype.lower() != ls.linetype.lower():
        attribs["linetype"] = ls.linetype
    if layer.dxf.color != ls.color:
        attribs["color"] = ls.color
    if layer.dxf.lineweight != ls.lineweight:
        attribs["lineweight"] = ls.lineweight
    return attribs


# ---------------------------------------------------------------- PNG (optional)

def to_png(section: SectionDrawing, path, dpi: int = 150, paper_scale: float = 0.2,
           style: Style | None = None, margin_mm: float = 20.0):
    """Raster preview with pillow: filled loops in each part's colour (holes
    cleared), outlines, dimension strings and callouts placed as in the DXF.
    Solid lines only; linetypes and hatches are a DXF matter. `paper_scale`
    is drawing mm per model mm (0.2 = 1:5). Returns the image."""
    from PIL import Image, ImageDraw, ImageFont

    style = style or Style()
    k = dpi / 25.4 * paper_scale
    font = ImageFont.load_default(size=max(8, round(style.text_height * k)))
    layout = _layout(section, style, 1.0, 1.0)
    extent = layout.extent if layout else (0.0, 0.0, 0.0, 0.0)
    text_px = max([0.0] + [font.getlength(p.label) for p, _, _ in (layout.labels if layout else [])])
    x0 = extent[0] - margin_mm - text_px / k
    y0 = extent[1] - margin_mm
    width = int(math.ceil((extent[2] - x0 + margin_mm) * k)) or 1
    height = int(math.ceil((extent[3] - y0 + margin_mm) * k)) or 1
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    def px(p: Point2) -> tuple[float, float]:
        return ((p[0] - x0) * k, height - (p[1] - y0) * k)

    painted = sorted(((abs(_area2(loop)), part, loop)
                      for part in section.parts.values() for loop in part.loops),
                     key=lambda t: -t[0])
    for _, part, loop in painted:  # largest first: holes paint over their outer
        draw.polygon([px(p) for p in loop], fill="white" if _area2(loop) < 0 else part.color)
    for part in section.parts.values():
        for loop in part.loops:
            draw.line([px(p) for p in loop] + [px(loop[0])], fill="black", width=2)
        for open_path in part.open_paths:
            draw.line([px(p) for p in open_path], fill="black", width=2)
    if layout is None:
        img.save(path)
        return img
    for dim, p1, p2, base, angle in layout.dims:
        a, b = ((p1[0], base[1]), (p2[0], base[1])) if angle == 0 else ((base[0], p1[1]), (base[0], p2[1]))
        draw.line([px(p1), px(a)], fill="gray")
        draw.line([px(p2), px(b)], fill="gray")
        draw.line([px(a), px(b)], fill="black")
        ta, tb = px(a), px(b)
        draw.text(((ta[0] + tb[0]) / 2, (ta[1] + tb[1]) / 2), dim.text, fill="black",
                  font=font, anchor="md" if angle == 0 else "lm")
    for part, landing, anchor in layout.labels:
        draw.line([px(landing), px(anchor)], fill="black")
        draw.text(px(anchor), part.label, fill="black", font=font, anchor="rm")
    img.save(path)
    return img
