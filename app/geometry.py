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


REGISTRY = {
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


def build_assembly(spec: DetailSpec, use_display: bool = False
                   ) -> dict[str, Mesh]:
    extent = float(spec.context.get("illustrative_extent_z_mm", 600))
    z0, z1 = 0.0, extent
    meshes: dict[str, Mesh] = {}
    for comp in spec.components:
        gid = comp.generator.id
        if gid not in REGISTRY:
            raise ValueError(f"unknown generator {gid!r} on {comp.id}")
        if comp.generator.version != GEN_VERSION:
            raise ValueError(f"{comp.id}: generator version "
                             f"{comp.generator.version!r} != {GEN_VERSION!r}")
        if gid == "sheet_follow":
            meshes[comp.id] = gen_sheet_follow(comp, z0, z1,
                                               use_display=use_display)
        else:
            meshes[comp.id] = REGISTRY[gid](comp, z0, z1)
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
