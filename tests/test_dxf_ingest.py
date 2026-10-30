"""DXF ingest on a fixture drawn with ezdxf inside the test: no binary fixtures."""
import importlib.util
import json
from pathlib import Path

import ezdxf
import numpy as np
import pytest
from ezdxf import units
from ezdxf.math import Vec2
from ezdxf.render.mleader import ConnectionSide
from PIL import Image

from app import dxf_ingest

ROOT = Path(__file__).resolve().parents[1]
# The EZDXF dimstyle is a 1:100 style; print real millimetres at a readable size.
DIM = {"dimlfac": 1.0, "dimtxt": 2.5, "dimasz": 2.5}


def build_fixture(path):
    """A small sheet with every entity kind the ingest classifies. Returns name -> handle."""
    doc = ezdxf.new("R2018", setup=True)
    doc.units = units.MM
    doc.header["$LTSCALE"] = 10.0  # dashes visible at 96 dpi
    doc.layers.add("A", linetype="DASHED")  # the layer carries the linetype
    doc.styles.add("NOTES", font="LiberationSans-Regular.ttf")
    msp = doc.modelspace()
    h = {}
    h["inherit1"] = msp.add_line((0, 0), (100, 0), dxfattribs={"layer": "A"}).dxf.handle
    h["inherit2"] = msp.add_line((0, 20), (100, 20), dxfattribs={"layer": "A"}).dxf.handle
    h["explicit"] = msp.add_line((0, 40), (100, 40), dxfattribs={
        "linetype": "DASHDOT", "lineweight": 50, "color": 1}).dxf.handle
    hatch = msp.add_hatch(color=7)
    hatch.set_pattern_fill("ANSI31", scale=2.0)
    hatch.paths.add_polyline_path([(0, 60), (50, 60), (50, 90), (0, 90)])
    hatch.paths.add_polyline_path([(10, 70), (20, 70), (20, 80), (10, 80)])  # island
    h["hatch"] = hatch.dxf.handle
    solid = msp.add_hatch(color=7)
    solid.paths.add_polyline_path([(60, 60), (90, 60), (90, 90), (60, 90)])
    h["solid"] = solid.dxf.handle
    dim = msp.add_linear_dim(base=(0, -15), p1=(0, 0), p2=(100, 0), text="150 NTS", override=DIM)
    dim.render()
    h["dim_override"] = dim.dimension.dxf.handle
    dim = msp.add_linear_dim(base=(0, -30), p1=(0, 0), p2=(100, 0), override=DIM)
    dim.render()
    h["dim_measured"] = dim.dimension.dxf.handle
    mleader = msp.add_multileader_mtext()
    mleader.set_content("membrane")
    mleader.add_leader_line(ConnectionSide.left, [Vec2(50, 75)])
    mleader.build(insert=Vec2(120, 100))
    h["mleader"] = mleader.multileader.dxf.handle
    note = msp.add_text("sealant", height=2.5, dxfattribs={"insert": (120, 40), "style": "NOTES"})
    h["note"] = note.dxf.handle
    h["leader"] = msp.add_leader([(100, 40), (118, 41)], dxfattribs={
        "annotation_handle": note.dxf.handle}).dxf.handle
    h["pseudo_text"] = msp.add_text("grate", height=2.5, dxfattribs={"insert": (120, 20)}).dxf.handle
    h["pseudo_line"] = msp.add_line((119, 21), (100, 20)).dxf.handle  # one end at the text
    h["tile_text"] = msp.add_text("tile", height=2.5, dxfattribs={"insert": (120, 60)}).dxf.handle
    h["pseudo_poly"] = msp.add_lwpolyline([(92, 70), (112, 61), (119, 61)]).dxf.handle  # with a landing
    block = doc.blocks.new("FIXING")
    block.add_circle((0, 0), 2)
    block.add_line((-3, 0), (3, 0), dxfattribs={"linetype": "BYBLOCK"})
    h["insert"] = msp.add_blockref("FIXING", (150, 0)).dxf.handle
    h["insert_dot"] = msp.add_blockref("FIXING", (150, 20), dxfattribs={"linetype": "DOT"}).dxf.handle
    h["mtext"] = msp.add_mtext("screed\\Pbed", dxfattribs={"insert": (150, 60), "char_height": 3}).dxf.handle
    # a right-attached callout reaching left of all geometry, and a vertical dimension:
    # the page must be sized from what is drawn, not from ezdxf's text estimate
    h["callout"] = msp.add_mtext("waterproof membrane", dxfattribs={
        "insert": (-5, 100), "char_height": 3, "attachment_point": 3}).dxf.handle  # TOP_RIGHT
    dim = msp.add_linear_dim(base=(175, 0), p1=(160, 0), p2=(160, 20), angle=90, override=DIM)
    dim.render()
    h["dim_vertical"] = dim.dimension.dxf.handle
    doc.saveas(path)
    return h


@pytest.fixture(scope="module")
def fixture(tmp_path_factory):
    path = tmp_path_factory.mktemp("dxf") / "fixture.dxf"
    handles = build_fixture(path)
    return path, dxf_ingest.load(path), handles


@pytest.fixture(scope="module")
def records(fixture):
    _, doc, _ = fixture
    return {r.handle: r for r in dxf_ingest.entities(doc)}


@pytest.fixture(scope="module")
def rendered(fixture, tmp_path_factory):
    _, doc, _ = fixture
    png = tmp_path_factory.mktemp("render") / "sheet.png"
    info = dxf_ingest.render(doc, dpi=96, out_png=png)
    ink = np.asarray(Image.open(png)) < 128
    return info, ink, dxf_ingest.entity_masks(doc, info)


def test_load_refuses_dwg(tmp_path):
    dwg = tmp_path / "detail.dwg"
    dwg.write_bytes(b"AC1032")
    with pytest.raises(ValueError, match="converted to DXF"):
        dxf_ingest.load(dwg)


def test_linetypes_resolve_through_layer_and_entity(fixture, records):
    _, _, h = fixture
    inherited = records[h["inherit1"]]
    assert (inherited.linetype, inherited.linetype_explicit, inherited.linetype_source) == ("DASHED", False, "layer")
    assert inherited.layer == "A"
    assert not inherited.lineweight_explicit and inherited.lineweight == 0.25
    assert not inherited.color_explicit and inherited.aci == 256
    explicit = records[h["explicit"]]
    assert (explicit.linetype, explicit.linetype_explicit, explicit.linetype_source) == ("DASHDOT", True, "entity")
    assert explicit.lineweight_explicit and explicit.lineweight == 0.5
    assert explicit.color_explicit and explicit.color == "#ff0000"
    assert records[h["insert_dot"]].linetype == "DOT"
    assert records[h["solid"]].color == "#000000"  # ACI 7 on a white sheet


def test_text_dimension_leader_block_and_hatch_records(fixture, records):
    _, _, h = fixture
    override = records[h["dim_override"]]
    assert (override.text, override.dim_kind) == ("150 NTS", "override")
    assert override.dim_measurement == 100.0 and override.dim_lfac == 1.0  # the NTS ratio is exposed
    measured = records[h["dim_measured"]]
    assert (measured.text, measured.dim_kind, measured.dim_measurement) == ("100", "measured", 100.0)
    assert records[h["mleader"]].type == "MULTILEADER"
    assert records[h["mleader"]].text == "membrane"
    assert records[h["leader"]].annotation == h["note"]
    assert (records[h["note"]].text, records[h["note"]].style) == ("sealant", "NOTES")
    assert records[h["mtext"]].text == "screed\nbed"
    assert records[h["hatch"]].hatch_pattern == "ANSI31"
    assert records[h["solid"]].hatch_pattern == "SOLID"
    assert records[h["insert"]].block == "FIXING"
    assert records[h["inherit1"]].bbox == (0.0, 0.0, 100.0, 0.0)
    assert not hasattr(override, "label")  # the ingest carries no class


def test_pseudo_leaders_link_one_line_to_one_text(fixture):
    _, doc, h = fixture
    assert dxf_ingest.pseudo_leaders(doc) == [(h["pseudo_line"], h["pseudo_text"]),
                                              (h["pseudo_poly"], h["tile_text"])]


def test_census_counts(fixture):
    path, _, _ = fixture
    c = dxf_ingest.census([path])
    assert c["file_count"] == 1 and c["errors"] == {}
    assert c["entities"] == 19
    assert c["entity_types"] == {"LINE": 4, "LWPOLYLINE": 1, "HATCH": 2, "DIMENSION": 3,
                                 "MULTILEADER": 1, "TEXT": 3, "LEADER": 1, "INSERT": 2, "MTEXT": 2}
    assert c["linetypes"]["DASHED"] == {"explicit": 0, "inherited": 2}
    assert c["linetypes"]["DASHDOT"] == {"explicit": 1, "inherited": 0}
    assert c["linetypes"]["DOT"] == {"explicit": 1, "inherited": 0}
    assert c["linetype_explicit"] == 2 and c["linetype_inherited"] == 17
    assert c["hatch_patterns"] == {"ANSI31": 1, "SOLID": 1}
    assert c["leaders"] == {"LEADER": 1, "MLEADER": 1, "line_plus_text": 2}
    assert c["dimensions"] == {"total": 3, "measured": 2, "override": 1, "suppressed": 0}
    assert c["blocks"]["inserts"] == {"FIXING": 2}
    assert c["blocks"]["definitions"] >= 1 and c["blocks"]["anonymous"] >= 2  # *D dimension blocks
    assert c["text"]["styles"] == {"NOTES": 1, "Standard": 4}
    assert c["layers"] == {"count": 3, "linetypes": {"CONTINUOUS": 2, "DASHED": 1}}
    assert c["units"] == units.MM
    per_file = c["files"][str(path)]
    assert per_file["entities"] == 19 and per_file["units_name"] == "Millimeters"
    assert per_file["dxfversion"] == "AC1032"


def test_census_lists_unreadable_files(tmp_path):
    bad = tmp_path / "broken.dxf"
    bad.write_text("not a dxf")
    c = dxf_ingest.census([bad])
    assert c["file_count"] == 0 and c["entities"] == 0 if "entities" in c else True
    assert list(c["errors"]) == [str(bad)]


def test_render_transform_and_png(rendered):
    info, ink, _ = rendered
    assert Path(info.path).exists()
    assert ink.shape == (info.height, info.width)
    assert ink.any()
    assert not info.units_assumed and info.units_name == "Millimeters"
    assert info.px_per_unit == pytest.approx(96 / 25.4)
    x0, y0, x1, y1 = info.extents
    assert info.to_pixel(x0, y1) == pytest.approx((info.margin_px, info.margin_px))
    px, py = info.to_pixel(x1, y0)
    assert info.width - info.margin_px - 1 <= px <= info.width - info.margin_px
    assert info.height - info.margin_px - 1 <= py <= info.height - info.margin_px
    assert info.to_drawing(*info.to_pixel(12.5, -3.0)) == pytest.approx((12.5, -3.0))
    assert info.as_dict()["dpi"] == 96


def test_render_caps_size(fixture):
    _, doc, _ = fixture
    info = dxf_ingest.render_info(doc, dpi=96, max_px=200)
    assert info.capped and max(info.width, info.height) <= 200


def test_page_holds_everything_drawn(fixture, rendered):
    """Callouts attached to the right of their insert point and dimension text
    reach beyond ezdxf's bbox estimate; the page is sized from what is drawn."""
    _, doc, _ = fixture
    info, ink, _ = rendered
    x0, _, _, _ = info.extents
    assert x0 < -30  # the right-attached callout, well left of the geometry at x >= -2
    band = info.margin_px - 2  # strokes at the edge may spill a pixel or two
    assert not ink[:band, :].any() and not ink[-band:, :].any()
    assert not ink[:, :band].any() and not ink[:, -band:].any()
    rows, cols = np.nonzero(ink)  # and the page is tight around the drawing
    assert rows.min() <= info.margin_px + 1 and cols.min() <= info.margin_px + 1
    assert rows.max() >= info.height - info.margin_px - 2
    assert cols.max() >= info.width - info.margin_px - 2
    assert dxf_ingest.drawn_extents(ezdxf.new("R2018")) is None


def test_unitless_file_is_rendered_as_millimetres_and_says_so(tmp_path):
    doc = ezdxf.new("R2018")
    doc.units = 0  # ezdxf.new defaults $INSUNITS to metres; exported files are often unitless
    doc.modelspace().add_line((0, 0), (10, 0))
    info = dxf_ingest.render_info(doc, dpi=96)
    assert info.units_assumed and info.units_name == "Unitless"
    assert info.px_per_unit == pytest.approx(96 / 25.4)


def test_masks_are_nonempty_aligned_and_disjoint(fixture, records, rendered):
    _, _, h = fixture
    info, ink, masks = rendered
    assert set(masks) == set(records)  # every fixture entity draws something
    for mask in masks.values():
        x, y, w, h_ = mask.box
        assert mask.area > 0
        assert 0 <= x and 0 <= y and x + w <= info.width and y + h_ <= info.height
        assert mask.bitmap.size == (w, h_)
    # separate entities never share a pixel
    assert not masks[h["inherit1"]].intersects(masks[h["inherit2"]])
    assert not masks[h["note"]].intersects(masks[h["pseudo_text"]])
    # a dashed line is drawn with its gaps
    dashed = masks[h["inherit1"]]
    assert dashed.area < dashed.box[2]
    # lines, text, blocks and dimensions mask exactly the pixels the sheet shows
    for name in ("inherit1", "explicit", "solid", "dim_override", "mleader", "note", "insert_dot", "mtext"):
        full = masks[h[name]].full(info.width, info.height)
        assert not np.any(full & ~ink), name


def test_hatch_region_mask_excludes_islands_and_pattern_mask_matches_sheet(fixture, rendered):
    _, doc, h = fixture
    info, ink, masks = rendered
    region = masks[h["hatch"]].full(info.width, info.height)
    inside = info.to_pixel(40, 65)
    island = info.to_pixel(15, 75)
    assert region[round(inside[1]), round(inside[0])]
    assert not region[round(island[1]), round(island[0])]
    pattern = dxf_ingest.entity_masks(doc, info, handles=[h["hatch"]], hatch="pattern")[h["hatch"]]
    assert 0 < pattern.area < masks[h["hatch"]].area
    assert not np.any(pattern.full(info.width, info.height) & ~ink)


def test_to_coco_writes_only_supplied_labels(fixture, rendered, tmp_path):
    _, _, h = fixture
    info, _, masks = rendered
    images = [{"id": 7, "file_name": "sheet.png", "width": info.width, "height": info.height}]
    labels = {7: {h["hatch"]: "cut_material", h["inherit1"]: "layer_line"}}
    out = tmp_path / "coco.json"
    dataset = dxf_ingest.to_coco(images, {7: masks}, labels, out)
    assert json.loads(out.read_text()) == dataset
    assert dataset["categories"] == [{"id": 1, "name": "cut_material"}, {"id": 2, "name": "layer_line"}]
    assert [a["dxf_handle"] for a in dataset["annotations"]] == [h["inherit1"], h["hatch"]]
    for annotation in dataset["annotations"]:
        mask = masks[annotation["dxf_handle"]]
        counts = annotation["segmentation"]["counts"]
        assert annotation["segmentation"]["size"] == [info.height, info.width]
        assert sum(counts) == info.width * info.height
        assert sum(counts[1::2]) == annotation["area"] == mask.area
        assert annotation["bbox"] == list(mask.box) and annotation["iscrowd"] == 0
    with pytest.raises(ValueError, match="not in categories"):
        dxf_ingest.to_coco(images, {7: masks}, labels, out, categories=["cut_material"])


def test_rle_roundtrip_matches_the_mask(rendered):
    info, _, masks = rendered
    mask = next(iter(masks.values()))
    rle = mask.rle(info.width, info.height)
    flat = np.zeros(info.width * info.height, dtype=bool)
    position, value = 0, False
    for run in rle["counts"]:
        flat[position:position + run] = value
        position += run
        value = not value
    assert np.array_equal(flat.reshape((info.width, info.height)).T, mask.full(info.width, info.height))


def _cli():
    spec = importlib.util.spec_from_file_location("dxf_census", ROOT / "tools" / "dxf_census.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_prints_table_and_writes_json(fixture, tmp_path, capsys):
    path, _, _ = fixture
    out = tmp_path / "census" / "out.json"
    assert _cli().main([str(path.parent), "--json", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "DASHED" in printed and "line+text" in printed and "150 NTS" not in printed
    written = json.loads(out.read_text())
    assert written["file_count"] == 1 and written["entities"] == 19


def test_cli_refuses_json_in_tracked_paths(fixture):
    path, _, _ = fixture
    target = ROOT / "tests" / "never-written.json"
    with pytest.raises(SystemExit) as stop:
        _cli().main([str(path), "--json", str(target)])
    assert stop.value.code != 0
    assert not target.exists()


def test_cli_allows_json_under_data(fixture):
    cli = _cli()
    assert cli.allowed_output(ROOT / "data" / "census" / "x.json")
    assert not cli.allowed_output(ROOT / "app" / "x.json")
    assert cli.allowed_output(Path("/somewhere/else/x.json"))
