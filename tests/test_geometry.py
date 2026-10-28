"""Geometry is dimension-driven: measure built meshes back against spec."""

import struct

from app.geometry import build_assembly, export_glb
from app.seed import door_sill_d3


def _bounds(meshes, cid):
    return meshes[cid].bounds()


def test_source_step_built():
    m = build_assembly(door_sill_d3())
    up, lo = _bounds(m, "slab_upper"), _bounds(m, "slab_lower")
    assert up[4] - lo[4] == 100  # slab top faces differ by the 100mm step


def test_source_gap_built():
    m = build_assembly(door_sill_d3())
    b = _bounds(m, "sealant")
    assert round(b[3] - b[0], 6) == 6


def test_source_fall_built():
    m = build_assembly(door_sill_d3())
    pos = m["screed"].positions
    xs, ys = pos[0::3], pos[1::3]
    x0, x1 = min(xs), max(xs)
    top_lo = max(y for x, y in zip(xs, ys) if x < x0 + 1.0)
    top_hi = max(y for x, y in zip(xs, ys) if x > x1 - 1.0)
    assert abs((top_lo - top_hi) / (x1 - x0) - 1 / 80) < 1e-9


def test_membrane_is_continuous_sheet_not_block():
    m = build_assembly(door_sill_d3())
    mem = m["membrane"]
    assert mem.tri_count > 20  # ribbon segments + caps, not a 12-tri box
    xs = mem.positions[0::3]
    ys = mem.positions[1::3]
    # follows upstand, sill, step face and exterior run
    assert min(ys) < -90 and max(ys) > 80
    assert min(xs) < -270 and max(xs) > 500


def test_sheet_tris_are_ribbon_only():
    # 6-point path -> 5 segments -> 10 side quads + 2 end caps = 24 tris.
    # Guards against cap fans spanning the ribbon's concave folds.
    m = build_assembly(door_sill_d3())
    assert m["membrane"].tri_count == 24
    assert m["slip_sheet"].tri_count == 8
    assert m["protection_v"].tri_count == 8


def test_fillet_is_triangular_prism():
    m = build_assembly(door_sill_d3())
    assert m["fillet"].tri_count == 8  # 3 sides + 2 caps


def test_annotations_have_no_geometry():
    m = build_assembly(door_sill_d3())
    for cid in ("drainage", "weep", "termination"):
        assert m[cid].tri_count == 0


def test_ids_stable_across_builds():
    a = build_assembly(door_sill_d3())
    b = build_assembly(door_sill_d3())
    assert set(a) == set(b)
    for cid in a:
        assert a[cid].positions == b[cid].positions


def test_param_change_moves_geometry():
    spec = door_sill_d3()
    base = build_assembly(spec)["slab_lower"].bounds()
    spec.component("slab_lower").param("step_height").value = 120
    spec.component("slab_lower").param("step_height").provenance = "user_input"
    moved = build_assembly(spec)["slab_lower"].bounds()
    assert base[4] - moved[4] == 20


def test_glb_roundtrip_header():
    glb = export_glb(build_assembly(door_sill_d3()),
                     [c.id for c in door_sill_d3().components])
    magic, version, length = struct.unpack("<III", glb[:12])
    assert magic == 0x46546C67 and version == 2 and length == len(glb)
    assert b"membrane" in glb  # stable component IDs preserved in nodes
