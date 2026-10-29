"""Closed-set parameter-driven mesh generators (setout-mesh-v0).

Every generator takes a Component + illustrative z-extent and returns a Mesh
with per-component stable id. No free-form code generation: the closed set is
box, l_angle, sheet_follow, bead, rod, fillet, wedge, annotation.

Meshes are dimension-driven: each vertex coordinate is an affine function of
spec params, so verification can measure built dims back out of the mesh.
Mitered 90-degree sheet corners are an acknowledged approximation (see
sheet_follow docstring); physical vs display thickness stays explicit.
"""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass, field

from .spec import Component, DetailSpec
from .units import mm_to_m

GEN_VERSION = "setout-mesh-v0"


@dataclass
class Mesh:
    component_id: str
    positions: list[float] = field(default_factory=list)  # flat xyz, mm
    color: str = "#999999"

    @property
    def tri_count(self) -> int:
        return len(self.positions) // 9

    def bounds(self) -> tuple[float, float, float, float, float, float] | None:
        if not self.positions:
            return None
        xs = self.positions[0::3]
        ys = self.positions[1::3]
        zs = self.positions[2::3]
        return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def _tri(m: Mesh, a, b, c) -> None:
    m.positions.extend([a[0], a[1], a[2], b[0], b[1], b[2], c[0], c[1], c[2]])


def _quad(m: Mesh, a, b, c, d) -> None:
    _tri(m, a, b, c)
    _tri(m, a, c, d)


def _box_tris(m: Mesh, x0, y0, z0, x1, y1, z1) -> None:
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    # (face indices) outward-facing
    _quad(m, v[0], v[3], v[2], v[1])  # front z0
    _quad(m, v[4], v[5], v[6], v[7])  # back z1
    _quad(m, v[0], v[1], v[5], v[4])  # bottom
    _quad(m, v[3], v[7], v[6], v[2])  # top
    _quad(m, v[0], v[4], v[7], v[3])  # left
    _quad(m, v[1], v[2], v[6], v[5])  # right


def _prism(m: Mesh, profile: list[tuple[float, float]], z0: float, z1: float,
           cap_ends: bool = True) -> None:
    """Extrude a 2D polygon (x,y) along z. Profile must be ordered (any dir)."""
    n = len(profile)
    for i in range(n):
        x0, y0 = profile[i]
        x1, y1 = profile[(i + 1) % n]
        _quad(m, (x0, y0, z0), (x1, y1, z0), (x1, y1, z1), (x0, y0, z1))
    if cap_ends and n >= 3:
        p0 = profile[0]
        for i in range(1, n - 1):
            p1, p2 = profile[i], profile[i + 1]
            _tri(m, (p0[0], p0[1], z0), (p2[0], p2[1], z0), (p1[0], p1[1], z0))
            _tri(m, (p0[0], p0[1], z1), (p1[0], p1[1], z1), (p2[0], p2[1], z1))


def gen_box(c: Component, z0: float, z1: float) -> Mesh:
    m = Mesh(c.id, color=c.color)
    g = {p.name: p.value for p in c.params}
    if "step_height" in g:  # lower slab: top below datum by step
        top = -g["step_height"]
        _box_tris(m, g["x0"], top - g["thickness"], z0, g["x1"], top, z1)
    elif "thickness" in g:  # slab-style: top_y + thickness
        _box_tris(m, g["x0"], g["top_y"] - g["thickness"], z0,
                  g["x1"], g["top_y"], z1)
    elif "y0" in g and "h" in g:
        _box_tris(m, g["x0"], g["y0"], z0, g["x1"], g["y0"] + g["h"], z1)
    elif "y0" in g and "y1" in g:
        _box_tris(m, g["x0"], g["y0"], z0, g["x1"], g["y1"], z1)
    else:
        raise ValueError(f"box generator: {c.id} params insufficient")
    return m


def gen_l_angle(c: Component, z0: float, z1: float) -> Mesh:
    """L profile: vertical leg on plane x_face (+x side), foot along +x."""
    m = Mesh(c.id, color=c.color)
    g = {p.name: p.value for p in c.params}
    x, t, h, f, yb = (g["x_face"], g["leg_t"], g["leg_h"], g["foot_len"],
                      g["base_y"])
    profile = [(x, yb), (x + f, yb), (x + f, yb + t), (x + t, yb + t),
               (x + t, yb + h), (x, yb + h)]
    _prism(m, profile, z0, z1)
    return m


def gen_sheet_follow(c: Component, z0: float, z1: float,
                     use_display: bool = False) -> Mesh:
    """Ribbon of thickness t centred on the section-plane path, extruded z.

    Interior vertices use averaged (miter) normals. At 90-degree outside
    corners the miter extends the sheet past the path vertex by the exact
    miter length — correct for a bent sheet of uniform thickness. End caps
    included so the solid is closed.
    """
    m = Mesh(c.id, color=c.color)
    if len(c.path) < 2:
        raise ValueError(f"sheet_follow: {c.id} needs >= 2 path points")
    key = "thickness_display" if use_display else "thickness_physical"
    t = c.param(key).value / 2.0
    pts = [(p.x, p.y) for p in c.path]
    # segment normals (left of direction)
    norms = []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        dx, dy = x1 - x0, y1 - y0
        L = math.hypot(dx, dy)
        if L < 1e-9:
            raise ValueError(f"sheet_follow: {c.id} zero-length segment")
        norms.append((-dy / L, dx / L))
    # vertex normals: averaged, rescaled to keep half-thickness exact
    vn = [norms[0]]
    for i in range(1, len(pts) - 1):
        nx = norms[i - 1][0] + norms[i][0]
        ny = norms[i - 1][1] + norms[i][1]
        L = math.hypot(nx, ny)
        if L < 1e-9:  # 180-degree reversal; fall back to segment normal
            vn.append(norms[i])
            continue
        nx, ny = nx / L, ny / L
        cos_half = nx * norms[i][0] + ny * norms[i][1]
        s = 1.0 / max(cos_half, 0.2)  # clamp: folds beyond ~157deg
        vn.append((nx * s, ny * s))
    vn.append(norms[-1])
    left = [(x + nx * t, y + ny * t) for (x, y), (nx, ny) in zip(pts, vn)]
    right = [(x - nx * t, y - ny * t) for (x, y), (nx, ny) in zip(pts, vn)]
    for i in range(len(pts) - 1):
        # top surface, bottom surface, extruded as side walls of the ribbon
        _quad(m, (left[i][0], left[i][1], z0),
              (left[i + 1][0], left[i + 1][1], z0),
              (left[i + 1][0], left[i + 1][1], z1),
              (left[i][0], left[i][1], z1))
        _quad(m, (right[i][0], right[i][1], z0),
              (right[i][0], right[i][1], z1),
              (right[i + 1][0], right[i + 1][1], z1),
              (right[i + 1][0], right[i + 1][1], z0))
    # end caps + z end caps
    _quad(m, (left[0][0], left[0][1], z0), (left[0][0], left[0][1], z1),
          (right[0][0], right[0][1], z1), (right[0][0], right[0][1], z0))
    _quad(m, (left[-1][0], left[-1][1], z0), (right[-1][0], right[-1][1], z0),
          (right[-1][0], right[-1][1], z1), (left[-1][0], left[-1][1], z1))
    # No z end caps: the folded ribbon polygon is concave, so a triangle fan
    # would span the folds with spurious faces. Sheet edges at z0/z1 stay
    # intentionally open (verification treats sheets as open by design).
    return m


def gen_bead(c: Component, z0: float, z1: float) -> Mesh:
    m = Mesh(c.id, color=c.color)
    g = {p.name: p.value for p in c.params}
    _box_tris(m, g["x0"], g["y0"], z0, g["x0"] + g["width"], g["y1"], z1)
    return m


def gen_rod(c: Component, z0: float, z1: float, sides: int = 8) -> Mesh:
    m = Mesh(c.id, color=c.color)
    g = {p.name: p.value for p in c.params}
    poly = [(g["cx"] + g["r"] * math.cos(2 * math.pi * i / sides),
             g["cy"] + g["r"] * math.sin(2 * math.pi * i / sides))
            for i in range(sides)]
    _prism(m, poly, z0, z1)
    return m


def gen_fillet(c: Component, z0: float, z1: float) -> Mesh:
    """Right-triangle cove: right angle at (x, y), legs +x and +y."""
    m = Mesh(c.id, color=c.color)
    g = {p.name: p.value for p in c.params}
    _prism(m, [(g["x"], g["y"]), (g["x"] + g["leg_x"], g["y"]),
               (g["x"], g["y"] + g["leg_y"])], z0, z1)
    return m


def gen_wedge(c: Component, z0: float, z1: float) -> Mesh:
    """Screed wedge: flat bottom, top falling toward +x at fall_ratio."""
    m = Mesh(c.id, color=c.color)
    g = {p.name: p.value for p in c.params}
    x0, x1, yb, yt = g["x0"], g["x1"], g["y_bottom"], g["y_top_x0"]
    yt1 = yt - g["fall_ratio"] * (x1 - x0)
    if yt1 <= yb:
        raise ValueError(f"wedge: {c.id} fall consumes full depth "
                         f"(top {yt1:.1f} <= bottom {yb:.1f})")
    _prism(m, [(x0, yb), (x1, yb), (x1, yt1), (x0, yt)], z0, z1)
    return m


def gen_annotation(c: Component, z0: float, z1: float) -> Mesh:
    return Mesh(c.id, color=c.color)  # no geometry by design


def gen_post(c: Component, z0: float, z1: float, sides: int = 8) -> Mesh:
    """Vertical prism (octagon in x-z plane, extruded along y): rods, posts.
    Threading is not modelled; params cx/cz/y0/y1/r."""
    m = Mesh(c.id, color=c.color)
    g = {p.name: p.value for p in c.params}
    ring = [(g["cx"] + g["r"] * math.cos(2 * math.pi * i / sides),
             g["cz"] + g["r"] * math.sin(2 * math.pi * i / sides))
            for i in range(sides)]
    y0, y1 = g["y0"], g["y1"]
    for i in range(sides):
        x0, z0p = ring[i]
        x1, z1p = ring[(i + 1) % sides]
        _quad(m, (x0, y0, z0p), (x1, y0, z1p), (x1, y1, z1p), (x0, y1, z0p))
    for y, flip in ((y0, False), (y1, True)):
        p0 = ring[0]
        for i in range(1, sides - 1):
            p1, p2 = ring[i], ring[i + 1]
            if flip:
                _tri(m, (p0[0], y, p0[1]), (p1[0], y, p1[1]),
                     (p2[0], y, p2[1]))
            else:
                _tri(m, (p0[0], y, p0[1]), (p2[0], y, p2[1]),
                     (p1[0], y, p1[1]))
    return m


# ---------------------------------------------------------------- spec v2 shapes

def _area2(poly: list[tuple[float, float]]) -> float:
    """Twice the signed area (positive = counter-clockwise)."""
    return sum(x0 * y1 - x1 * y0
               for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]))


def _cross(o, a, b) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def triangulate(poly: list[tuple[float, float]]) -> list[tuple[int, int, int]]:
    """Ear clipping for a simple polygon of any winding; returns CCW index
    triples (concave outlines such as L-angles and channels are fine)."""
    idx = list(range(len(poly)))
    if _area2(poly) < 0:
        idx.reverse()
    tris: list[tuple[int, int, int]] = []
    eps = 1e-9
    while len(idx) > 3:
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = poly[i0], poly[i1], poly[i2]
            if _cross(a, b, c) <= eps:
                continue  # reflex or collinear corner: not an ear
            if any(_cross(a, b, poly[j]) >= -eps and _cross(b, c, poly[j]) >= -eps
                   and _cross(c, a, poly[j]) >= -eps
                   for j in idx if j not in (i0, i1, i2) and poly[j] not in (a, b, c)):
                continue  # another vertex inside: not an ear
            tris.append((i0, i1, i2))
            del idx[k]
            break
        else:
            # no ear: drop a collinear vertex if there is one, else give up
            flat = [k for k in range(len(idx))
                    if abs(_cross(poly[idx[k - 1]], poly[idx[k]],
                                  poly[idx[(k + 1) % len(idx)]])) <= eps]
            if not flat:
                raise ValueError("profile outline self-intersects")
            del idx[flat[0]]
    if len(idx) == 3 and _cross(poly[idx[0]], poly[idx[1]], poly[idx[2]]) > eps:
        tris.append((idx[0], idx[1], idx[2]))
    return tris


def _tri_safe(m: Mesh, a, b, c) -> None:
    """Skip zero-area triangles (revolve poles, collinear points)."""
    u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
    if n[0] * n[0] + n[1] * n[1] + n[2] * n[2] > 1e-12:
        _tri(m, a, b, c)


def _extrude(m: Mesh, poly: list[tuple[float, float]], z0: float, z1: float) -> None:
    if _area2(poly) < 0:
        poly = poly[::-1]
    n = len(poly)
    for i in range(n):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % n]
        _quad(m, (x0, y0, z0), (x1, y1, z0), (x1, y1, z1), (x0, y0, z1))
    for a, b, c in triangulate(poly):
        _tri(m, (*poly[a], z1), (*poly[b], z1), (*poly[c], z1))
        _tri(m, (*poly[a], z0), (*poly[c], z0), (*poly[b], z0))


def gen_profile(c: Component, z0: float, z1: float) -> Mesh:
    """Any closed 2D outline in the section plane (x, y), extruded along z."""
    m = Mesh(c.id, color=c.color)
    poly = [(p.x, p.y) for p in c.path]
    if len(poly) > 3 and poly[0] == poly[-1]:
        poly = poly[:-1]
    if len(poly) < 3 or abs(_area2(poly)) < 1e-6:
        raise ValueError(f"profile: {c.id} needs a closed outline of >= 3 points")
    _extrude(m, poly, z0, z1)
    return m


def _revolve_into(m: Mesh, cx: float, cz: float,
                  prof: list[tuple[float, float]], segs: int = 24) -> None:
    """Lathe a (radius, y) profile around the vertical axis at (cx, cz)."""
    ring = [(math.cos(2 * math.pi * j / segs), math.sin(2 * math.pi * j / segs))
            for j in range(segs + 1)]
    pt = lambda r, y, j: (cx + r * ring[j][0], y, cz + r * ring[j][1])
    for j in range(segs):
        for (r0, y0), (r1, y1) in zip(prof, prof[1:]):
            a, b = pt(r0, y0, j), pt(r1, y1, j)
            c_, d = pt(r1, y1, j + 1), pt(r0, y0, j + 1)
            _tri_safe(m, a, b, c_)
            _tri_safe(m, a, c_, d)
    for r, y in (prof[0], prof[-1]):  # close open ends with discs
        if r > 1e-9:
            for j in range(segs):
                _tri_safe(m, (cx, y, cz), pt(r, y, j), pt(r, y, j + 1))


def gen_revolve(c: Component, z0: float, z1: float) -> Mesh:
    """Round parts (waste, tap body, flange): profile x = radius, y = height."""
    m = Mesh(c.id, color=c.color)
    g = {p.name: p.value for p in c.params}
    prof = [(p.x, p.y) for p in c.path]
    if len(prof) < 2 or any(r < 0 for r, _ in prof):
        raise ValueError(f"revolve: {c.id} needs >= 2 points with radius >= 0")
    _revolve_into(m, g["cx"], g["cz"], prof)
    return m


def gen_sweep(c: Component, z0: float, z1: float, sides: int = 12) -> Mesh:
    """Round tube of radius r along a 3D polyline (pipes, conduits)."""
    m = Mesh(c.id, color=c.color)
    r = {p.name: p.value for p in c.params}["r"]
    pts = [(p.x, p.y, p.z if p.z is not None else 0.0) for p in c.path]
    if len(pts) < 2 or r <= 0:
        raise ValueError(f"sweep: {c.id} needs >= 2 path points and r > 0")

    def sub(a, b):
        return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

    def unit(v):
        n = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
        if n < 1e-9:
            raise ValueError(f"sweep: {c.id} has repeated points")
        return (v[0] / n, v[1] / n, v[2] / n)

    def dot(a, b):
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

    def crs(a, b):
        return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])

    tans = [unit(sub(pts[min(i + 1, len(pts) - 1)], pts[max(i - 1, 0)]))
            for i in range(len(pts))]
    up = (0.0, 1.0, 0.0) if abs(tans[0][1]) < 0.9 else (1.0, 0.0, 0.0)
    n = unit(sub(up, tuple(dot(up, tans[0]) * t for t in tans[0])))
    rings = []
    for p, t in zip(pts, tans):  # parallel transport of the normal
        n = unit(sub(n, tuple(dot(n, t) * k for k in t)))
        b = crs(t, n)
        rings.append([tuple(p[k] + r * (math.cos(a) * n[k] + math.sin(a) * b[k]) for k in range(3))
                      for a in (2 * math.pi * j / sides for j in range(sides))])
    for r0, r1 in zip(rings, rings[1:]):
        for j in range(sides):
            _quad(m, r0[j], r0[(j + 1) % sides], r1[(j + 1) % sides], r1[j])
    for ring, p in ((rings[0], pts[0]), (rings[-1], pts[-1])):
        for j in range(sides):
            _tri(m, p, ring[(j + 1) % sides], ring[j])
    return m


def _envelope(c: Component) -> tuple[float, float, float, float, float, float]:
    g = {p.name: p.value for p in c.params}
    x0, x1, y0, y1, z0, z1 = (g[k] for k in ("x0", "x1", "y0", "y1", "z0", "z1"))
    if x1 <= x0 or y1 <= y0 or z1 <= z0:
        raise ValueError(f"fixture: {c.id} envelope must have positive size")
    return x0, x1, y0, y1, z0, z1


def _tub(m: Mesh, env, wall: float, base: float) -> None:
    x0, x1, y0, y1, z0, z1 = env
    _box_tris(m, x0, y0, z0, x1, y0 + base, z1)
    _box_tris(m, x0, y0 + base, z0, x0 + wall, y1, z1)
    _box_tris(m, x1 - wall, y0 + base, z0, x1, y1, z1)
    _box_tris(m, x0 + wall, y0 + base, z0, x1 - wall, y1, z0 + wall)
    _box_tris(m, x0 + wall, y0 + base, z1 - wall, x1 - wall, y1, z1)


def gen_fixture_bath(c: Component, z0: float, z1: float) -> Mesh:
    m = Mesh(c.id, color=c.color)
    env = _envelope(c)
    W, H, D = env[1] - env[0], env[3] - env[2], env[5] - env[4]
    _tub(m, env, min(60.0, 0.12 * min(W, D)), min(50.0, 0.25 * H))
    return m


def gen_fixture_shower_tray(c: Component, z0: float, z1: float) -> Mesh:
    m = Mesh(c.id, color=c.color)
    env = _envelope(c)
    W, H, D = env[1] - env[0], env[3] - env[2], env[5] - env[4]
    _tub(m, env, min(30.0, 0.1 * min(W, D)), 0.6 * H)
    return m


def gen_fixture_wc(c: Component, z0: float, z1: float) -> Mesh:
    """Cistern against the back (min z), pan reaching the front (max z)."""
    m = Mesh(c.id, color=c.color)
    x0, x1, y0, y1, ez0, ez1 = _envelope(c)
    W, H, D = x1 - x0, y1 - y0, ez1 - ez0
    cd = min(0.25 * D, 220.0)
    _box_tris(m, x0, y0 + 0.5 * H, ez0, x1, y1, ez0 + cd)
    R = min(0.42 * W, 0.4 * (D - cd))
    cx, cz, hp = (x0 + x1) / 2, ez1 - R, 0.5 * H
    _revolve_into(m, cx, cz, [(0.55 * R, y0), (0.6 * R, y0 + 0.3 * hp), (R, y0 + 0.85 * hp),
                              (R, y0 + hp), (0.85 * R, y0 + hp), (0.35 * R, y0 + 0.45 * hp)])
    _box_tris(m, cx - 0.35 * R, y0, ez0 + cd, cx + 0.35 * R, y0 + 0.9 * hp, cz)
    return m


def gen_fixture_basin(c: Component, z0: float, z1: float) -> Mesh:
    m = Mesh(c.id, color=c.color)
    x0, x1, y0, y1, ez0, ez1 = _envelope(c)
    H, R = y1 - y0, min(x1 - x0, ez1 - ez0) / 2
    _revolve_into(m, (x0 + x1) / 2, (ez0 + ez1) / 2,
                  [(0.3 * R, y0), (0.55 * R, y0 + 0.35 * H), (R, y1 - 0.1 * H), (R, y1),
                   (0.9 * R, y1), (0.35 * R, y0 + 0.45 * H)])
    return m


def gen_fixture_vanity(c: Component, z0: float, z1: float) -> Mesh:
    m = Mesh(c.id, color=c.color)
    x0, x1, y0, y1, ez0, ez1 = _envelope(c)
    top = min(30.0, 0.05 * (y1 - y0))
    kick = min(80.0, 0.12 * (y1 - y0))
    _box_tris(m, x0, y1 - top, ez0, x1, y1, ez1)
    _box_tris(m, x0, y0 + kick, ez0, x1, y1 - top, ez1 - 20)
    _box_tris(m, x0, y0, ez0, x1, y0 + kick, ez1 - 70)
    return m


def gen_fixture_door(c: Component, z0: float, z1: float) -> Mesh:
    """Frame (two jambs + head) filling the envelope, leaf centred in depth."""
    m = Mesh(c.id, color=c.color)
    x0, x1, y0, y1, ez0, ez1 = _envelope(c)
    j = min(40.0, 0.1 * (x1 - x0))
    _box_tris(m, x0, y0, ez0, x0 + j, y1, ez1)
    _box_tris(m, x1 - j, y0, ez0, x1, y1, ez1)
    _box_tris(m, x0 + j, y1 - j, ez0, x1 - j, y1, ez1)
    t = min(40.0, 0.4 * (ez1 - ez0))
    zc = (ez0 + ez1) / 2
    _box_tris(m, x0 + j + 3, y0 + 10, zc - t / 2, x1 - j - 3, y1 - j - 3, zc + t / 2)
    return m


FIXTURES = ("bath", "shower_tray", "wc", "basin", "vanity", "door")

LEGACY_FIXTURE_REGISTRY = {
    "fixture_bath": gen_fixture_bath,
    "fixture_shower_tray": gen_fixture_shower_tray,
    "fixture_wc": gen_fixture_wc,
    "fixture_basin": gen_fixture_basin,
    "fixture_vanity": gen_fixture_vanity,
    "fixture_door": gen_fixture_door,
}

REGISTRY = {
    "profile": gen_profile,
    "revolve": gen_revolve,
    "sweep": gen_sweep,
    "box": gen_box,
    "l_angle": gen_l_angle,
    "sheet_follow": gen_sheet_follow,
    "bead": gen_bead,
    "rod": gen_rod,
    "post": gen_post,
    "fillet": gen_fillet,
    "wedge": gen_wedge,
    "annotation": gen_annotation,
}


def load_cad_mesh(spec: DetailSpec, c: Component) -> Mesh:
    """AI-modelled part: triangles produced in the CAD sandbox at generation
    time, stored beside the library record (absolute mm, Setout axes)."""
    from .store import var_root  # local: geometry stays storage-agnostic

    info = (spec.context.get("cad_parts") or {}).get(c.id)
    if not info:
        raise ValueError(f"{c.id}: AI-modelled part has no generated mesh")
    base = (var_root() / "library").resolve()
    path = (base / str(info["file"])).resolve()
    if not path.is_relative_to(base) or path.suffix != ".json" or not path.is_file():
        raise ValueError(f"{c.id}: CAD mesh file must be a .json inside the library")
    data = json.loads(path.read_text())
    m = Mesh(c.id, color=c.color)
    m.positions = [float(v) for v in data["positions"]]
    return m


def build_assembly(spec: DetailSpec, use_display: bool = False
                   ) -> dict[str, Mesh]:
    extent = float(spec.context.get("illustrative_extent_z_mm", 600))
    z0, z1 = 0.0, extent
    meshes: dict[str, Mesh] = {}
    for comp in spec.components:
        gid = comp.generator.id
        if gid == "cad_mesh":
            meshes[comp.id] = load_cad_mesh(spec, comp)
            continue
        generator = REGISTRY.get(gid)
        if generator is None and spec.context.get("program_version") == "2.0":
            # Transitional v2 import only; v3 authoring never accepts fixtures,
            # and archived versions are served from immutable scene snapshots.
            generator = LEGACY_FIXTURE_REGISTRY.get(gid)
        if generator is None:
            raise ValueError(f"unknown generator {gid!r} on {comp.id}")
        if comp.generator.version != GEN_VERSION:
            raise ValueError(f"{comp.id}: generator version "
                             f"{comp.generator.version!r} != {GEN_VERSION!r}")
        # spec v2 parts may carry their own z extent (rooms run in x and z)
        g = {p.name: p.value for p in comp.params}
        cz0, cz1 = (g["z0"], g["z1"]) if "z0" in g and "z1" in g else (z0, z1)
        if cz1 <= cz0:
            raise ValueError(f"{comp.id}: z1 {cz1} <= z0 {cz0}")
        if gid == "sheet_follow":
            meshes[comp.id] = gen_sheet_follow(comp, cz0, cz1,
                                               use_display=use_display)
        else:
            meshes[comp.id] = generator(comp, cz0, cz1)
    return meshes


# ---------------------------------------------------------------- GLB export

def _hex(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def export_glb(meshes: dict[str, Mesh], order: list[str]) -> bytes:
    """Minimal binary glTF (GLB): one node+mesh per component, metres, with
    computed flat normals (non-indexed). No external dependency."""
    bin_blob = bytearray()
    buffer_views = []
    accessors = []
    gl_meshes = []
    nodes = []

    def push_float_array(data: list[float]) -> int:
        raw = struct.pack(f"<{len(data)}f", *data)
        offset = len(bin_blob)
        bin_blob.extend(raw)
        buffer_views.append({"buffer": 0, "byteOffset": offset,
                             "byteLength": len(raw)})
        return len(buffer_views) - 1

    for i, cid in enumerate(order):
        m = meshes[cid]
        if not m.positions:
            nodes.append({"name": cid})  # annotation: empty node keeps the ID
            continue
        pos = [mm_to_m(v) for v in m.positions]
        nrm: list[float] = []
        for t in range(0, len(pos), 9):
            ax, ay, az = pos[t:t + 3]
            bx, by, bz = pos[t + 3:t + 6]
            cx, cy, cz = pos[t + 6:t + 9]
            ux, uy, uz = bx - ax, by - ay, bz - az
            vx, vy, vz = cx - ax, cy - ay, cz - az
            nx, ny, nz = (uy * vz - uz * vy, uz * vx - ux * vz,
                          ux * vy - uy * vx)
            L = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            nrm.extend([nx / L, ny / L, nz / L] * 3)
        count = len(pos) // 3
        bv_p = push_float_array(pos)
        bv_n = push_float_array(nrm)
        ai_p = len(accessors)
        accessors.append({"bufferView": bv_p, "componentType": 5126,
                          "count": count, "type": "VEC3",
                          "max": [max(pos[0::3]), max(pos[1::3]),
                                  max(pos[2::3])],
                          "min": [min(pos[0::3]), min(pos[1::3]),
                                  min(pos[2::3])]})
        ai_n = len(accessors)
        accessors.append({"bufferView": bv_n, "componentType": 5126,
                          "count": count, "type": "VEC3"})
        r, g, b = _hex(m.color)
        gl_meshes.append({"name": cid, "primitives": [{
            "attributes": {"POSITION": ai_p, "NORMAL": ai_n},
            "material": i, "mode": 4}]})
        nodes.append({"name": cid, "mesh": len(gl_meshes) - 1,
                      "translation": [0, 0, 0]})
    materials = []
    for cid in order:
        r, g, b = _hex(meshes[cid].color)
        materials.append({"name": cid, "pbrMetallicRoughness": {
            "baseColorFactor": [r, g, b, 1.0], "metallicFactor": 0.05,
            "roughnessFactor": 0.85}})
    gltf = {
        "asset": {"version": "2.0",
                  "generator": f"setout {GEN_VERSION}"},
        "scene": 0, "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes, "meshes": gl_meshes, "materials": materials,
        "buffers": [{"byteLength": len(bin_blob)}],
        "bufferViews": buffer_views, "accessors": accessors,
    }
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    binary = bytes(bin_blob) + b"\x00" * (-len(bin_blob) % 4)
    header = struct.pack("<III", 0x46546C67, 2,
                         12 + 8 + len(js) + 8 + len(binary))
    return (header + struct.pack("<II", len(js), 0x4E4F534A) + js
            + struct.pack("<II", len(binary), 0x004E4942) + binary)


def export_scene_json(spec: DetailSpec, meshes: dict[str, Mesh]) -> dict:
    """Viewer payload: per-component triangles (mm) + full provenance."""
    solids = []
    for comp in spec.components:
        m = meshes[comp.id]
        solids.append({
            "id": comp.id, "name": comp.type, "source_label": comp.source_label,
            "representation": comp.representation, "layer": comp.type,
            "color": comp.color, "note": comp.note,
            "tris": m.positions, "tri_count": m.tri_count,
            "bounds": m.bounds(),
            "params": [p.model_dump() for p in comp.params],
            "product": comp.product.model_dump() if comp.product else None,
            "install_after": comp.install_after,
            "source_regions": [r.model_dump() for r in comp.source_regions],
        })
    return {
        "detail_id": spec.detail_id, "title": spec.title, "units": "mm",
        "viewer_scale": 0.01, "origin": spec.origin,
        "source": spec.source.model_dump(),
        "context": spec.context,
        "install_order": spec.install_order(),
        "relations": [r.model_dump() for r in spec.relations],
        "unresolved": [u.model_dump() for u in spec.unresolved],
        "verification": [c.model_dump() for c in spec.verification],
        "solids": solids,
    }
