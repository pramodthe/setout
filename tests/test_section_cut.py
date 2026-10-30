"""Section cuts are measured from built meshes; the DXF house style is data."""

import json
from collections import Counter

import ezdxf
import pytest

from app import section_cut as sc
from app.geometry import GEN_VERSION, Mesh, build_assembly, gen_box
from app.seed import door_sill_d3
from app.spec import Component, GeneratorRef, Param, Provenance

A = Provenance.ASSUMPTION

STYLE = sc.Style(
    by_class={"membrane": sc.LineStyle(layer="MEMBRANE", linetype="MEMBRANE_LT", color=5,
                                       pattern=(12.0, 10.0, -2.0))},
    by_role={"substrate": sc.LineStyle(layer="SUBSTRATE", linetype="DASHED", color=8,
                                       hatch="ANSI31", hatch_scale=10)},
    default=sc.LineStyle(layer="CUT"))


def _box(x0, y0, z0, x1, y1, z1):
    comp = Component(id="b", type="t", representation="solid",
                     generator=GeneratorRef(id="box", version=GEN_VERSION),
                     params=[Param(name=n, value=v, provenance=A)
                             for n, v in (("x0", x0), ("x1", x1), ("y0", y0), ("y1", y1))])
    return gen_box(comp, z0, z1)


def _d3_section():
    spec = door_sill_d3()
    section = sc.cut(build_assembly(spec), (0, 0, 300), (0, 0, 1))
    return sc.annotate(section, spec, class_ids={"membrane": "membrane"},
                       roles={"slab_upper": "substrate", "slab_lower": "substrate"})


# ---------------------------------------------------------------- the cut

def test_box_cut_is_one_rectangle():
    section = sc.cut({"b": _box(-10, 5, 0, 30, 25, 600)}, (0, 0, 300), (0, 0, 1))
    assert list(section.parts) == ["b"] and section.missed == []
    part = section.parts["b"]
    assert len(part.loops) == 1 and len(part.loops[0]) == 4  # diagonal vertices removed
    assert part.bounds() == (-10, 5, 30, 25)
    assert part.area() == 800  # outer loop counter-clockwise
    assert part.open_paths == [] and part.dropped == 0 and section.notes == []


def test_plan_cut_reads_x_right_and_minus_z_up():
    section = sc.cut({"b": _box(-10, 5, 0, 30, 25, 600)}, (0, 15, 0), (0, 1, 0))
    assert section.parts["b"].bounds() == (-10, -600, 30, 0)


def test_cut_seen_from_the_other_side_is_mirrored():
    section = sc.cut({"b": _box(-10, 5, 0, 30, 25, 600)}, (0, 0, 300), (0, 0, -1))
    assert section.parts["b"].bounds() == (-30, 5, 10, 25)


def test_plane_through_a_face_is_one_sided():
    # Vertices on the plane count as the normal's side: the top face shows as
    # its outline (the solid is behind the plane), the bottom face as nothing.
    m = _box(-10, 5, 0, 30, 25, 600)
    top = sc.cut({"b": m}, (0, 25, 0), (0, 1, 0))
    assert len(top.parts["b"].loops) == 1 and top.parts["b"].area() == 24000
    bottom = sc.cut({"b": m}, (0, 5, 0), (0, 1, 0))
    assert bottom.parts == {} and bottom.missed == ["b"]


def test_ridge_the_plane_only_touches_cancels():
    # Two triangles below y=0 sharing an edge on it: each emits the edge, the
    # pair cancels, and the part is missed rather than a zero-area loop.
    m = Mesh("ridge", positions=[0, 0, 0, 10, 0, 0, 5, -5, 5,
                                 10, 0, 0, 0, 0, 0, 5, -5, -5])
    section = sc.cut({"ridge": m}, (0, 0, 0), (0, 1, 0))
    assert section.parts == {} and section.missed == ["ridge"]


def test_open_mesh_gives_an_open_path_and_a_note():
    m = Mesh("tri", positions=[0, -5, 0, 10, -5, 0, 5, 5, 0])  # one triangle across y=0
    section = sc.cut({"tri": m}, (0, 0, 0), (0, 1, 0))
    part = section.parts["tri"]
    assert part.loops == [] and len(part.open_paths) == 1 and len(part.open_paths[0]) == 2
    assert section.notes == ["tri: 1 open chain(s): the mesh is not closed where the plane cuts it"]


def test_d3_cut_hits_every_part_the_plane_crosses_and_no_other():
    spec = door_sill_d3()
    meshes = build_assembly(spec)
    z = spec.context["illustrative_extent_z_mm"] / 2
    section = sc.cut(meshes, (0, 0, z), (0, 0, 1))
    for cid, mesh in meshes.items():
        b = mesh.bounds()
        if b and b[2] < z < b[5]:
            assert section.parts[cid].loops and not section.parts[cid].open_paths, cid
        else:
            assert cid in section.missed and cid not in section.parts, cid
    assert sorted(section.missed) == ["drainage", "termination", "weep"]  # annotations
    assert section.notes == []
    # measured back out of the cut, the drawing's values
    sealant = section.parts["sealant"].bounds()
    assert sealant[2] - sealant[0] == 6
    assert section.parts["slab_upper"].bounds()[3] - section.parts["slab_lower"].bounds()[3] == 100
    assert len(section.parts["rod"].loops[0]) == 8  # the octagon keeps its corners


def test_cut_across_the_sill_misses_the_parts_beyond_it():
    section = sc.cut(build_assembly(door_sill_d3()), (-300, 0, 0), (1, 0, 0))
    assert list(section.parts) == ["slab_upper"]
    assert "slab_lower" in section.missed and "membrane" in section.missed


def test_plane_that_misses_everything_is_an_empty_drawing(tmp_path):
    meshes = build_assembly(door_sill_d3())
    section = sc.cut(meshes, (0, 0, 10_000), (0, 0, 1))
    assert section.parts == {} and sorted(section.missed) == sorted(meshes)
    assert section.bounds() is None and section.dimensions() == []
    doc = sc.to_dxf(section, tmp_path / "empty.dxf", STYLE)
    assert len(doc.modelspace()) == 0


# ---------------------------------------------------------------- annotation

def test_annotate_places_only_drawing_values_the_section_measures():
    spec = door_sill_d3()
    section = _d3_section()
    dims = {(d.part_id, d.name): d for d in section.dimensions()}
    assert set(dims) == {("slab_lower", "step_height"), ("sealant", "width")}
    step = dims[("slab_lower", "step_height")]
    assert step.text == "100" and step.axis == "v" and step.quote == "100mm minimum step"
    assert step.p2[1] - step.p1[1] == 100 and step.p1[0] == step.p2[0]  # an aligned pair
    assert dims[("sealant", "width")].text == "6"
    for d in section.dimensions():  # text is the param's own value, nothing else
        assert d.value == spec.component(d.part_id).param(d.name).value
    assert section.unplaced == [("screed", "fall_ratio", "unit 'ratio' is not a length")]
    assert section.parts["membrane"].label == "Waterproof membrane"
    assert section.parts["slab_upper"].label is None
    assert section.parts["membrane"].class_id == "membrane"
    assert section.parts["slab_lower"].role == "substrate"
    assert section.parts["packer"].role is None


def test_printed_values_have_no_trailing_zeros():
    assert sc.printed(100.0) == "100" and sc.printed(1.5) == "1.5" and sc.printed(0.25) == "0.25"


def test_section_serialises():
    data = json.loads(json.dumps(_d3_section().to_dict()))
    assert data["units"] == "mm" and len(data["parts"]) == 14
    assert data["parts"][0]["id"] == "slab_upper" and data["missed"] == ["drainage", "weep", "termination"]


# ---------------------------------------------------------------- DXF

def test_dxf_round_trips_with_expected_entities(tmp_path):
    section = _d3_section()
    out = tmp_path / "d3.dxf"
    sc.to_dxf(section, out, STYLE)
    doc = ezdxf.readfile(out)
    msp = doc.modelspace()
    loops = sum(len(p.loops) + len(p.open_paths) for p in section.parts.values())
    assert len(msp.query("LWPOLYLINE")) == loops == 14
    labelled = [p for p in section.parts.values() if p.label]
    assert len(msp.query("MTEXT")) == len(msp.query("LEADER")) == len(labelled) == 11
    assert [e.text for e in msp.query("MTEXT")] == [p.label for p in labelled]  # verbatim
    assert sorted(e.dxf.text for e in msp.query("DIMENSION")) == ["100", "6"]
    assert len(msp.query("HATCH")) == 2  # the two substrates
    assert doc.linetypes.has_entry("MEMBRANE_LT") and doc.linetypes.has_entry("DASHED")
    assert {e.dxf.layer for e in msp.query("LWPOLYLINE")} == {"MEMBRANE", "SUBSTRATE", "CUT"}
    assert len(msp.query('LWPOLYLINE[layer=="MEMBRANE"]')) == 1
    assert doc.layers.get("MEMBRANE").dxf.linetype == "MEMBRANE_LT"
    assert doc.layers.get("SUBSTRATE").dxf.color == 8
    assert doc.header["$INSUNITS"] == 4  # mm


def test_nts_scale_distorts_geometry_but_keeps_printed_text(tmp_path):
    doc = sc.to_dxf(_d3_section(), tmp_path / "nts.dxf", STYLE, scale=(2.0, 1.0))
    dims = {e.dxf.text: round(e.get_measurement(), 6) for e in doc.modelspace().query("DIMENSION")}
    assert dims == {"100": 100.0, "6": 12.0}  # u stretched, printed value unchanged


def test_two_styles_on_one_layer_still_resolve_apart(tmp_path):
    style = sc.Style(by_role={"substrate": sc.LineStyle(layer="CUT", linetype="DASHED", color=1)},
                     default=sc.LineStyle(layer="CUT"))
    doc = sc.to_dxf(_d3_section(), tmp_path / "shared.dxf", style)
    layer = doc.layers.get("CUT")

    def resolved(e):  # explicit attribute, else the layer's (BYLAYER)
        lt = e.dxf.linetype if e.dxf.linetype.upper() != "BYLAYER" else layer.dxf.linetype
        return lt.upper(), (e.dxf.color if e.dxf.color != 256 else layer.dxf.color)

    kinds = Counter(resolved(e) for e in doc.modelspace().query("LWPOLYLINE"))
    assert kinds == {("DASHED", 1): 2, ("CONTINUOUS", 7): 12}


def test_unknown_linetype_or_hatch_is_refused(tmp_path):
    section = _d3_section()
    with pytest.raises(ValueError, match="linetype"):
        sc.to_dxf(section, tmp_path / "x.dxf", sc.Style(default=sc.LineStyle(layer="CUT", linetype="NOPE")))
    with pytest.raises(ValueError, match="hatch"):
        sc.to_dxf(section, tmp_path / "x.dxf", sc.Style(default=sc.LineStyle(layer="CUT", hatch="NOPE")))


def test_style_from_dict_and_lookup_order():
    style = sc.Style.from_dict({
        "by_class": {"c": {"layer": "C", "linetype": "X", "pattern": [1, 0.5, -0.5]}},
        "by_role": {"object": {"layer": "R"}}, "default": {"layer": "D"}, "text_height": 5})
    part = sc.SectionPart(id="p", color="#000000", class_id="c", role="object")
    assert style.for_part(part).layer == "C" and style.for_part(part).pattern == (1.0, 0.5, -0.5)
    part.class_id = None
    assert style.for_part(part).layer == "R"
    part.role = "zone"
    assert style.for_part(part).layer == "D" and style.text_height == 5


def test_png_preview_draws_something(tmp_path):
    img = sc.to_png(_d3_section(), tmp_path / "d3.png")
    assert (tmp_path / "d3.png").exists() and img.size[0] > 100
    assert len(img.getcolors(1 << 20)) > 1
    # no labels, no dimensions, and nothing at all, both still render
    bare = sc.cut({"b": _box(-10, 5, 0, 30, 25, 600)}, (0, 0, 300), (0, 0, 1))
    assert len(sc.to_png(bare, tmp_path / "bare.png").getcolors(1 << 20)) > 1
    empty = sc.cut({"b": _box(-10, 5, 0, 30, 25, 600)}, (0, 0, 900), (0, 0, 1))
    assert sc.to_png(empty, tmp_path / "empty.png").size[0] > 0
