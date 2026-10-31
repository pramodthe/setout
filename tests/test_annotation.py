"""Annotation tools on fixture DXFs drawn with ezdxf inside the tests: no network, no real drawings."""
import csv
import json
import re
import threading
from http.client import HTTPConnection
from pathlib import Path

import ezdxf
import numpy as np
import pytest
from ezdxf import units
from ezdxf.math import Vec2
from ezdxf.render.mleader import ConnectionSide
from PIL import Image

from annotation import cli, contact, details, drawing, export, measure, names, pipeline, planar, propagate, regions, report, review, sheets, upload
from app import dxf_ingest, ontology, synthetic

ROOT = Path(__file__).resolve().parents[1]
TEXT = 5.0  # the text height of every fixture: near 1.0, far 5.0, eps 0.005


# ---------------------------------------------------------------- fixture drawing

def new_doc():
    doc = ezdxf.new("R2018", setup=True)
    doc.units = units.MM
    return doc


def note(space, text, at):
    return space.add_mtext(text, dxfattribs={"insert": at, "char_height": TEXT})


def box_points(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def loop(space, x0, y0, x1, y1, **attribs):
    return space.add_lwpolyline(box_points(x0, y0, x1, y1), close=True, dxfattribs=attribs)


def lines(space, points, close=True, **attribs):
    """A shape as plain LINEs, as the owner's sheets draw components."""
    ends = list(points) + ([points[0]] if close else [])
    return [space.add_line(a, b, dxfattribs=attribs) for a, b in zip(ends, ends[1:])]


def hatch(space, points, pattern="ANSI31", scale=1.0, angle=0.0):
    entity = space.add_hatch(color=7)
    if pattern != "SOLID":
        entity.set_pattern_fill(pattern, scale=scale, angle=angle)
    entity.paths.add_polyline_path(points, is_closed=True)
    return entity


def callout(space, text, tip, at=None):
    """A real LEADER and its text."""
    at = at or (tip[0] + 300, tip[1] + 40)
    label = note(space, text, at)
    space.add_leader([tip, at], dxfattribs={"annotation_handle": label.dxf.handle})
    return label


def line_callout(space, text, tip, at=None):
    """A plain line and a text beside its end."""
    at = at or (tip[0] + 300, tip[1] + 40)
    space.add_line(tip, at)
    return note(space, text, (at[0] + 2, at[1]))


def analyse(doc):
    return pipeline.analyse(doc)


def live(analysis):
    return [r for r in analysis.result.regions if not r.dropped]


def at(analysis, x, y):
    """The kept region holding a point."""
    found = [r for r in live(analysis) if planar.points_in_rings(r.rings, [(x, y)])[0]]
    assert len(found) == 1, (x, y, [r.kind for r in found])
    return found[0]


def proposal(analysis, x, y):
    return analysis.proposals[at(analysis, x, y).id]


def two_layers(space):
    """A slab with a screed on it, as closed outlines, and text so tolerances are known."""
    loop(space, 0, 0, 200, 100)
    loop(space, 0, 100, 200, 130)
    note(space, "NOTE", (0, -40))


def area(region):
    return abs(planar.rings_area(region.rings))


# ---------------------------------------------------------------- paper space

def layout_doc():
    """A slab in modelspace at 1:2 through a viewport, with annotation on the layout."""
    doc = new_doc()
    msp = doc.modelspace()
    loop(msp, 0, 0, 200, 100)
    loop(msp, 0, 100, 200, 130)
    layout = doc.paperspace()
    layout.add_viewport(center=(150, 100), size=(240, 180), view_center_point=(100, 65), view_height=360)
    return doc, layout


def test_viewport_maps_paper_to_model():
    doc, _ = layout_doc()
    [view] = dxf_ingest.viewports(doc)
    assert view.scale == pytest.approx(0.5)
    assert view.to_model(150, 100) == pytest.approx((100, 65))
    assert view.to_model(170, 110) == pytest.approx((140, 85))
    assert view.contains(150, 100) and not view.contains(10, 10)


def test_viewport_map_honours_view_twist():
    doc, layout = layout_doc()
    entity = next(e for e in layout if e.dxftype() == "VIEWPORT")
    entity.dxf.view_twist_angle = 90.0
    [view] = dxf_ingest.viewports(doc)
    model = entity.get_transformation_matrix()
    assert model.transform(ezdxf.math.Vec3(*view.to_model(150, 120), 0)).isclose((150, 120, 0))


def test_switched_off_overall_and_tilted_viewports_are_left_out():
    doc, layout = layout_doc()
    off = layout.add_viewport(center=(50, 50), size=(10, 10), view_center_point=(0, 0), view_height=10)
    off.dxf.status = 0
    tilted = layout.add_viewport(center=(60, 60), size=(10, 10), view_center_point=(0, 0), view_height=10)
    tilted.dxf.view_direction_vector = (1, 1, 1)
    overall = layout.add_viewport(center=(70, 70), size=(300, 200), view_center_point=(0, 0), view_height=200)
    overall.dxf.status = 1
    assert len(dxf_ingest.viewports(doc)) == 1


def test_entities_read_a_layout_and_modelspace_stays_the_default():
    doc, layout = layout_doc()
    label = note(layout, "PAPER NOTE", (20, 20))
    model = dxf_ingest.entities(doc)
    assert {r.space for r in model} == {"model"} and len(model) == 2
    paper = dxf_ingest.entities(doc, "Layout1")
    row = next(r for r in paper if r.handle == label.dxf.handle)
    assert row.space == "Layout1" and row.text == "PAPER NOTE"


def test_pseudo_leaders_read_a_layout():
    doc, layout = layout_doc()
    label = note(layout, "PAPER NOTE", (20, 20))
    line = layout.add_line((18, 20), (100, 100))
    assert dxf_ingest.pseudo_leaders(doc) == []
    assert dxf_ingest.pseudo_leaders(doc, layout="Layout1") == [(line.dxf.handle, label.dxf.handle)]


def test_census_counts_layouts_apart_from_modelspace():
    doc, layout = layout_doc()
    label = note(layout, "PAPER NOTE", (20, 20))
    layout.add_leader([(100, 100), (40, 30)], dxfattribs={"annotation_handle": label.dxf.handle})
    census = dxf_ingest.census_doc(doc)
    assert census["entities"] == 2 and census["text"]["entities"] == 0
    paper = census["layouts"]
    assert paper["viewports"] == 1 and paper["text"] == 1 and paper["leaders"]["LEADER"] == 1


def test_census_table_prints_paper_space(tmp_path, capsys):
    doc, layout = layout_doc()
    note(layout, "PAPER NOTE", (20, 20))
    doc.saveas(tmp_path / "sheet.dxf")
    from tools import dxf_census
    dxf_census.main([str(tmp_path / "sheet.dxf"), "--json", str(tmp_path / "census.json")])
    assert "Paper space (1 of 1 layouts in use" in capsys.readouterr().out


def test_callouts_on_a_layout_land_on_modelspace_regions():
    doc, layout = layout_doc()
    # paper (110, 80) is model (20, 25), inside the slab; paper (130, 120) is model (60, 105), in the screed
    label = note(layout, "CONCRETE SLAB", (20, 150))
    layout.add_leader([(110, 80), (22, 150)], dxfattribs={"annotation_handle": label.dxf.handle})
    second = note(layout, "SCREED", (20, 170))
    layout.add_line((130, 120), (18, 170))
    leader = layout.add_multileader_mtext()
    leader.set_content("CONCRETE SLAB")
    leader.add_leader_line(ConnectionSide.left, [Vec2(200, 80)])  # model (200, 25) is on the slab's edge
    leader.build(insert=Vec2(290, 60))
    analysis = analyse(doc)
    assert second.dxf.handle in {c.note for c in analysis.drawing.callouts}
    slab, screed = at(analysis, 20, 25), at(analysis, 60, 105)
    assert analysis.proposals[slab.id]["class_id"] == "slab"
    assert analysis.proposals[screed.id]["class_id"] == "screed"
    assert analysis.proposals[slab.id]["verbatim"].count("CONCRETE SLAB") >= 1
    assert analysis.drawing.units_per_paper_mm == pytest.approx(2.0)
    assert "viewport" in analysis.drawing.scale_source


def test_text_outside_every_viewport_stays_out_of_a_sheet():
    doc, layout = layout_doc()
    note(layout, "TITLE BLOCK", (360, 10))
    loop(layout, 340, 0, 420, 40)
    d = drawing.read(doc)
    assert "TITLE BLOCK" not in [t.text for t in d.texts]
    assert d.skipped["layout Layout1: not a callout on the drawing"] == 2
    assert len(d.curves) == 2


def test_a_layout_is_a_cover_sheet_unless_a_callout_lands_on_the_drawing():
    """Linework and notes on a layout stay out, even inside a viewport. Only a leader that
    reaches the drawing comes across."""
    doc, layout = layout_doc()
    loop(layout, 120, 80, 130, 90)
    note(layout, "A NOTE OVER THE DRAWING", (120, 100))
    d = drawing.read(doc)
    assert len(d.curves) == 2 and d.texts == [] and d.callouts == []


def test_a_viewport_on_an_empty_part_of_modelspace_says_nothing_about_scale():
    """Regression: a cover sheet's viewports looked at empty modelspace at 1:50, and that set
    the scale of details drawn elsewhere at 1:4."""
    doc = new_doc()
    msp = doc.modelspace()
    loop(msp, 0, 0, 200, 100)
    note(msp, "NOTE", (0, -40))
    layout = doc.paperspace()
    layout.add_viewport(center=(100, 100), size=(100, 100), view_center_point=(90000, 90000), view_height=5000)
    label = note(layout, "CONCRETE SLAB", (20, 20))
    layout.add_leader([(100, 100), (22, 20)], dxfattribs={"annotation_handle": label.dxf.handle})
    d = drawing.read(doc)
    assert d.units_per_paper_mm == pytest.approx(TEXT / drawing.NOMINAL_TEXT_MM)
    assert d.callouts == []


def test_layout_annotation_is_drawn_on_the_sheet(tmp_path):
    doc, layout = layout_doc()
    plain, _ = pipeline.render_sheet(doc, drawing.read(doc))
    label = note(layout, "CONCRETE SLAB", (20, 150))
    layout.add_leader([(110, 80), (22, 150)], dxfattribs={"annotation_handle": label.dxf.handle})
    d = drawing.read(doc)
    assert d.extra
    info, image = pipeline.render_sheet(doc, d, tmp_path / "sheet.png")
    x, y = info.to_pixel(20, 25)
    assert np.any(np.asarray(image)[int(y) - 2:int(y) + 3, int(x) - 2:int(x) + 3] < 128)  # the leader tip is inked
    assert info.width > plain.width  # the page grew to hold the text


def test_a_drawing_wholly_on_a_layout_is_read_there():
    doc = new_doc()
    layout = doc.paperspace()
    loop(layout, 0, 0, 200, 100)
    label = note(layout, "CONCRETE SLAB", (300, 50))
    layout.add_leader([(50, 50), (298, 50)], dxfattribs={"annotation_handle": label.dxf.handle})
    analysis = analyse(doc)
    assert analysis.drawing.frame == "Layout1"
    assert proposal(analysis, 50, 50)["class_id"] == "slab"
    info, image = pipeline.render_sheet(doc, analysis.drawing)
    assert np.asarray(image).min() == 0


# ---------------------------------------------------------------- the arrangement

def rings_of(*shapes, eps=1e-3):
    a = np.concatenate([np.array(s, float) for s in shapes])
    b = np.concatenate([np.roll(np.array(s, float), -1, axis=0) for s in shapes])
    return planar.Arrangement(a, b, eps)


def test_faces_share_edges_and_t_junctions():
    arrangement = rings_of(box_points(0, 0, 10, 10), box_points(10, 2, 20, 8))
    assert sorted(round(f.area, 6) for f in arrangement.faces) == [60.0, 100.0]


def test_a_shape_inside_another_is_a_hole():
    arrangement = rings_of(box_points(0, 0, 30, 30), box_points(10, 10, 20, 20))
    big = max(arrangement.faces, key=lambda f: f.area)
    assert big.area == pytest.approx(800) and len(big.cycles) == 2
    rings = arrangement.face_rings(big)
    assert sum(planar.ring_area(r) for r in rings) == pytest.approx(800)
    assert sorted(np.sign(planar.ring_area(r)) for r in rings) == [-1.0, 1.0]


def test_union_of_faces_drops_the_edge_between_them():
    arrangement = rings_of(box_points(0, 0, 10, 10), box_points(10, 0, 20, 10))
    rings = arrangement.union_rings([0, 1])
    assert len(rings) == 1 and planar.ring_area(rings[0]) == pytest.approx(200)


def test_a_bridge_between_two_loops_bounds_nothing():
    a = [(30, 15)]
    b = [(40, 15)]
    outer = np.array(box_points(0, 0, 30, 30), float)
    other = np.array(box_points(40, 10, 50, 20), float)
    starts = np.concatenate([outer, other, a])
    ends = np.concatenate([np.roll(outer, -1, axis=0), np.roll(other, -1, axis=0), b])
    arrangement = planar.Arrangement(starts, ends, 1e-3)
    assert sorted(round(f.area) for f in arrangement.faces) == [100, 900]
    assert int((~arrangement.alive).sum()) == 1


def test_a_chord_splits_a_face_and_its_overhangs_are_peeled():
    outer = np.array(box_points(0, 0, 30, 30), float)
    starts = np.concatenate([outer, [(-5, 15)]])
    ends = np.concatenate([np.roll(outer, -1, axis=0), [(35, 15)]])
    arrangement = planar.Arrangement(starts, ends, 1e-3)
    assert [round(f.area) for f in arrangement.faces] == [450, 450]
    assert arrangement.drawing_box() == pytest.approx((0, 0, 30, 30), abs=1e-2)


def test_coincident_outlines_and_float_noise_collapse():
    arrangement = rings_of(box_points(0, 0, 10, 10), [(10 + 1e-9, -1e-9), (20, 0), (20, 10), (10, 10 + 1e-9)])
    assert sorted(round(f.area, 3) for f in arrangement.faces) == [100.0, 100.0]


def sloppy_grid(rng, cols, rows, size, eps, slide):
    """Each cell draws its own four edges, so shared edges come twice; ends run past their
    corners by up to `slide` and sit up to eps/4 off the line. Rotated, so nothing is exact."""
    a, b = [], []
    angle = rng.uniform(0, np.pi)
    turn = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    for i in range(cols):
        for j in range(rows):
            x0, y0, x1, y1 = i * size, j * size, (i + 1) * size, (j + 1) * size
            for p, q in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
                p, q = np.array(p, float), np.array(q, float)
                unit = (q - p) / size
                normal = np.array([-unit[1], unit[0]])
                a.append(turn @ (p - unit * rng.uniform(0, slide) + normal * rng.uniform(-eps / 4, eps / 4)))
                b.append(turn @ (q + unit * rng.uniform(0, slide) + normal * rng.uniform(-eps / 4, eps / 4)))
    return np.array(a), np.array(b)


@pytest.mark.parametrize("seed", [59, 106, 112, 143, 153, 206, 815, 842, 1250, *range(0, 400, 7)])
def test_every_cell_keeps_its_own_face_however_sloppy_the_lines(seed):
    """Regression. Tolerance-based cutting lost a whole ring when a point landed level with
    a segment's end, and repeated cutting without dedupe ran out of memory."""
    rng = np.random.default_rng(seed)
    cols, rows = int(rng.integers(1, 6)), int(rng.integers(1, 6))
    size, eps = 100.0, 0.01
    a, b = sloppy_grid(rng, cols, rows, size, eps, rng.choice([0.0, eps / 2, eps, eps * 2, 1.0]))
    arrangement = planar.Arrangement(a, b, eps)
    assert len(arrangement.edges) < 40 * len(a)  # the tangle stays bounded
    whole = [f for f in arrangement.faces if f.area > 0.5 * size * size]
    assert len(whole) == cols * rows
    assert all(abs(f.area - size * size) < size * 8 for f in whole)


def test_many_line_ends_in_one_place_do_not_blow_up_the_search():
    """Regression: dash ends bunched at a corner, all within one margin of each other, made
    the quadtree visit 4^14 cells and a sheet hang for good."""
    points = np.random.default_rng(1).uniform(0, 10, (300, 2))
    i, j = planar._close_pairs(points, points, 31.0)
    assert len(i) == 300 * 299 // 2


def closed_after(segments, near=1.0, far=5.0, eps=0.005):
    a = np.array([s[0] for s in segments], float)
    b = np.array([s[1] for s in segments], float)
    arrangement = planar.Arrangement(a, b, eps)
    for _ in range(5):
        extra = planar.gap_bridges(arrangement, near, far, min_stub=near)
        if not len(extra):
            break
        a, b = np.concatenate([a, extra[:, 0]]), np.concatenate([b, extra[:, 1]])
        arrangement = planar.Arrangement(a, b, eps)
    return arrangement


def test_gap_closing_seals_a_lost_corner():
    arrangement = closed_after([((1, 0), (99, 0)), ((100, 1), (100, 99)), ((99, 100), (1, 100)), ((0, 99), (0, 1))])
    assert [round(f.area, -1) for f in arrangement.faces] == [10000]


def test_gap_closing_runs_across_a_dash_gap_along_the_line():
    arrangement = closed_after([((0, 0), (46, 0)), ((50, 0), (100, 0)), ((100, 0), (100, 100)),
                                ((100, 100), (0, 100)), ((0, 100), (0, 0))])
    assert len(arrangement.faces) == 1


def test_gap_closing_reaches_an_edge_straight_ahead():
    arrangement = closed_after([((0, 0), (100, 0)), ((100, 0), (100, 100)), ((100, 100), (0, 100)), ((0, 100), (0, 0)),
                                ((50, 4), (50, 100))])
    assert len(arrangement.faces) == 2


def test_gap_closing_never_reaches_back_over_its_own_line():
    """Regression: a free end bridged to its own line one vertex behind, every round, and
    never reached the line just ahead of it."""
    segments = [((0, 0), (100, 0)), ((100, 0), (100, 100)), ((100, 100), (0, 100)), ((0, 100), (0, 0)),
                ((50, 0.4), (50, 60)), ((50, 0.45), (50, 100))]
    arrangement = closed_after(segments)
    assert len(arrangement.faces) == 2


def test_gap_closing_caps_the_open_end_of_a_thin_layer():
    arrangement = closed_after([((0, 0), (100, 0)), ((0, 3), (100, 3))])
    assert len(arrangement.faces) == 1 and arrangement.faces[0].area == pytest.approx(300, abs=1)


def test_gap_closing_leaves_a_real_opening_open():
    arrangement = closed_after([((0, 0), (100, 0)), ((100, 0), (100, 100)), ((100, 100), (0, 100)),
                                ((0, 100), (0, 40)), ((0, 20), (0, 0))])
    assert len(arrangement.faces) == 0


def test_nothing_moves_two_lines_closer_than_the_gap_tolerance_together():
    """Snapping ends across a gap would fold a thin layer flat; bridges only add lines."""
    arrangement = closed_after([*zip(box_points(0, 0, 100, 2), box_points(0, 0, 100, 2)[1:] + [(0, 0)])])
    assert [round(f.area) for f in arrangement.faces] == [200]


# ---------------------------------------------------------------- regions

def test_hatch_and_its_outline_are_one_region_with_the_hatch_name():
    doc = new_doc()
    msp = doc.modelspace()
    loop(msp, 0, 0, 200, 100)
    hatch(msp, box_points(0, 0, 200, 100), "AR-CONC", scale=0.5, angle=30)
    note(msp, "NOTE", (0, -40))
    [region] = analyse(doc).result.regions
    assert region.kind == "hatch" and region.hatch == {"pattern": "AR-CONC", "scale": 0.5, "angle": 30.0}


def test_closed_polyline_and_area_enclosed_by_lines_are_regions():
    doc = new_doc()
    msp = doc.modelspace()
    loop(msp, 0, 0, 200, 100)
    lines(msp, box_points(0, 100, 200, 130))
    note(msp, "NOTE", (0, -40))
    analysis = analyse(doc)
    assert at(analysis, 50, 50).kind == "loop" and at(analysis, 50, 115).kind == "lines"
    assert area(at(analysis, 50, 115)) == pytest.approx(6000, rel=1e-3)


def test_annotation_does_not_chop_regions_up():
    """A dimension, a leader and text lie right across the slab."""
    doc = new_doc()
    msp = doc.modelspace()
    loop(msp, 0, 0, 200, 100)
    label = note(msp, "CONCRETE SLAB", (50, 50))
    msp.add_leader([(-30, 50), (250, 50)], dxfattribs={"annotation_handle": label.dxf.handle})
    msp.add_linear_dim(base=(0, 50), p1=(0, 0), p2=(200, 0), override={"dimtxt": 2.5}).render()
    [region] = analyse(doc).result.regions
    assert area(region) == pytest.approx(20000)


def test_hatch_pattern_lines_are_not_linework():
    doc = new_doc()
    msp = doc.modelspace()
    hatch(msp, box_points(0, 0, 200, 100), "ANSI31", scale=0.2)
    note(msp, "NOTE", (0, -40))
    assert len(analyse(doc).drawing.curves) == 0 and len(analyse(doc).result.regions) == 1


def test_a_stray_line_across_a_hatch_splits_nothing():
    doc = new_doc()
    msp = doc.modelspace()
    hatch(msp, box_points(0, 0, 200, 100))
    msp.add_line((50, 0), (50, 100))
    note(msp, "NOTE", (0, -40))
    analysis = analyse(doc)
    assert area(at(analysis, 20, 50)) == pytest.approx(20000, rel=1e-3)


def test_a_closed_part_inside_another_leaves_a_hole():
    doc = new_doc()
    msp = doc.modelspace()
    loop(msp, 0, 0, 200, 100)
    loop(msp, 80, 40, 120, 60)
    note(msp, "NOTE", (0, -40))
    analysis = analyse(doc)
    assert area(at(analysis, 20, 50)) == pytest.approx(20000 - 800, rel=1e-3)
    assert area(at(analysis, 100, 50)) == pytest.approx(800, rel=1e-3)


def test_a_gap_between_closed_parts_is_set_aside_and_can_come_back():
    doc = new_doc()
    msp = doc.modelspace()
    for x0, x1 in ((0, 90), (110, 200)):
        loop(msp, x0, 20, x1, 80)
    loop(msp, 0, 0, 200, 20)
    loop(msp, 0, 80, 200, 100)
    note(msp, "NOTE", (0, -40))
    analysis = analyse(doc)
    assert len(live(analysis)) == 4
    [gap] = [r for r in analysis.result.regions if r.dropped]
    assert "gap between" in gap.dropped


def test_a_thin_layer_drawn_as_one_line_is_a_region_from_its_stroke():
    doc = new_doc()
    msp = doc.modelspace()
    two_layers(msp)
    msp.add_lwpolyline([(0, 150), (100, 150), (100, 170)], dxfattribs={"lineweight": 70})
    analysis = analyse(doc)
    region = at(analysis, 50, 150)
    assert region.kind == "stroke" and region.lineweight == pytest.approx(0.7)
    assert region.width == pytest.approx(0.7 * analysis.drawing.units_per_paper_mm)
    assert area(region) == pytest.approx(120 * region.width, rel=0.02)


def test_a_line_drawn_in_pieces_is_one_stroke():
    doc = new_doc()
    msp = doc.modelspace()
    two_layers(msp)
    lines(msp, [(0, 150), (60, 150), (120, 150), (120, 180)], close=False, lineweight=50)
    strokes = [r for r in live(analyse(doc)) if r.kind == "stroke"]
    assert len(strokes) == 1


def test_a_polyline_with_a_drawn_width_uses_that_width():
    doc = new_doc()
    msp = doc.modelspace()
    two_layers(msp)
    msp.add_lwpolyline([(0, 150), (100, 150)], dxfattribs={"const_width": 3.0})
    assert at(analyse(doc), 50, 150).width == pytest.approx(3.0)


def test_a_short_tick_is_no_layer():
    doc = new_doc()
    msp = doc.modelspace()
    two_layers(msp)
    msp.add_line((50, 150), (55, 150))
    assert all(r.kind != "stroke" for r in analyse(doc).result.regions)


def test_a_line_drawn_across_a_narrow_gap_is_its_stroke_not_two_slivers():
    """Regression: two closed parts 4 apart with one heavy line between them. Faces either
    side are half the stroke: the part is the line."""
    doc = new_doc()
    msp = doc.modelspace()
    loop(msp, 0, 0, 200, 100)
    loop(msp, 0, 104, 200, 130)
    msp.add_line((0, 100), (0, 104))
    msp.add_line((200, 100), (200, 104))
    msp.add_line((0, 102), (200, 102), dxfattribs={"lineweight": 200})  # 2 mm at 2 units per mm: as wide as the gap
    note(msp, "NOTE", (0, -40))
    analysis = analyse(doc)
    region = at(analysis, 100, 102)
    assert region.kind == "stroke"
    assert len([r for r in live(analysis) if r.kind == "lines"]) == 0
    assert sum("ink" in (r.dropped or "") for r in analysis.result.regions) == 2


def test_a_thin_part_beside_a_heavy_neighbour_stays():
    """Regression: the rule for the case above dropped real thin parts, because a heavy
    line beside them was wider than they are."""
    doc = new_doc()
    msp = doc.modelspace()
    lines(msp, box_points(0, 0, 200, 100), lineweight=100)
    lines(msp, box_points(0, 100, 200, 103), lineweight=13)
    lines(msp, box_points(0, 103, 200, 130), lineweight=100)
    note(msp, "NOTE", (0, -40))
    assert area(at(analyse(doc), 100, 101.5)) == pytest.approx(600, rel=0.02)


def level_line_doc():
    doc = new_doc()
    msp = doc.modelspace()
    lines(msp, box_points(0, 0, 200, 100))
    lines(msp, box_points(0, 100, 100, 130))
    lines(msp, box_points(100, 100, 200, 130))
    note(msp, "NOTE", (0, -40))
    return doc, msp


def test_a_level_line_through_the_parts_does_not_split_them():
    doc, msp = level_line_doc()
    level = msp.add_line((-60, 115), (260, 115))
    analysis = analyse(doc)
    assert any(level.dxf.handle in v for v in analysis.result.excluded.values())
    assert area(at(analysis, 50, 110)) == pytest.approx(3000, rel=0.01)


def test_a_level_line_along_edges_is_not_a_layer():
    """Regression: a line lying along part edges crosses nothing, so only running past the
    drawing at both ends gives it away."""
    doc, msp = level_line_doc()
    level = msp.add_line((-60, 130), (260, 130), dxfattribs={"linetype": "CENTER"})
    analysis = analyse(doc)
    assert any(level.dxf.handle in v for v in analysis.result.excluded.values())
    assert all(r.kind != "stroke" for r in analysis.result.regions)


def test_a_marker_on_the_end_of_a_level_line_does_not_stretch_the_drawing():
    """Regression: the triangle at a level line's end is a closed face, and the line was
    judged against a box stretched out to it."""
    doc, msp = level_line_doc()
    level = msp.add_line((-60, 130), (260, 130))
    msp.add_lwpolyline([(260, 130), (254, 142), (266, 142)], close=True)
    analysis = analyse(doc)
    assert any(level.dxf.handle in v for v in analysis.result.excluded.values())
    assert all(r.kind != "stroke" for r in analysis.result.regions)
    assert any("drawing symbol" in (r.dropped or "") for r in analysis.result.regions)


def test_neighbours_overshooting_an_edge_do_not_make_it_a_through_line():
    """Regression: an edge with two neighbours poking through by a drafting miss was taken
    for a level line, removed, and the slab under it leaked."""
    doc = new_doc()
    msp = doc.modelspace()
    lines(msp, box_points(0, 0, 200, 100))
    for x in (40, 160):
        msp.add_line((x, 99.5), (x, 130))
    msp.add_line((0, 130), (200, 130))
    msp.add_line((0, 100), (0, 130))
    msp.add_line((200, 100), (200, 130))
    note(msp, "NOTE", (0, -40))
    analysis = analyse(doc)
    assert not analysis.result.excluded
    assert area(at(analysis, 100, 50)) == pytest.approx(20000, rel=0.01)


def test_a_break_symbol_is_not_linework():
    doc = new_doc()
    msp = doc.modelspace()
    two_layers(msp)
    zigzag = msp.add_lwpolyline([(0, -5), (0, 55), (-8, 62), (8, 72), (0, 80), (0, 135)])
    analysis = analyse(doc)
    assert analysis.result.excluded["break symbol"] == [zigzag.dxf.handle]
    assert all(r.kind != "stroke" for r in analysis.result.regions)


def test_a_flashing_with_a_step_is_not_a_break_symbol():
    assert not regions._is_break_symbol(np.array([(0, 0), (50, 0), (50, 10), (100, 10), (150, 10)], float))
    assert not regions._is_break_symbol(np.array([(0, 0), (50, 0), (50, 10), (70, 10), (70, 0), (150, 0)], float))


def test_a_sheet_border_and_a_tag_are_set_aside():
    doc = new_doc()
    msp = doc.modelspace()
    for k in range(4):
        loop(msp, k * 60, 0, k * 60 + 50, 50)
    loop(msp, -40, -40, 300, 200)
    msp.add_circle((280, 180), 6)
    note(msp, "NOTE", (0, -30))
    analysis = analyse(doc)
    assert len(live(analysis)) == 4
    reasons = " ".join(r.dropped or "" for r in analysis.result.regions)
    assert "frame" in reasons and "drawing symbol" in reasons


def tip_doc(end):
    doc = new_doc()
    msp = doc.modelspace()
    loop(msp, 0, 0, 200, 100)
    line_callout(msp, "CONCRETE SLAB", (50, 50), (250, 150))
    end(msp)
    return doc


@pytest.mark.parametrize("kind", ["dot", "tick"])
def test_a_leader_end_marker_is_not_a_part(kind):
    def end(msp):
        if kind == "dot":
            ring = [(50 + 1.5 * np.cos(a), 50 + 1.5 * np.sin(a)) for a in np.linspace(0, 2 * np.pi, 12, endpoint=False)]
            hatch(msp, ring, "SOLID")
        else:
            msp.add_line((47, 47), (53, 53))
    analysis = analyse(tip_doc(end))
    assert len(analysis.result.regions) == 1 and analysis.result.excluded["leader end marker"]


def test_a_small_filled_part_under_a_tip_stays():
    def end(msp):
        hatch(msp, box_points(45, 47, 55, 53), "SOLID")
    assert len(live(analyse(tip_doc(end)))) == 2


def test_blocks_are_expanded_and_arrays_repeated():
    doc = new_doc()
    block = doc.blocks.new("PLATE")
    block.add_lwpolyline(box_points(0, 0, 40, 40), close=True)
    msp = doc.modelspace()
    msp.add_blockref("PLATE", (0, 0))
    msp.add_blockref("PLATE", (100, 0), dxfattribs={"xscale": 2, "yscale": 2})
    grid = msp.add_blockref("PLATE", (0, 200))
    grid.dxf.column_count, grid.dxf.column_spacing = 3, 60
    note(msp, "NOTE", (0, -40))
    analysis = analyse(doc)
    assert area(at(analysis, 120, 40)) == pytest.approx(6400)
    assert len(live(analysis)) == 5


def test_hidden_layers_are_not_on_the_sheet():
    doc = new_doc()
    doc.layers.add("OFF").off()
    doc.layers.add("COLD").freeze()
    doc.layers.add("NOPLOT", plot=False)
    msp = doc.modelspace()
    loop(msp, 0, 0, 200, 100)
    for layer in ("OFF", "COLD", "NOPLOT"):
        msp.add_line((100, 0), (100, 100), dxfattribs={"layer": layer})
    note(msp, "NOTE", (0, -40))
    assert len(drawing.read(doc).curves) == 1


def test_a_mirrored_polyline_lands_where_it_is_drawn():
    doc = new_doc()
    msp = doc.modelspace()
    msp.add_lwpolyline(box_points(0, 0, 200, 100), close=True, dxfattribs={"extrusion": (0, 0, -1)})
    note(msp, "NOTE", (0, -40))
    assert at(analyse(doc), -50, 50).kind == "loop"


def test_arcs_and_circles_flatten_to_their_true_area():
    doc = new_doc()
    msp = doc.modelspace()
    msp.add_circle((100, 100), 50)
    note(msp, "NOTE", (0, -40))
    assert area(at(analyse(doc), 100, 100)) == pytest.approx(np.pi * 2500, rel=2e-3)


def test_layer_names_change_nothing():
    def sheet(a, b):
        doc = new_doc()
        doc.layers.add(a)
        doc.layers.add(b)
        msp = doc.modelspace()
        loop(msp, 0, 0, 200, 100, layer=a)
        lines(msp, box_points(0, 100, 200, 130), layer=b)
        note(msp, "NOTE", (0, -40))
        analysis = analyse(doc)
        return [(r.kind, round(r.area, 3), analysis.proposals[r.id]["class_id"]) for r in analysis.result.regions]
    assert sheet("CONCRETE SLAB", "SCREED") == sheet("L1", "L2")


def test_no_text_on_the_sheet_still_gives_regions():
    doc = new_doc()
    loop(doc.modelspace(), 0, 0, 200, 100)
    assert len(analyse(doc).result.regions) == 1


def test_an_empty_sheet_gives_nothing():
    analysis = analyse(new_doc())
    assert analysis.result.regions == [] and analysis.callouts == []


# ---------------------------------------------------------------- names

@pytest.fixture(scope="module")
def index():
    return names.AliasIndex.of(ontology.load())


@pytest.mark.parametrize("text, class_id", [
    ("CONCRETE SLAB", "slab"), ("Concrete slab", "slab"), ("SUSPENDED SLAB", "slab"), ("SUB-SILL", "sub_sill"),
    ("sub sill", "sub_sill"), ("SILL", "door_sill"), ("SILL FLASHING", "sub_sill_flashing"), ("PACKERS", "packer"),
    ("BACKING ROD", "backing_rod"), ("ROD", "rod"), ("THREADED ROD", "fixing"), ("WATERPROOF\nMEMBRANE", "membrane"),
    ("2 MM SHEET MEMBRANE", "membrane"), ("SCREED TO FALL", "screed"), ("MEMBRANE TURNED UP", "membrane"),
    ("FLASHING", "general_flashing"), ("WEEPHOLES", "weep_hole"), ("LIFT OVERRUN", "lift_overrun"),
    ("NEW CONCRETE\nTO FALLS", "new_concrete_to_falls"), ("HOBS", "hob"), ("TERMINATION BAR", "termination_bar"),
])
def test_text_maps_to_a_class_through_ontology_aliases(text, class_id, index):
    assert names.match_text(text, index).class_id == class_id


def test_a_longer_alias_beats_one_inside_it(index):
    assert names.match_text("COVER ANGLE", index).alias == "cover angle"


def test_the_subject_of_a_callout_comes_before_its_preposition(index):
    assert names.match_text("MEMBRANE OVER ANGLE", index).class_id == "membrane"


@pytest.mark.parametrize("text", ["STONE FLOOR", "WATERPROOFING", "SEE DETAIL 5", "", None, "   ", "123"])
def test_no_match_stays_unknown(text, index):
    assert names.match_text(text, index).class_id == names.UNKNOWN


@pytest.mark.parametrize("text, candidates", [
    ("TIMBER/ BRICK WALL", ["brick_wall"]), ("TIMBER OR BRICK WALL", ["brick_wall"]), ("TIMBER & BRICK WALL", ["brick_wall"]),
    ("TIMBER AND BRICK WALL", ["brick_wall"]), ("OLD HOBS/PIERS", ["hob"]), ("PIERS / HOBS", ["hob"]),
    ("HOB/PLINTH", ["hob", "plinth"]),
])
def test_two_things_joined_in_a_label_name_neither(text, candidates, index):
    """A label naming two things says what a region might be: it is no name for it, even
    when only one of the two is a class."""
    match = names.match_text(text, index)
    assert match.class_id == names.UNKNOWN and match.candidates == candidates


@pytest.mark.parametrize("text, class_id", [
    ("W/P MEMBRANE", "membrane"), ("M12/150 BOLT", "fixing"), ("BACKING ROD AND SEALANT", "backing_rod"),
    ("OLD MEMBRANE ON SCREED/SAND", "membrane"), ("MEMBRANE OR MEMBRANE", "membrane"),
])
def test_a_slash_or_and_that_joins_nothing_still_names(text, class_id, index):
    assert names.match_text(text, index).class_id == class_id


def test_two_classes_in_a_callout_stay_unknown_and_list_both(index):
    match = names.match_text("SEALANT AND MEMBRANE", index)
    assert match.class_id == names.UNKNOWN and match.candidates == ["membrane", "sealant"]


def test_real_leader_gives_verbatim_text_source_reason_and_state():
    doc = new_doc()
    msp = doc.modelspace()
    two_layers(msp)
    callout(msp, "CONCRETE SLAB", (50, 50))
    p = proposal(analyse(doc), 50, 50)
    assert p["class_id"] == "slab" and p["source"] == "callout" and p["state"] == "proposed"
    assert p["verbatim"] == ["CONCRETE SLAB"] and "alias 'concrete slab'" in p["reason"] and "inside" in p["reason"]


def test_mleader_and_line_plus_text_give_callouts():
    doc = new_doc()
    msp = doc.modelspace()
    two_layers(msp)
    leader = msp.add_multileader_mtext()
    leader.set_content("CONCRETE SLAB")
    leader.add_leader_line(ConnectionSide.left, [Vec2(50, 50)])
    leader.build(insert=Vec2(400, 100))
    line_callout(msp, "SCREED", (60, 115), (300, 220))
    analysis = analyse(doc)
    assert {c.kind for c in analysis.drawing.callouts} == {"MULTILEADER", "line+text"}
    assert proposal(analysis, 50, 50)["class_id"] == "slab" and proposal(analysis, 60, 115)["class_id"] == "screed"


def test_a_leader_line_is_never_linework():
    doc = new_doc()
    msp = doc.modelspace()
    loop(msp, 0, 0, 200, 100)
    line_callout(msp, "CONCRETE SLAB", (50, 50), (250, 150))
    assert len(drawing.read(doc).curves) == 1


def test_a_leader_with_no_link_takes_the_text_at_its_far_end():
    doc = new_doc()
    msp = doc.modelspace()
    two_layers(msp)
    note(msp, "CONCRETE SLAB", (302, 150))
    msp.add_leader([(50, 50), (300, 150)])
    assert proposal(analyse(doc), 50, 50)["class_id"] == "slab"


def test_stacked_callouts_each_keep_their_own_text():
    """Regression: dxf_ingest.pseudo_leaders takes the nearest text centre, and in a stacked
    column that is often the neighbour's: 24% of leaders were paired wrongly."""
    doc = new_doc()
    msp = doc.modelspace()
    two_layers(msp)
    words = ("SCREED", "A VERY LONG CALLOUT ABOUT THE SLAB BELOW IT", "PACKER")
    for k, text in enumerate(words):
        y = 150 - 9 * k
        msp.add_lwpolyline([(30 + 40 * k, 115 if k == 0 else 50), (300, y), (320, y)])
        label = note(msp, text, (322, y))
        label.set_location((322, y), attachment_point=ezdxf.enums.MTextEntityAlignment.MIDDLE_LEFT)
    found = {c.tip: c.text for c in drawing.read(doc).callouts}
    assert found == {(30.0, 115.0): "SCREED", (70.0, 50.0): words[1], (110.0, 50.0): "PACKER"}


def test_a_note_block_over_a_callout_column_does_not_steal_its_leaders():
    """Regression: a leader's end inside a big note's box matched the note."""
    doc = new_doc()
    msp = doc.modelspace()
    two_layers(msp)
    label = note(msp, "PACKER", (322, 100))
    label.set_location((322, 100), attachment_point=ezdxf.enums.MTextEntityAlignment.MIDDLE_LEFT)
    msp.add_lwpolyline([(50, 50), (320, 100)])
    block = msp.add_mtext("NOTES:\n1. ONE.\n2. TWO.\n3. THREE.\n4. FOUR.",
                          dxfattribs={"insert": (300, 140), "char_height": TEXT, "width": 200})
    block.set_location((300, 140), attachment_point=ezdxf.enums.MTextEntityAlignment.TOP_LEFT)
    assert [c.text for c in drawing.read(doc).callouts] == ["PACKER"]


def thin_doc():
    doc = new_doc()
    msp = doc.modelspace()
    loop(msp, 0, 0, 200, 100)
    loop(msp, 0, 100, 200, 103)
    loop(msp, 0, 103, 200, 130)
    return doc, msp


def test_a_tip_inside_a_thin_part_lands_on_the_thin_part():
    """Regression: every point in a thin part is near an edge, so it was read as a tip on
    an outline and pushed through into the neighbour."""
    doc, msp = thin_doc()
    callout(msp, "MEMBRANE", (50, 101.5))
    assert proposal(analyse(doc), 50, 101.5)["class_id"] == "membrane"


def test_a_tip_on_an_outline_lands_on_what_it_points_at():
    doc, msp = thin_doc()
    callout(msp, "MEMBRANE", (50, 103), at=(50, 300))  # coming down, stopping at the top of the membrane
    p = proposal(analyse(doc), 50, 101.5)
    assert p["class_id"] == "membrane" and "read just ahead" in p["reason"]


def test_a_tip_just_short_of_a_part_lands_on_it():
    doc = new_doc()
    msp = doc.modelspace()
    loop(msp, 0, 0, 200, 100)
    callout(msp, "CONCRETE SLAB", (200.5, 50))
    assert proposal(analyse(doc), 50, 50)["class_id"] == "slab"


def test_a_tip_on_a_single_line_names_the_line():
    doc = new_doc()
    msp = doc.modelspace()
    two_layers(msp)
    msp.add_line((0, 150), (150, 150), dxfattribs={"lineweight": 50})
    callout(msp, "MEMBRANE", (60, 150))
    assert proposal(analyse(doc), 60, 150)["class_id"] == "membrane"


def test_a_tip_landing_nowhere_is_kept_and_reported():
    doc = new_doc()
    msp = doc.modelspace()
    two_layers(msp)
    callout(msp, "CONCRETE SLAB", (500, 500))
    analysis = analyse(doc)
    [record] = analysis.callouts
    assert record["region"] is None and record["text"] == "CONCRETE SLAB" and "no region" in record["landing"]
    assert all(p["class_id"] == names.UNKNOWN for p in analysis.proposals.values())


def test_two_callouts_disagreeing_on_a_region_stay_unknown():
    doc = new_doc()
    msp = doc.modelspace()
    two_layers(msp)
    callout(msp, "CONCRETE SLAB", (50, 50))
    callout(msp, "SCREED", (80, 50), at=(380, 150))
    p = proposal(analyse(doc), 50, 50)
    assert p["class_id"] == names.UNKNOWN and p["candidates"] == ["screed", "slab"] and len(p["verbatim"]) == 2


def test_a_callout_that_matches_nothing_keeps_its_text_and_stays_unknown():
    doc = new_doc()
    msp = doc.modelspace()
    two_layers(msp)
    callout(msp, "STONE FLOOR", (50, 50))
    p = proposal(analyse(doc), 50, 50)
    assert p["class_id"] == names.UNKNOWN and p["verbatim"] == ["STONE FLOOR"] and p["source"] is None


def hatched_doc(second="ANSI31", second_scale=1.0, pattern="ANSI31"):
    doc = new_doc()
    msp = doc.modelspace()
    hatch(msp, box_points(0, 0, 90, 100), pattern)
    hatch(msp, box_points(110, 0, 200, 100), second, scale=second_scale)
    note(msp, "NOTE", (0, -40))
    return doc, msp


def test_the_same_hatch_on_a_sheet_proposes_the_same_class():
    doc, msp = hatched_doc()
    callout(msp, "CONCRETE SLAB", (50, 50))
    p = proposal(analyse(doc), 150, 50)
    assert p["class_id"] == "slab" and p["source"] == "hatch" and "ANSI31" in p["reason"] and "CONCRETE SLAB" in p["reason"]
    assert p["state"] == "proposed" and p["verbatim"] == []


@pytest.mark.parametrize("second, scale", [("ANSI37", 1.0), ("ANSI31", 2.0)])
def test_a_different_pattern_or_scale_is_a_different_hatch(second, scale):
    doc, msp = hatched_doc(second, scale)
    callout(msp, "CONCRETE SLAB", (50, 50))
    assert proposal(analyse(doc), 150, 50)["class_id"] == names.UNKNOWN


def test_a_solid_fill_says_nothing_about_what_it_fills():
    doc, msp = hatched_doc("SOLID", pattern="SOLID")
    callout(msp, "SEALANT", (50, 50))
    assert proposal(analyse(doc), 150, 50)["class_id"] == names.UNKNOWN


def test_callouts_disagreeing_on_a_hatch_propose_nothing_from_it():
    doc, msp = hatched_doc()
    hatch(msp, box_points(220, 0, 300, 100))
    callout(msp, "CONCRETE SLAB", (50, 50))
    callout(msp, "SCREED", (150, 50), at=(450, 150))
    assert proposal(analyse(doc), 260, 50)["class_id"] == names.UNKNOWN


def test_a_hatch_does_not_override_a_callout_on_the_same_region():
    doc, msp = hatched_doc()
    callout(msp, "CONCRETE SLAB", (50, 50))
    callout(msp, "SCREED", (150, 50), at=(450, 150))
    assert proposal(analyse(doc), 150, 50)["source"] == "callout"


def source_of_the_tools():
    return {p: p.read_text() for p in (ROOT / "annotation").glob("*") if p.suffix in (".py", ".js", ".html")}


def test_nothing_here_calls_a_model_or_the_network():
    for path, text in source_of_the_tools().items():
        for word in ("meta_client", "model_gateway", "app.sam", "muse", "urllib.request", "import requests", "httpx", "https://", "http://cdn"):
            assert word not in text, (path.name, word)


def test_no_class_or_alias_is_named_in_code():
    onto = ontology.load()
    words = set(onto.component_ids())  # one-word aliases such as "anchor" are also plain English
    words |= {a.lower() for c in onto.component_ids() for a in onto.component(c).aliases if " " in a}
    for path, text in source_of_the_tools().items():
        if path.suffix != ".py":
            continue
        quoted = {q.lower() for q in re.findall(r"""["']([a-zA-Z_ -]{3,})["']""", text)}
        assert not quoted & words, (path.name, sorted(quoted & words))


# ---------------------------------------------------------------- one detail for the app

def two_details(tmp_path, thin=2.0):
    """A wide detail with a thin layer on it, and a tall one far from it."""
    doc = new_doc()
    msp = doc.modelspace()
    hatch(msp, box_points(0, 0, 800, 100), "AR-CONC", scale=10)
    loop(msp, 0, 0, 800, 100)
    hatch(msp, box_points(0, 100, 800, 100 + thin), "ANSI31", scale=1)
    loop(msp, 0, 100, 800, 100 + thin)
    lines(msp, box_points(100, 100 + thin, 300, 200))
    msp.add_line((400, 105), (700, 105))  # one line, bounding nothing
    note(msp, "NOTE", (0, -30))
    section(msp, 5000, 0, width=100, height=900)
    path = tmp_path / "sheet.dxf"
    doc.saveas(path)
    return path


def test_a_detail_fills_the_model_budget_and_no_more(tmp_path):
    path = two_details(tmp_path)
    doc = dxf_ingest.load(path)
    render = upload.render_detail(doc, pipeline.analyse_sheet(doc), 1)
    width, height = render.image.size
    assert 0.98 * upload.MAX_PIXELS <= width * height <= 1.005 * upload.MAX_PIXELS
    assert render.px_per_unit > pipeline.render_scale(pipeline.analyse_sheet(doc).drawing)  # enlarged


def test_a_render_holds_its_own_detail_only(tmp_path):
    path = two_details(tmp_path)
    doc = dxf_ingest.load(path)
    whole = pipeline.analyse_sheet(doc)
    wide = upload.render_detail(doc, whole, next(a.detail.index for a in whole.analyses if a.detail.box[0] < 1000))
    tall = upload.render_detail(doc, whole, next(a.detail.index for a in whole.analyses if a.detail.box[0] > 1000))
    assert wide.image.width > wide.image.height and tall.image.height > tall.image.width


def test_a_frame_costs_no_pixels(tmp_path):
    doc, _ = sheet_of((0, 0), (900, 0), framed=True, gap_note=False)
    whole = pipeline.analyse_sheet(doc)
    render = upload.render_detail(doc, whole, 2)
    assert render.px_per_unit == pytest.approx(upload.budget_scale(200, 180, pipeline.MARGIN_PX))


def test_a_frame_inside_a_block_keeps_the_block_in_the_render():
    """Every piece of a block has the block's handle: dropping the frame's would drop it all."""
    doc = new_doc()
    block = doc.blocks.new("DETAIL")
    section(block, 0, 0, framed=True)
    doc.modelspace().add_blockref("DETAIL", (0, 0))
    section(doc.modelspace(), 2000, 0, framed=True)
    note(doc.modelspace(), "NOTE", (0, -60))
    whole = pipeline.analyse_sheet(doc)
    index = next(a.detail.index for a in whole.analyses if a.detail.box[0] < 1000)
    image = upload.render_detail(doc, whole, index).image
    assert 0.8 < image.width / image.height < 1.3  # the block, not the note left beside it


def test_the_thinnest_part_is_counted_in_pixels_and_lines_are_not_parts(tmp_path):
    path = two_details(tmp_path, thin=2.0)
    doc = dxf_ingest.load(path)
    whole = pipeline.analyse_sheet(doc)
    analysis = next(a for a in whole.analyses if a.detail.box[0] < 1000)
    render = upload.render_detail(doc, whole, analysis.detail.index)
    assert any(r.kind == "stroke" and r.width < 1.0 for r in analysis.result.regions)
    assert render.thinnest == pytest.approx(2.0, abs=0.2)
    assert render.thinnest_px == pytest.approx(render.thinnest * render.px_per_unit)


@pytest.mark.parametrize("px, verdict", [(9.0, "good"), (8.0, "good"), (5.0, "enough"), (4.0, "enough"), (3.9, "too coarse")])
def test_a_render_says_when_it_is_too_coarse(px, verdict):
    assert upload.Render(None, 1.0, px, 1.0).verdict == verdict


def test_the_budget_counts_the_margins():
    scale = upload.budget_scale(1000, 10, 24)
    assert (1000 * scale + 48) * (10 * scale + 48) == pytest.approx(upload.MAX_PIXELS)


def test_the_upload_reaches_the_store_as_the_same_pixels(tmp_path):
    from app import store  # tests/conftest.py points it at a temp dir

    path = two_details(tmp_path)
    result = upload.write(path, 1, tmp_path / "out")
    data = Path(result["pdf"]).read_bytes()
    doc = store.store_pdf("detail.pdf", data)
    assert len(doc["figures"]) == 1 and doc["warnings"] == []
    figure = doc["figures"][0]
    assert figure["kind"] == "embedded_image" and (figure["width"], figure["height"]) == (result["width"], result["height"])
    stored = store.doc_dir(doc["id"]) / f"{figure['crop_id']}.png"
    with Image.open(stored) as got, Image.open(result["png"]) as wanted:
        assert got.mode == wanted.mode and got.tobytes() == wanted.tobytes()


def test_the_upload_pdf_says_nothing_about_size_or_source(tmp_path):
    path = two_details(tmp_path)
    data = Path(upload.write(path, 1, tmp_path / "out")["pdf"]).read_bytes()
    assert b"sheet" not in data and b"/UserUnit" not in data and b"/Metadata" not in data


def test_an_upload_will_not_land_in_a_tracked_folder(tmp_path):
    path = two_details(tmp_path)
    with pytest.raises(sheets.Refused):
        upload.write(path, 1, Path(__file__).resolve().parents[1] / "annotation" / "out")


def test_a_detail_the_file_does_not_have_is_refused(tmp_path):
    path = two_details(tmp_path)
    with pytest.raises(SystemExit):
        cli.main(["upload", str(path), "--detail", "99", "--out", str(tmp_path / "out")])


# ---------------------------------------------------------------- the store and its edits

def reviewable(tmp_path, name="sheet.dxf", folder=None):
    doc = new_doc()
    msp = doc.modelspace()
    two_layers(msp)
    callout(msp, "CONCRETE SLAB", (50, 50))
    home = tmp_path / (folder or "drawings")
    home.mkdir(parents=True, exist_ok=True)
    path = home / name
    doc.saveas(path)
    store = tmp_path / "store"
    [record] = pipeline.propose_file(path, store, root=tmp_path / "drawings")
    return store, record


def by_class(record, class_id):
    return next(r for r in record["regions"] if r["proposal"]["class_id"] == class_id)


def test_propose_writes_a_record_and_a_render(tmp_path):
    store, record = reviewable(tmp_path)
    folder = store / record["sheet_id"]
    assert json.loads((folder / "sheet.json").read_text())["schema"] == sheets.SCHEMA
    render = record["render"]
    with Image.open(folder / "sheet.png") as image:
        assert image.size == (render["width"], render["height"])
    assert record["source"]["relative_path"] == "sheet.dxf" and len(record["source"]["sha256"]) == 64
    assert record["ontology_version"] == ontology.load().version


def test_render_scale_comes_from_text_size_not_the_page(tmp_path):
    _, record = reviewable(tmp_path)
    assert record["units_per_paper_mm"] == pytest.approx(TEXT / drawing.NOMINAL_TEXT_MM)
    assert record["render"]["px_per_unit"] == pytest.approx(pipeline.DPI / 25.4 / 2.0)
    assert "text height" in record["scale_source"]


def test_regions_line_up_with_the_render(tmp_path):
    store, record = reviewable(tmp_path)
    image = np.asarray(Image.open(store / record["sheet_id"] / "sheet.png"))
    slab = by_class(record, "slab")
    [ring] = export.to_pixels(slab["rings"], record["render"])
    for x, y in ring:
        window = image[int(y) - 2:int(y) + 3, int(x) - 2:int(x) + 3]
        assert window.size and window.min() < 128


def test_accept_needs_a_class_and_confirms(tmp_path):
    _, record = reviewable(tmp_path)
    slab = by_class(record, "slab")
    sheets.accept(record, slab["id"])
    assert slab["proposal"]["state"] == names.CONFIRMED
    with pytest.raises(sheets.Refused):
        sheets.accept(record, by_class(record, names.UNKNOWN)["id"])


def test_correct_takes_only_ontology_classes_and_says_so(tmp_path):
    _, record = reviewable(tmp_path)
    known = set(ontology.load().component_ids())
    slab = by_class(record, "slab")
    with pytest.raises(sheets.Refused):
        sheets.correct(record, slab["id"], "something invented", known)
    sheets.correct(record, slab["id"], "screed", known)
    assert slab["proposal"]["state"] == names.CORRECTED and slab["proposal"]["corrected_from"] == "slab"
    assert slab["proposal"]["verbatim"] == ["CONCRETE SLAB"]  # what the sheet says never changes


def test_choosing_the_proposed_class_is_accepting_it(tmp_path):
    _, record = reviewable(tmp_path)
    slab = by_class(record, "slab")
    sheets.correct(record, slab["id"], "slab", {"slab"})
    assert slab["proposal"]["state"] == names.CONFIRMED


def test_reject_and_restore(tmp_path):
    _, record = reviewable(tmp_path)
    slab = by_class(record, "slab")
    sheets.reject(record, slab["id"])
    assert slab["rejected"] and sheets.is_decided(slab)
    sheets.restore(record, slab["id"])
    assert not slab["rejected"] and not sheets.is_decided(slab)


def test_merge_gives_one_outline_and_keeps_the_name(tmp_path):
    _, record = reviewable(tmp_path)
    first, second = by_class(record, "slab"), by_class(record, names.UNKNOWN)
    total = first["area"] + second["area"]
    new_id = sheets.merge(record, [second["id"], first["id"]])
    merged = sheets.region_of(record, new_id)
    assert len(record["regions"]) == 1 and len(merged["rings"]) == 1
    assert merged["area"] == pytest.approx(total, rel=1e-3)
    assert merged["proposal"]["class_id"] == "slab" and "merged from" in merged["proposal"]["reason"]
    assert merged["proposal"]["state"] == names.PROPOSED


def test_merge_of_regions_apart_keeps_both_pieces(tmp_path):
    doc = new_doc()
    msp = doc.modelspace()
    loop(msp, 0, 0, 90, 100)
    loop(msp, 93, 0, 200, 100)
    note(msp, "NOTE", (0, -40))
    path = tmp_path / "sheet.dxf"
    doc.saveas(path)
    [record] = pipeline.propose_file(path, tmp_path / "store")
    merged = sheets.region_of(record, sheets.merge(record, [r["id"] for r in record["regions"]]))
    assert len(merged["rings"]) == 2 and merged["area"] == pytest.approx(19700, rel=1e-3)


def test_merge_needs_two_regions(tmp_path):
    _, record = reviewable(tmp_path)
    with pytest.raises(sheets.Refused):
        sheets.merge(record, [record["regions"][0]["id"]])


def test_split_cuts_along_the_line_extended_across_the_region(tmp_path):
    _, record = reviewable(tmp_path)
    slab = by_class(record, "slab")
    made = sheets.split(record, slab["id"], [50, 40], [50, 60])
    pieces = [sheets.region_of(record, k) for k in made]
    assert sorted(round(p["area"]) for p in pieces) == [5000, 15000]
    assert all(p["proposal"]["class_id"] == "slab" and "split from" in p["proposal"]["reason"] for p in pieces)
    assert slab["id"] not in [r["id"] for r in record["regions"]]


def test_split_that_misses_is_refused_and_changes_nothing(tmp_path):
    _, record = reviewable(tmp_path)
    slab = by_class(record, "slab")
    before = json.dumps(record)
    with pytest.raises(sheets.Refused):
        sheets.split(record, slab["id"], [300, 300], [320, 300])
    with pytest.raises(sheets.Refused):
        sheets.split(record, slab["id"], [50, 50], [50, 50])
    assert json.dumps(record) == before


def test_split_of_a_region_with_a_hole_keeps_the_hole(tmp_path):
    doc = new_doc()
    msp = doc.modelspace()
    loop(msp, 0, 0, 200, 100)
    loop(msp, 150, 40, 170, 60)
    note(msp, "NOTE", (0, -40))
    path = tmp_path / "sheet.dxf"
    doc.saveas(path)
    [record] = pipeline.propose_file(path, tmp_path / "store")
    host = max(record["regions"], key=lambda r: r["area"])
    made = sheets.split(record, host["id"], [50, 0], [50, 100])
    assert sorted(round(sheets.region_of(record, k)["area"]) for k in made) == [5000, 14600]


def test_a_reviewed_sheet_is_never_redone_without_force(tmp_path):
    store, record = reviewable(tmp_path)
    sheets.accept(record, by_class(record, "slab")["id"])
    sheets.write_sheet(store, record)
    path = tmp_path / "drawings" / "sheet.dxf"
    with pytest.raises(pipeline.AlreadyReviewed):
        pipeline.propose_file(path, store)
    assert sheets.read_sheet(store, record["sheet_id"]) == record
    [redone] = pipeline.propose_file(path, store, force=True)
    assert all(r["proposal"]["state"] == names.PROPOSED for r in redone["regions"])


def test_an_untouched_sheet_is_simply_redone(tmp_path):
    store, record = reviewable(tmp_path)
    [again] = pipeline.propose_file(tmp_path / "drawings" / "sheet.dxf", store)
    assert again["sheet_id"] == record["sheet_id"]


def test_sheet_ids_are_safe_and_stable():
    assert pipeline.sheet_id_of("Some Sheet (rev 2).DXF", "abcdef0123", 7) == "some-sheet-rev-2-abcdef01-d07"
    assert sheets.SHEET_ID.match(pipeline.sheet_id_of("../../etc/passwd", "abcdef0123"))
    with pytest.raises(sheets.Refused):
        sheets.sheet_dir("/tmp/store", "../outside")


def test_outputs_are_refused_inside_the_repo_outside_data():
    assert sheets.allowed_output(ROOT / "data" / "annotation" / "x")
    assert not sheets.allowed_output(ROOT / "annotation" / "x")
    assert not sheets.allowed_output(ROOT / "tests")
    assert not sheets.allowed_output(ROOT)
    assert sheets.allowed_output("/tmp/not-in-a-repo/x")
    with pytest.raises(sheets.Refused):
        sheets.require_output(ROOT / "docs" / "x")


def test_a_store_in_the_repo_must_be_under_data_annotation():
    sheets.require_store(ROOT / "data" / "annotation" / "batch1")
    with pytest.raises(sheets.Refused):
        sheets.require_store(ROOT / "data" / "datasets" / "x")
    with pytest.raises(sheets.Refused):
        sheets.require_store(ROOT / "tests")


def test_everything_generated_is_gitignored():
    ignored = (ROOT / ".gitignore").read_text()
    assert "data/**" in ignored
    assert sheets.DEFAULT_STORE.is_relative_to(ROOT / "data")


# ---------------------------------------------------------------- the review server

@pytest.fixture
def server(tmp_path):
    store, record = reviewable(tmp_path)
    instance = review.make_server(store, port=0)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield instance, store, record
    instance.shutdown()
    instance.server_close()


def call(instance, method, path, body=None, headers=None, token=True):
    port = instance.server_address[1]
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    send = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    payload = None
    if body is not None:
        payload = json.dumps(body)
        send["Content-Type"] = "application/json"
    if token and method == "POST":
        send["X-Setout-Token"] = instance.token
    connection.request(method, path, payload, send)
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return response, data


def test_the_server_binds_to_loopback_only(server):
    instance, _, _ = server
    assert instance.server_address[0] == "127.0.0.1"


def test_the_page_loads_nothing_from_anywhere_else(server):
    instance, _, _ = server
    response, data = call(instance, "GET", "/")
    html = data.decode()
    assert response.status == 200 and "<script>" in html and "</style>" in html
    assert not re.search(r"""(src|href)\s*=\s*["']https?:""", html)
    assert "@import" not in html and "https://" not in html
    assert "default-src 'none'" in response.getheader("Content-Security-Policy")


def test_the_page_serves_the_sheet_the_image_and_the_classes(server):
    instance, _, record = server
    assert json.loads(call(instance, "GET", "/api/sheets")[1])[0]["sheet_id"] == record["sheet_id"]
    sheet = json.loads(call(instance, "GET", f"/api/sheets/{record['sheet_id']}")[1])
    assert sheet["summary"]["regions"] == 2 and sheet["regions"]
    response, data = call(instance, "GET", f"/api/sheets/{record['sheet_id']}/image")
    assert response.getheader("Content-Type") == "image/png" and data[:4] == b"\x89PNG"
    assert {c["id"] for c in json.loads(call(instance, "GET", "/api/classes")[1])} == set(ontology.load().component_ids())


def test_a_decision_saves_to_disk_with_its_seconds(server):
    instance, store, record = server
    slab = by_class(record, "slab")["id"]
    response, data = call(instance, "POST", f"/api/sheets/{record['sheet_id']}/accept", {"region": slab, "seconds": 2.5})
    assert response.status == 200 and json.loads(data)["summary"]["decided"] == 1
    saved = sheets.region_of(sheets.read_sheet(store, record["sheet_id"]), slab)
    assert saved["proposal"]["state"] == names.CONFIRMED and saved["seconds"] == 2.5
    event = json.loads((store / record["sheet_id"] / "events.jsonl").read_text().splitlines()[-1])
    assert event["action"] == "accept" and event["seconds"] == 2.5 and event["region"] == slab


def test_seconds_add_up_per_part_and_per_sheet(server):
    instance, store, record = server
    path = f"/api/sheets/{record['sheet_id']}"
    slab, other = by_class(record, "slab")["id"], by_class(record, names.UNKNOWN)["id"]
    call(instance, "POST", f"{path}/reject", {"region": slab, "seconds": 3.0})
    call(instance, "POST", f"{path}/restore", {"region": slab, "seconds": 1.0})
    call(instance, "POST", f"{path}/accept", {"region": slab, "seconds": 1.0})
    call(instance, "POST", f"{path}/correct", {"region": other, "class_id": "screed", "seconds": 5.0})
    saved = sheets.read_sheet(store, record["sheet_id"])
    assert sheets.region_of(saved, slab)["seconds"] == 5.0
    summary = sheets.summary(saved)
    assert summary["seconds"] == 10.0 and summary["seconds_per_part"] == 5.0 and summary["done"]


def test_seconds_stay_with_the_parts_through_merge_and_split(server):
    instance, store, record = server
    path = f"/api/sheets/{record['sheet_id']}"
    ids = [r["id"] for r in record["regions"]]
    call(instance, "POST", f"{path}/reject", {"region": ids[0], "seconds": 4.0})
    data = json.loads(call(instance, "POST", f"{path}/merge", {"regions": ids, "seconds": 2.0})[1])
    merged = data["result"]["created"][0]
    assert sheets.region_of(data, merged)["seconds"] == 6.0
    data = json.loads(call(instance, "POST", f"{path}/split", {"region": merged, "start": [50, 0], "end": [50, 10], "seconds": 2.0})[1])
    assert [sheets.region_of(data, k)["seconds"] for k in data["result"]["created"]] == [4.0, 4.0]


def test_silly_seconds_cannot_corrupt_the_record(server):
    instance, store, record = server
    slab = by_class(record, "slab")["id"]
    path = f"/api/sheets/{record['sheet_id']}/accept"
    call(instance, "POST", path, {"region": slab, "seconds": 1e12})
    assert sheets.region_of(sheets.read_sheet(store, record["sheet_id"]), slab)["seconds"] == review.MAX_SECONDS
    call(instance, "POST", path, {"region": slab, "seconds": "soon"})
    call(instance, "POST", path, {"region": slab, "seconds": -5})
    assert sheets.region_of(sheets.read_sheet(store, record["sheet_id"]), slab)["seconds"] == review.MAX_SECONDS


def test_undo_puts_the_record_back(server):
    instance, store, record = server
    path = f"/api/sheets/{record['sheet_id']}"
    ids = [r["id"] for r in record["regions"]]
    call(instance, "POST", f"{path}/merge", {"regions": ids})
    assert len(sheets.read_sheet(store, record["sheet_id"])["regions"]) == 1
    call(instance, "POST", f"{path}/undo", {})
    assert [r["id"] for r in sheets.read_sheet(store, record["sheet_id"])["regions"]] == ids
    assert call(instance, "POST", f"{path}/undo", {})[0].status == 409


def test_a_write_without_the_token_is_refused(server):
    instance, store, record = server
    slab = by_class(record, "slab")["id"]
    response, _ = call(instance, "POST", f"/api/sheets/{record['sheet_id']}/accept", {"region": slab}, token=False)
    assert response.status == 403
    assert by_class(sheets.read_sheet(store, record["sheet_id"]), "slab")["proposal"]["state"] == names.PROPOSED


def test_a_request_for_another_host_is_refused(server):
    instance, _, record = server
    assert call(instance, "GET", "/", headers={"Host": "evil.example"})[0].status == 403
    assert call(instance, "POST", f"/api/sheets/{record['sheet_id']}/accept", {}, headers={"Host": "evil.example"})[0].status == 403


def test_paths_cannot_leave_the_store(server):
    instance, _, _ = server
    assert call(instance, "GET", "/api/sheets/..%2F..%2Fetc")[0].status == 404
    assert call(instance, "GET", "/api/sheets/../../etc/passwd")[0].status == 404


def test_bad_requests_change_nothing(server):
    instance, store, record = server
    path = f"/api/sheets/{record['sheet_id']}"
    before = (store / record["sheet_id"] / "sheet.json").read_text()
    assert call(instance, "POST", f"{path}/accept", {"region": "r999"})[0].status == 409
    assert call(instance, "POST", f"{path}/correct", {"region": "r1", "class_id": "invented"})[0].status == 409
    assert call(instance, "POST", f"{path}/explode", {"region": "r1"})[0].status == 409
    assert call(instance, "POST", f"{path}/split", {"region": "r1"})[0].status == 400
    assert (store / record["sheet_id"] / "sheet.json").read_text() == before


def test_a_sheet_can_be_left_out_with_a_reason(server):
    instance, store, record = server
    path = f"/api/sheets/{record['sheet_id']}/flag"
    call(instance, "POST", path, {"exclude": True, "note": "a part has no region"})
    assert sheets.read_sheet(store, record["sheet_id"])["excluded_from_export"] == "a part has no region"
    call(instance, "POST", path, {"exclude": False})
    assert sheets.read_sheet(store, record["sheet_id"])["excluded_from_export"] is None


# ---------------------------------------------------------------- export

def reviewed_sheet(tmp_path, store, project, name, thin=False):
    """A sheet in a project folder, every region decided."""
    doc = new_doc()
    msp = doc.modelspace()
    width = 200 + len(name) * 37 % 400  # each drawing differs
    loop(msp, 0, 0, width, 100)
    loop(msp, 0, 100, width, 130 if not thin else 101)
    callout(msp, "CONCRETE SLAB", (50, 50))
    callout(msp, "MEMBRANE" if thin else "SCREED", (60, 100.5 if thin else 115), at=(360, 200))
    folder = tmp_path / "drawings" / project
    folder.mkdir(parents=True, exist_ok=True)
    doc.saveas(folder / name)
    [record] = pipeline.propose_file(folder / name, store, root=tmp_path / "drawings")
    for region in record["regions"]:
        if region["proposal"]["class_id"] != names.UNKNOWN:
            sheets.accept(record, region["id"])
    sheets.write_sheet(store, record)
    return record


def three_projects(tmp_path):
    store = tmp_path / "store"
    records = {}
    for project, count in (("alpha", 4), ("beta", 2), ("gamma", 2)):
        for k in range(count):
            records[f"{project}{k}"] = reviewed_sheet(tmp_path, store, project, f"{project}{k}.dxf", thin=k == 0)
    return store, records


def test_export_refuses_without_a_project_source(tmp_path):
    store, _ = reviewable(tmp_path)
    with pytest.raises(export.Refused, match="project"):
        export.export(store, tmp_path / "out")
    assert not (tmp_path / "out" / "manifest.json").exists()


def test_export_refuses_a_sheet_with_no_project(tmp_path):
    store, _ = three_projects(tmp_path)
    loose = tmp_path / "drawings"
    doc = new_doc()
    two_layers(doc.modelspace())
    doc.saveas(loose / "loose.dxf")
    [record] = pipeline.propose_file(loose / "loose.dxf", store, root=loose)
    for region in record["regions"]:
        sheets.correct(record, region["id"], "slab", {"slab"})
    sheets.write_sheet(store, record)
    with pytest.raises(export.Refused, match="no project"):
        export.export(store, tmp_path / "out", folders=True)
    assert not (tmp_path / "out" / "manifest.json").exists()


def test_export_refuses_too_few_projects(tmp_path):
    store = tmp_path / "store"
    reviewed_sheet(tmp_path, store, "alpha", "a.dxf")
    reviewed_sheet(tmp_path, store, "beta", "b.dxf")
    with pytest.raises(export.Refused, match="project"):
        export.export(store, tmp_path / "out", folders=True)


def test_export_refuses_one_drawing_in_two_projects(tmp_path):
    store, _ = three_projects(tmp_path)
    copy = tmp_path / "drawings" / "gamma" / "copy.dxf"
    copy.write_bytes((tmp_path / "drawings" / "alpha" / "alpha1.dxf").read_bytes())
    [record] = pipeline.propose_file(copy, store, root=tmp_path / "drawings")
    for region in record["regions"]:
        if region["proposal"]["class_id"] != names.UNKNOWN:
            sheets.accept(record, region["id"])
    sheets.write_sheet(store, record)
    with pytest.raises(export.Refused, match="more than one project"):
        export.export(store, tmp_path / "out", folders=True)


def test_export_writes_the_synthetic_dataset_layout(tmp_path):
    store, records = three_projects(tmp_path)
    out = tmp_path / "owner-v1"
    info = export.export(store, out, folders=True, log=lambda *_: None)
    for split in ("train", "valid", "test"):
        coco = json.loads((out / split / "_annotations.coco.json").read_text())
        assert set(coco) >= {"images", "annotations", "categories", "info"}
        for image in coco["images"]:
            assert image["width"] == image["height"] == 1008
            assert {"drawing_id", "tile_xy", "file_name", "id"} <= set(image)
            with Image.open(out / split / image["file_name"]) as tile:
                assert tile.size == (1008, 1008) and tile.mode == "L"
        for annotation in coco["annotations"]:
            assert {"thin", "min_width_px", "part_id", "truncated"} == set(annotation["attributes"])
            assert annotation["segmentation"] and annotation["iscrowd"] == 0
    for name in ("manifest.json", "drawings.jsonl", "run.json"):
        assert (out / name).is_file()
    assert info["source"] == "owner" and info["synthetic"] is False
    assert [c["name"] for c in info["categories"]] == sorted({"membrane", "screed", "slab"})
    assert info["review"]["parts"] > 0


def test_export_passes_the_gate_training_uses(tmp_path):
    from training import rfdetr_seg
    store, _ = three_projects(tmp_path)
    export.export(store, tmp_path / "owner-v1", folders=True, log=lambda *_: None)
    report = rfdetr_seg.check_dataset(tmp_path / "owner-v1")
    assert report["source"] == "owner" and report["classes"] == ["membrane", "screed", "slab"]


def test_roboflow_refuses_an_owner_dataset(tmp_path, monkeypatch):
    from training import roboflow_upload
    store, _ = three_projects(tmp_path)
    datasets = tmp_path / "datasets"
    export.export(store, datasets / "owner-v1", folders=True, log=lambda *_: None)
    monkeypatch.setattr(roboflow_upload, "DATASETS", datasets)
    with pytest.raises(roboflow_upload.Refused, match="synthetic"):
        roboflow_upload.synthetic_dataset("owner-v1")


def test_a_project_never_spans_two_splits(tmp_path):
    store, _ = three_projects(tmp_path)
    out = tmp_path / "out"
    info = export.export(store, out, folders=True, log=lambda *_: None)
    rows = [json.loads(line) for line in (out / "drawings.jsonl").read_text().splitlines()]
    assert len({r["project"] for r in rows}) == 3
    seen = {}
    for row in rows:
        seen.setdefault(row["project"], set()).add(row["split"])
    assert all(len(v) == 1 for v in seen.values())
    assert sorted(p for v in info["projects"].values() for p in v) == ["alpha", "beta", "gamma"]
    for split in ("train", "valid", "test"):
        coco = json.loads((out / split / "_annotations.coco.json").read_text())
        assert {i["template"] for i in coco["images"]} <= set(info["projects"][split])
        assert all(Path(i["file_name"]).stem.rsplit("_x", 1)[0] in {r["drawing_id"] for r in rows if r["split"] == split}
                   for i in coco["images"])


def test_projects_can_come_from_a_csv(tmp_path):
    store, records = three_projects(tmp_path)
    table = tmp_path / "projects.csv"
    with open(table, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sheet", "project"])
        for k, name in enumerate(records):
            writer.writerow([f"{name}.dxf", f"job{k % 3}"])
    info = export.export(store, tmp_path / "out", csv_path=table, log=lambda *_: None)
    assert sorted(p for v in info["projects"].values() for p in v) == ["job0", "job1", "job2"]


def test_a_csv_without_the_right_columns_is_refused(tmp_path):
    store, _ = three_projects(tmp_path)
    table = tmp_path / "projects.csv"
    table.write_text("file,job\na,b\n")
    with pytest.raises(export.Refused, match="columns"):
        export.export(store, tmp_path / "out", csv_path=table)


def test_the_test_split_stays_frozen_as_projects_arrive(tmp_path):
    store, _ = three_projects(tmp_path)
    export.export(store, tmp_path / "first", folders=True, log=lambda *_: None)
    frozen = json.loads((store / "splits.json").read_text())
    for name in ("delta", "epsilon", "zeta"):
        reviewed_sheet(tmp_path, store, name, f"{name}.dxf")
    export.export(store, tmp_path / "second", folders=True, log=lambda *_: None)
    later = json.loads((store / "splits.json").read_text())
    assert {k: later[k] for k in frozen} == frozen and len(later) == 6


def test_splits_follow_the_shares_as_projects_grow():
    split_of = export.assign_splits([f"p{k}" for k in range(50)], {}, 1)
    counts = sorted(list(split_of.values()).count(s) for s in ("train", "valid", "test"))
    assert counts == [5, 5, 40]


def test_only_confirmed_regions_are_exported(tmp_path):
    store, records = three_projects(tmp_path)
    record = records["alpha1"]
    region = by_class(record, "screed")
    sheets.reject(record, region["id"])
    sheets.write_sheet(store, record)
    out = tmp_path / "out"
    export.export(store, out, folders=True, log=lambda *_: None)
    rows = {json.loads(line)["drawing_id"]: json.loads(line) for line in (out / "drawings.jsonl").read_text().splitlines()}
    assert [p["class_id"] for p in rows[record["sheet_id"]]["parts"]] == ["slab"]


def test_a_sheet_not_fully_reviewed_is_left_out_whole(tmp_path):
    store, records = three_projects(tmp_path)
    record = records["alpha1"]
    region = by_class(record, "screed")
    sheets.restore(record, region["id"])
    sheets.write_sheet(store, record)
    info = export.export(store, tmp_path / "out", folders=True, log=lambda *_: None)
    assert info["skipped_sheets"] == {"not fully reviewed": [record["sheet_id"]]}
    assert record["sheet_id"] not in (tmp_path / "out" / "drawings.jsonl").read_text()


def test_a_sheet_a_reviewer_left_out_stays_out(tmp_path):
    store, records = three_projects(tmp_path)
    record = records["alpha1"]
    record["excluded_from_export"] = "a part has no region"
    sheets.write_sheet(store, record)
    info = export.export(store, tmp_path / "out", folders=True, log=lambda *_: None)
    assert info["skipped_sheets"] == {"left out by a reviewer": [record["sheet_id"]]}


def test_an_existing_dataset_is_kept_unless_overwrite(tmp_path):
    store, _ = three_projects(tmp_path)
    out = tmp_path / "out"
    export.export(store, out, folders=True, log=lambda *_: None)
    with pytest.raises(export.Refused, match="overwrite"):
        export.export(store, out, folders=True)
    export.export(store, out, folders=True, overwrite=True, log=lambda *_: None)


def test_export_refuses_a_tracked_path(tmp_path):
    store, _ = three_projects(tmp_path)
    with pytest.raises(sheets.Refused):
        export.export(store, ROOT / "tests" / "dataset", folders=True)


def export_coco(tmp_path):
    store, records = three_projects(tmp_path)
    out = tmp_path / "out"
    info = export.export(store, out, folders=True, log=lambda *_: None)
    return out, info, records


def test_masks_land_on_their_ink(tmp_path):
    out, info, _ = export_coco(tmp_path)
    checked = 0
    for split in ("train", "valid", "test"):
        coco = json.loads((out / split / "_annotations.coco.json").read_text())
        files = {i["id"]: i["file_name"] for i in coco["images"]}
        for annotation in coco["annotations"]:
            image = np.asarray(Image.open(out / split / files[annotation["image_id"]]))
            mask = synthetic.rasterize([np.array(s).reshape(-1, 2) for s in annotation["segmentation"]], (0, 0, 1008, 1008))
            edge = synthetic._boundary(mask)
            edge[:3], edge[-3:], edge[:, :3], edge[:, -3:] = False, False, False, False  # where a tile cuts it
            ink = synthetic._dilate(image < 128, 2)
            if np.count_nonzero(edge):
                assert np.count_nonzero(edge & ink) / np.count_nonzero(edge) > 0.9
                checked += 1
    assert checked > 5


def test_the_thin_flag_follows_the_rendered_width(tmp_path):
    out, info, _ = export_coco(tmp_path)
    rows = [json.loads(line) for line in (out / "drawings.jsonl").read_text().splitlines()]
    parts = [p for r in rows for p in r["parts"]]
    assert any(p["thin"] for p in parts) and any(not p["thin"] for p in parts)
    assert all(p["thin"] == (p["min_width_px"] < 6.0) for p in parts)


def test_a_sheet_smaller_than_a_tile_is_padded_with_paper(tmp_path):
    out, info, _ = export_coco(tmp_path)
    rows = [json.loads(line) for line in (out / "drawings.jsonl").read_text().splitlines()]
    small = next(r for r in rows if r["render"]["width"] < 1008 or r["render"]["height"] < 1008)
    tile = next((out / small["split"]).glob(f"{small['drawing_id']}_*.png"))
    assert np.asarray(Image.open(tile)).max() == 255
    assert (np.asarray(Image.open(tile)) == 0).mean() < 0.2  # paper, not black


def test_reexport_is_byte_identical(tmp_path):
    store, _ = three_projects(tmp_path)
    export.export(store, tmp_path / "a", folders=True, log=lambda *_: None)
    export.export(store, tmp_path / "b", folders=True, log=lambda *_: None)
    assert (tmp_path / "a" / "train" / "_annotations.coco.json").read_bytes().replace(b"\"a ", b"\"X ") == (
        tmp_path / "b" / "train" / "_annotations.coco.json").read_bytes().replace(b"\"b ", b"\"X ")
    assert sorted(p.name for p in (tmp_path / "a" / "train").iterdir()) == sorted(p.name for p in (tmp_path / "b" / "train").iterdir())


# ---------------------------------------------------------------- measurement

@pytest.fixture(scope="module")
def measured(tmp_path_factory):
    folder = tmp_path_factory.mktemp("measure")
    rows = measure.make_sheets(folder, 5, 3, log=lambda *_: None)
    return folder, rows


def test_sheets_cover_every_style(measured):
    folder, rows = measured
    styles = {json.loads(Path(r["truth"]).read_text())["style"] for r in rows}
    assert styles == {s.id for s in synthetic.load_styles()}


def test_truth_is_the_generators_own_parts(measured):
    _, rows = measured
    truth = json.loads(Path(rows[0]["truth"]).read_text())
    assert truth["parts"] and all(p["class_id"] and p["rings"] for p in truth["parts"])
    assert truth["px_per_unit"] > 0 and truth["thin_px"] == synthetic.Config().thin_px


def test_scrubbing_leaves_no_part_name_on_a_layer(measured):
    _, rows = measured
    doc = ezdxf.readfile(rows[0]["master"])
    assert any(layer.dxf.name.startswith(measure.PART_LAYER) for layer in doc.layers)
    measure.scrub_layers(doc)
    assert not any(layer.dxf.name.startswith(measure.PART_LAYER) for layer in doc.layers)
    assert not any(e.dxf.layer.startswith(measure.PART_LAYER) for e in doc.modelspace())


@pytest.mark.parametrize("variant", measure.VARIANTS)
def test_the_pipeline_runs_blind_on_every_variant(measured, variant):
    folder, rows = measured
    report = measure.run_variant(folder, rows, variant, 3, log=lambda *_: None)
    assert report["sheets"] == len(rows) and not report["failed"]
    assert report["recall"]["0.5"]["compact"] > 0.8


def test_clean_sheets_recover_every_part(measured):
    folder, rows = measured
    report = measure.run_variant(folder, rows, "clean", 3, log=lambda *_: None)
    assert report["recall"]["0.5"]["all"] == 1.0 and report["precision"]["0.5"]["all"] == 1.0


def test_lines_variant_leaves_no_closed_outlines(measured):
    folder, rows = measured
    truth = json.loads(Path(rows[0]["truth"]).read_text())
    doc = ezdxf.readfile(rows[0]["master"])
    measure.apply_variant(doc, truth, "lines", 3)
    parts = [e for e in doc.modelspace().query("LWPOLYLINE") if e.dxf.layer.startswith(measure.PART_LAYER)]
    assert not parts and len(doc.modelspace().query("LINE")) > 10


def test_scorer_counts_a_perfect_match_and_a_miss():
    ring = np.array(box_points(0, 0, 20, 10), float)
    other = np.array(box_points(40, 0, 60, 10), float)
    scored = measure.overlaps([[ring], [other]], [[ring]])
    assert scored.iou[0, 0] == pytest.approx(1.0) and scored.iou[1, 0] == 0
    assert measure.match(scored.iou, 0.5) == {0: 0}


def test_scorer_iou_of_half_overlap():
    a = np.array(box_points(0, 0, 20, 10), float)
    b = np.array(box_points(10, 0, 30, 10), float)
    assert measure.overlaps([[a]], [[b]]).iou[0, 0] == pytest.approx(1 / 3, abs=0.01)


def test_matching_is_one_to_one_best_first():
    iou = np.array([[0.9, 0.8], [0.85, 0.0]])
    assert measure.match(iou, 0.5) == {0: 0}
    assert measure.match(np.array([[0.9, 0.0], [0.8, 0.7]]), 0.5) == {0: 0, 1: 1}


def test_table_prints_every_variant(measured):
    folder, rows = measured
    report = {"clean": measure.run_variant(folder, rows, "clean", 3, log=lambda *_: None)}
    text = measure.table(report)
    assert "recall    IoU 0.5 thin" in text and "corrections per sheet" in text and "wrong-name rate" in text


# ---------------------------------------------------------------- the command line

def test_cli_propose_then_export(tmp_path, capsys):
    drawings = tmp_path / "drawings"
    store = tmp_path / "store"
    for project in ("alpha", "beta", "gamma"):
        folder = drawings / project
        folder.mkdir(parents=True)
        doc = new_doc()
        msp = doc.modelspace()
        two_layers(msp)
        callout(msp, "CONCRETE SLAB", (50, 50))
        callout(msp, f"SCREED {project}", (60, 115), at=(360, 200))
        doc.saveas(folder / "sheet.dxf")
    assert cli.main(["propose", str(drawings), "--store", str(store)]) == 0
    assert "3 file(s), 3 detail(s): 6 regions, 6 with a proposed name" in capsys.readouterr().out
    for sheet_id in sheets.sheet_ids(store):
        record = sheets.read_sheet(store, sheet_id)
        for region in record["regions"]:
            sheets.accept(record, region["id"])
        sheets.write_sheet(store, record)
    assert cli.main(["export", "--out", str(tmp_path / "out"), "--project-folders", "--store", str(store)]) == 0
    assert json.loads((tmp_path / "out" / "manifest.json").read_text())["source"] == "owner"


def test_cli_batch_survives_a_bad_file_and_keeps_reviewed_sheets(tmp_path, capsys):
    drawings = tmp_path / "drawings"
    drawings.mkdir()
    store, record = reviewable(tmp_path)
    (drawings / "broken.dxf").write_text("not a drawing")
    sheets.accept(record, by_class(record, "slab")["id"])
    sheets.write_sheet(store, record)
    assert cli.main(["batch", str(drawings), "--workers", "1", "--store", str(store)]) == 1
    out = capsys.readouterr().out
    assert "failed" in out and "kept" in out
    assert json.loads((store / "batch.json").read_text())
    assert by_class(sheets.read_sheet(store, record["sheet_id"]), "slab")["proposal"]["state"] == names.CONFIRMED


def test_cli_finds_no_files(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(SystemExit, match="No .dxf"):
        cli.main(["propose", str(tmp_path / "empty"), "--store", str(tmp_path / "store")])


def test_cli_turns_a_refusal_into_a_message(tmp_path):
    store, _ = reviewable(tmp_path)
    with pytest.raises(SystemExit, match="every sheet needs a project"):
        cli.main(["export", "--out", str(tmp_path / "out"), "--store", str(store)])


def test_cli_refuses_a_store_in_a_tracked_path(tmp_path):
    source = tmp_path / "a.dxf"
    new_doc().saveas(source)
    with pytest.raises(SystemExit, match="data/annotation"):
        cli.main(["propose", str(source), "--store", str(ROOT / "annotation" / "store")])


# ---------------------------------------------------------------- one file, many details

def section(space, x, y, width=200, height=100, framed=False):
    """One small detail: a block with a layer on it, hatched and outlined."""
    loop(space, x, y, x + width, y + height)
    hatch(space, box_points(x, y, x + width, y + height), "AR-CONC", scale=10)
    loop(space, x, y + height, x + width, y + height + 30)
    lines(space, box_points(x + 20, y + height + 30, x + 80, y + height + 80))
    lines(space, box_points(x + 90, y + height + 30, x + 150, y + height + 80))
    if framed:
        loop(space, x - 40, y - 40, x + width + 40, y + height + 120)


def sheet_of(*origins, framed=False, gap_note=True):
    doc = new_doc()
    msp = doc.modelspace()
    for x, y in origins:
        section(msp, x, y, framed=framed)
    if gap_note:
        note(msp, "NOTE", (origins[0][0], origins[0][1] - 30))
    return doc, msp


def split_of(doc):
    d = drawing.read(doc)
    return d, details.split(d)


def test_whitespace_splits_a_sheet_into_details():
    doc, _ = sheet_of((0, 0), (600, 0), (1200, 0), (0, 600), (600, 600))
    _, sheet = split_of(doc)
    assert len(sheet.details) == 5
    assert [d.index for d in sheet.details] == [1, 2, 3, 4, 5]
    assert sheet.details[0].box[1] > sheet.details[-1].box[1]  # top of the sheet first
    assert all(len(d.curves) == 10 and len(d.hatches) == 1 for d in sheet.details)


def test_one_detail_stays_one_detail():
    doc, _ = sheet_of((0, 0))
    assert len(split_of(doc)[1].details) == 1


def framed_pair(frame=None):
    """Two groups with a wide gap and one frame round both, and a detail apart from them."""
    doc = new_doc()
    msp = doc.modelspace()
    section(msp, 0, 0)
    section(msp, 280, 0)
    outline = msp.add_lwpolyline(frame or box_points(-40, -40, 520, 240), close=True)
    section(msp, 900, 0)
    note(msp, "NOTE", (0, -60))
    return doc, msp, outline


def test_a_frame_holding_several_groups_gives_one_detail_each_and_belongs_to_none():
    doc, _, outline = framed_pair()
    d, sheet = split_of(doc)
    assert len(sheet.details) == 3
    split = [k for k in sheet.details if k.framed]
    assert len(split) == 2 and all(k.groups_inside == 2 and k.frame == outline.dxf.handle for k in split)
    assert all(len(k.curves) == 10 and len(k.hatches) == 1 for k in split)
    handles = {d.curves[n].handle for k in sheet.details for n in k.curves}
    assert outline.dxf.handle not in handles
    assert split[0].box[2] < split[1].box[0]  # each box is its group, not the frame


def test_a_skewed_frame_is_still_a_frame():
    """Half the frames on a real sheet ran a fraction of a degree off square."""
    doc, _, outline = framed_pair([(-40, -40), (520, -35), (518, 240), (-42, 235)])
    d, sheet = split_of(doc)
    assert len(sheet.details) == 3 and sum(k.framed for k in sheet.details) == 2


def test_a_long_thin_slanted_part_is_no_rectangle():
    """Each side is judged on its own: a short side slanted hard must not pass because a long
    side makes it look small."""
    doc = new_doc()
    outline = doc.modelspace().add_lwpolyline([(0, 0), (1000, 0), (1006, 40), (6, 40)], close=True)
    d = drawing.read(doc)
    assert not details._is_rectangle(next(c for c in d.curves if c.handle == outline.dxf.handle))


def test_a_frame_holding_one_group_stays_one_detail():
    doc, _ = sheet_of((0, 0), (900, 0), framed=True)
    d, sheet = split_of(doc)
    assert len(sheet.details) == 2
    assert all(k.framed and k.groups_inside == 1 and k.frame for k in sheet.details)
    assert all(len(k.curves) == 11 for k in sheet.details)  # the frame stays with its one group


def test_a_scrap_in_a_split_frame_joins_its_group_or_is_counted():
    doc, msp, _ = framed_pair(box_points(-40, -40, 520, 640))
    tick = msp.add_line((212, 50), (219, 50))  # past the gap from the left group, within reach of it
    far = msp.add_line((0, 500), (5, 500))  # further from both groups than a scrap reaches
    d, sheet = split_of(doc)
    split = sorted((k for k in sheet.details if k.framed), key=lambda k: k.box[0])
    assert tick.dxf.handle in {d.curves[n].handle for n in split[0].curves}
    assert far.dxf.handle not in {d.curves[n].handle for k in sheet.details for n in k.curves}
    assert sheet.fragments == 1


def test_text_in_a_split_frame_goes_to_its_own_group():
    doc, msp, _ = framed_pair()
    note(msp, "RIGHT HAND", (300, 150))
    d, sheet = split_of(doc)
    right = max((k for k in sheet.details if k.framed), key=lambda k: k.box[0])
    assert "RIGHT HAND" in [d.texts[n].text for n in right.texts]


def test_a_split_frame_gives_a_record_per_group_and_the_report_counts_one_frame(tmp_path):
    doc, _, _ = framed_pair()
    path = tmp_path / "sheet.dxf"
    doc.saveas(path)
    store = tmp_path / "store"
    records = pipeline.propose_file(path, store)
    assert len(records) == 3
    split = [r for r in records if r["source"]["detail"]["groups_inside"] > 1]
    assert len(split) == 2 and all(r["source"]["detail"]["groups_inside"] == 2 for r in split)
    summary = report.build(store)
    assert summary["frames_holding_several"] == 1 and summary["details"] == 3
    assert not any((r["dropped"] or "").startswith("a frame") for record in split for r in record["regions"])


def test_two_frames_close_together_stay_two_details():
    """Regression: two stacked frames a few text heights apart merged into one."""
    doc, _ = sheet_of((0, 0), (0, 300), framed=True)  # the frames are 20 apart: four text heights
    _, sheet = split_of(doc)
    assert len(sheet.details) == 2 and all(d.framed for d in sheet.details)


def test_something_poking_through_a_frame_stays_with_it():
    doc, msp = sheet_of((0, 0), framed=True)
    pole = msp.add_lwpolyline([(100, 150), (100, 900)], dxfattribs={"lineweight": 50})
    section(msp, 1000, 0)
    d, sheet = split_of(doc)
    assert len(sheet.details) == 2
    framed = next(k for k in sheet.details if k.framed)
    assert pole.dxf.handle in {d.curves[k].handle for k in framed.curves}


def test_a_small_fragment_joins_the_nearest_detail():
    doc, msp = sheet_of((0, 0), (900, 0))
    tick = msp.add_line((230, 50), (240, 50))  # past the gap, within reach of the first detail
    d, sheet = split_of(doc)
    assert len(sheet.details) == 2 and sheet.fragments == 0
    assert tick.dxf.handle in {d.curves[k].handle for k in sheet.details[0].curves}


def test_a_stray_scrap_far_from_everything_is_counted_not_a_detail():
    doc, msp = sheet_of((0, 0), (900, 0))
    msp.add_line((5000, 5000), (5010, 5000))
    _, sheet = split_of(doc)
    assert len(sheet.details) == 2 and sheet.fragments == 1


def test_a_sheet_border_does_not_pull_everything_together():
    doc, msp = sheet_of((0, 0), (900, 0), (0, 700))
    border = loop(msp, -300, -300, 1500, 1200)
    _, sheet = split_of(doc)
    assert len(sheet.details) == 3 and sheet.border == [border.dxf.handle]


def test_text_and_callouts_go_to_their_detail():
    doc, msp = sheet_of((0, 0), (900, 0))
    label = callout(msp, "CONCRETE SLAB", (950, 50), at=(1000, 300))
    d, sheet = split_of(doc)
    second = sheet.details[1]
    assert [d.callouts[k].text for k in second.callouts] == ["CONCRETE SLAB"]
    assert label.dxf.handle in details.handles_of(d, second)


def test_each_detail_is_a_record_with_its_own_render(tmp_path):
    doc, msp = sheet_of((0, 0), (900, 0), (1800, 0))
    msp.add_linear_dim(base=(0, -20), p1=(0, 0), p2=(200, 0), override={"dimtxt": 2.5}).render()
    path = tmp_path / "many.dxf"
    doc.saveas(path)
    records = pipeline.propose_file(path, tmp_path / "store")
    assert [r["source"]["detail"]["index"] for r in records] == [1, 2, 3]
    assert len({r["sheet_id"] for r in records}) == 3 and {r["source"]["details_in_file"] for r in records} == {3}
    for record in records:
        render = record["render"]
        with Image.open(tmp_path / "store" / record["sheet_id"] / "sheet.png") as image:
            assert image.size == (render["width"], render["height"]) and render["width"] < 1200
        assert not record["regions"][0]["proposal"]["verbatim"]
    assert sheets.summary(records[1])["label"] == "many.dxf d02"


def test_a_detail_render_holds_its_own_ink_and_no_neighbours():
    doc, _ = sheet_of((0, 0), (900, 0))
    d, sheet = split_of(doc)
    only = details.handles_of(d, sheet.details[0])
    info, _ = pipeline.render_sheet(doc, d, only=only, extra=[])
    assert info.extents[2] < 300


def test_redoing_a_file_replaces_all_its_details(tmp_path):
    doc, _ = sheet_of((0, 0), (900, 0))
    path = tmp_path / "many.dxf"
    doc.saveas(path)
    store = tmp_path / "store"
    first = pipeline.propose_file(path, store)
    second = pipeline.propose_file(path, store)
    assert [r["sheet_id"] for r in first] == [r["sheet_id"] for r in second]
    assert len(sheets.sheet_ids(store)) == 2


def test_a_decision_on_one_detail_protects_the_whole_file(tmp_path):
    doc, _ = sheet_of((0, 0), (900, 0))
    path = tmp_path / "many.dxf"
    doc.saveas(path)
    store = tmp_path / "store"
    records = pipeline.propose_file(path, store)
    sheets.reject(records[1], records[1]["regions"][0]["id"])
    sheets.write_sheet(store, records[1])
    with pytest.raises(pipeline.AlreadyReviewed):
        pipeline.propose_file(path, store)


def test_details_of_one_file_share_a_project_in_the_export(tmp_path):
    store = tmp_path / "store"
    for project in ("alpha", "beta", "gamma"):
        doc, msp = sheet_of((0, 0), (900, 0))
        callout(msp, "CONCRETE SLAB", (50, 50), at=(100, 300))
        callout(msp, f"SCREED {project}", (60, 115), at=(100, 350))
        folder = tmp_path / "drawings" / project
        folder.mkdir(parents=True)
        doc.saveas(folder / "many.dxf")
        for record in pipeline.propose_file(folder / "many.dxf", store, root=tmp_path / "drawings"):
            for region in record["regions"]:
                if region["dropped"]:
                    continue
                sheets.correct(record, region["id"], "slab", {"slab"})
            sheets.write_sheet(store, record)
    info = export.export(store, tmp_path / "out", folders=True, log=lambda *_: None)
    rows = [json.loads(line) for line in (tmp_path / "out" / "drawings.jsonl").read_text().splitlines()]
    assert len(rows) == 6 and {r["detail"]["index"] for r in rows} == {1, 2}
    assert all(len({r["split"] for r in rows if r["project"] == p}) == 1 for p in ("alpha", "beta", "gamma"))
    assert info["splits"]["test"]["drawings"] == 2


def test_contact_sheet_and_report_cover_every_detail(tmp_path):
    doc, _ = sheet_of((0, 0), (900, 0), (1800, 0))
    path = tmp_path / "many.dxf"
    doc.saveas(path)
    store = tmp_path / "store"
    pipeline.propose_file(path, store)
    out = contact.build(store, store / "contact.png")
    with Image.open(out) as image:
        assert image.width >= 3 * contact.CELL[0]
    result = report.build(store)
    assert result["details"] == 3 and result["regions_per_detail"]["median"] == 4
    assert result["reviewer"]["picks_saved_near_identical"] > 0
    assert "details" in report.text(result)


def test_contact_sheet_refuses_a_tracked_path(tmp_path):
    store, _ = reviewable(tmp_path)
    with pytest.raises(sheets.Refused):
        contact.build(store, ROOT / "annotation" / "contact.png")


# ---------------------------------------------------------------- blocks

def test_a_block_of_tiny_pieces_is_one_object():
    """A strip drawn as hundreds of little lines would shatter into dust."""
    doc = new_doc()
    block = doc.blocks.new("STRIP")
    for k in range(60):
        block.add_lwpolyline([(k * 3, 0), (k * 3 + 2, 0), (k * 3 + 2, 3), (k * 3, 3)])
    msp = doc.modelspace()
    two_layers(msp)
    msp.add_blockref("STRIP", (0, 150))
    d = drawing.read(doc)
    assert d.skipped["block pieces read as one object"] == 60
    assert sum(1 for c in d.curves if c.in_block) == 1
    strip = at(analyse(doc), 90, 151.5)
    assert strip.kind == "loop" and area(strip) == pytest.approx(179 * 3, rel=0.02)


def test_a_rotated_block_of_pieces_keeps_its_direction():
    doc = new_doc()
    block = doc.blocks.new("STRIP")
    for k in range(60):
        block.add_lwpolyline([(k * 3, 0), (k * 3 + 2, 0), (k * 3 + 2, 3), (k * 3, 3)])
    msp = doc.modelspace()
    two_layers(msp)
    msp.add_blockref("STRIP", (300, 0), dxfattribs={"rotation": 30})
    d = drawing.read(doc)
    outline = next(c for c in d.curves if c.in_block)
    assert abs(planar.ring_area(outline.points)) == pytest.approx(179 * 3, rel=0.03)


def test_a_block_with_real_parts_is_left_alone():
    doc = new_doc()
    block = doc.blocks.new("BRACKET")
    for k in range(40):
        block.add_line((0, k), (100, k))
    msp = doc.modelspace()
    msp.add_blockref("BRACKET", (0, 0))
    note(msp, "NOTE", (0, -40))
    assert len(drawing.read(doc).curves) == 40


def test_a_block_never_inserted_is_not_on_the_sheet():
    doc = new_doc()
    doc.blocks.new("UNUSED").add_lwpolyline(box_points(0, 0, 500, 500), close=True)
    msp = doc.modelspace()
    two_layers(msp)
    assert len(drawing.read(doc).curves) == 2


def test_a_hidden_block_of_markers_stays_out_and_does_not_set_the_page():
    doc = new_doc()
    doc.layers.add("MARKS").freeze()
    block = doc.blocks.new("MARK")
    block.add_line((-40, 0), (40, 0), dxfattribs={"layer": "MARKS"})
    msp = doc.modelspace()
    two_layers(msp)
    msp.add_blockref("MARK", (-90000, 90000), dxfattribs={"layer": "MARKS"})
    d = drawing.read(doc)
    assert d.geometry_box()[0] == 0
    assert dxf_ingest.drawn_extents(doc)[0] > -1000


# ---------------------------------------------------------------- the legend

def legend_doc(*rows):
    """A sheet with a legend column to the right: (pattern, scale, words)."""
    doc = new_doc()
    msp = doc.modelspace()
    section(msp, 0, 0)
    for k, (pattern, scale, words) in enumerate(rows):
        y = 800 - k * 120
        loop(msp, 2000, y, 2100, y + 80)
        if pattern:
            hatch(msp, box_points(2000, y, 2100, y + 80), pattern, scale=scale)
        label = msp.add_mtext(words, dxfattribs={"insert": (2120, y + 40), "char_height": TEXT})
        label.set_location((2120, y + 40), attachment_point=ezdxf.enums.MTextEntityAlignment.MIDDLE_LEFT)
    return doc, msp


def test_a_legend_is_read_and_is_not_a_detail():
    doc, _ = legend_doc(("AR-CONC", 10, "CONCRETE SLAB"), ("ANSI31", 1, "MYSTERY STUFF"), ("SOLID", 1, "OTHER STUFF"), (None, 0, "BLANK"))
    _, sheet = split_of(doc)
    assert len(sheet.details) == 1
    assert [(e.pattern, e.text) for e in sheet.legend] == [
        ("AR-CONC", "CONCRETE SLAB"), ("ANSI31", "MYSTERY STUFF"), ("SOLID", "OTHER STUFF"), (None, "BLANK")]


def test_two_swatches_are_not_a_legend():
    doc, _ = legend_doc(("AR-CONC", 10, "CONCRETE SLAB"), ("ANSI31", 1, "MYSTERY STUFF"))
    assert split_of(doc)[1].legend == []


def sheet_analysis(doc):
    whole = pipeline.analyse_sheet(doc)
    assert len(whole.analyses) == 1
    return whole.analyses[0]


def test_a_legend_names_its_exact_hatch_with_its_words_verbatim():
    doc, _ = legend_doc(("AR-CONC", 10, "CONCRETE SLAB"), ("ANSI31", 1, "MYSTERY STUFF"), ("ANSI37", 5, "MORE STUFF"))
    p = proposal(sheet_analysis(doc), 50, 50)
    assert p["class_id"] == "slab" and p["source"] == "legend" and p["verbatim"] == ["CONCRETE SLAB"]
    assert "legend" in p["reason"] and "alias 'concrete slab'" in p["reason"]


def test_a_legend_word_with_no_alias_stays_unknown_and_keeps_the_word():
    doc, _ = legend_doc(("AR-CONC", 10, "MYSTERY STUFF"), ("ANSI31", 1, "MORE"), ("ANSI37", 5, "OTHER"))
    p = proposal(sheet_analysis(doc), 50, 50)
    assert p["class_id"] == names.UNKNOWN and p["source"] is None and p["verbatim"] == ["MYSTERY STUFF"]


def test_a_legend_at_another_scale_says_nothing():
    """A pattern can mean one thing at one scale and another at another: swatches and the
    drawing often use the same pattern name for different things."""
    doc, _ = legend_doc(("AR-CONC", 3, "CONCRETE SLAB"), ("ANSI31", 1, "MORE"), ("ANSI37", 5, "OTHER"))
    p = proposal(sheet_analysis(doc), 50, 50)
    assert p["class_id"] == names.UNKNOWN and p["verbatim"] == []


def test_two_legend_rows_for_one_hatch_keep_both_and_name_neither():
    doc, _ = legend_doc(("AR-CONC", 10, "CONCRETE SLAB"), ("AR-CONC", 10, "MYSTERY STUFF"), ("ANSI31", 1, "MORE"))
    p = proposal(sheet_analysis(doc), 50, 50)
    assert p["class_id"] == names.UNKNOWN and p["verbatim"] == ["CONCRETE SLAB", "MYSTERY STUFF"]
    assert p["candidates"] == ["slab"]


def test_two_legend_rows_for_one_hatch_name_nothing_even_when_they_map_to_one_class():
    """The sheet draws two things alike. That their words share an alias does not tell which
    one a region is, and a whole drawing's worth of that hatch would take the guess."""
    doc, _ = legend_doc(("AR-CONC", 10, "HOB KEPT"), ("AR-CONC", 10, "HOB REMOVED"), ("ANSI31", 1, "MORE"))
    p = proposal(sheet_analysis(doc), 50, 50)
    assert p["class_id"] == names.UNKNOWN and p["verbatim"] == ["HOB KEPT", "HOB REMOVED"] and p["candidates"] == ["hob"]


def test_a_legend_row_naming_two_things_offers_the_class_and_names_nothing():
    doc, _ = legend_doc(("AR-CONC", 10, "OLD HOBS/PIERS"), ("ANSI31", 1, "MORE"), ("ANSI37", 5, "OTHER"))
    p = proposal(sheet_analysis(doc), 50, 50)
    assert p["class_id"] == names.UNKNOWN and p["candidates"] == ["hob"] and p["verbatim"] == ["OLD HOBS/PIERS"]


def test_one_label_does_not_carry_across_a_hatch_the_legend_says_otherwise_about():
    doc, msp = legend_doc(("AR-CONC", 10, "MYSTERY STUFF"), ("ANSI31", 1, "MORE"), ("ANSI37", 5, "OTHER"))
    hatch(msp, box_points(0, -100, 200, 0), "AR-CONC", scale=10)
    label = note(msp, "BRICK WALL", (100, -50))
    label.set_location((100, -50), attachment_point=ezdxf.enums.MTextEntityAlignment.MIDDLE_CENTER)
    analysis = sheet_analysis(doc)
    assert proposal(analysis, 100, -80)["class_id"] == "brick_wall"
    assert proposal(analysis, 50, 50)["class_id"] == names.UNKNOWN


def test_a_label_naming_two_things_does_not_carry_across_its_hatch():
    doc = new_doc()
    msp = doc.modelspace()
    hatch(msp, box_points(0, 0, 200, 100), "AR-CONC", scale=10)
    hatch(msp, box_points(0, 200, 200, 300), "AR-CONC", scale=10)
    label = note(msp, "TIMBER/ BRICK WALL", (100, 50))
    label.set_location((100, 50), attachment_point=ezdxf.enums.MTextEntityAlignment.MIDDLE_CENTER)
    analysis = analyse(doc)
    assert proposal(analysis, 50, 20)["class_id"] == names.UNKNOWN
    assert proposal(analysis, 50, 20)["candidates"] == ["brick_wall"]
    assert proposal(analysis, 50, 250)["class_id"] == names.UNKNOWN


def test_a_solid_swatch_names_no_solid_line():
    doc, msp = legend_doc(("SOLID", 1, "SEALANT"), ("ANSI31", 1, "MORE"), ("ANSI37", 5, "OTHER"))
    hatch(msp, box_points(0, -10, 200, -7), "SOLID")
    analysis = sheet_analysis(doc)
    assert proposal(analysis, 100, -8.5)["class_id"] == names.UNKNOWN


def test_text_inside_a_region_labels_it():
    doc = new_doc()
    msp = doc.modelspace()
    two_layers(msp)
    label = note(msp, "WEEP HOLE", (60, 60))
    label.set_location((60, 60), attachment_point=ezdxf.enums.MTextEntityAlignment.MIDDLE_CENTER)
    p = proposal(analyse(doc), 50, 20)
    assert p["class_id"] == "weep_hole" and p["source"] == "callout" and "no leader" in p["reason"]


def test_a_long_note_inside_a_region_is_no_label():
    doc = new_doc()
    msp = doc.modelspace()
    two_layers(msp)
    label = note(msp, "CONCRETE SLAB TO BE CHECKED ON SITE BEFORE WORK", (60, 60))
    label.set_location((60, 60), attachment_point=ezdxf.enums.MTextEntityAlignment.MIDDLE_CENTER)
    assert proposal(analyse(doc), 50, 20)["class_id"] == names.UNKNOWN


def test_a_callouts_own_text_inside_a_region_is_not_read_twice():
    doc = new_doc()
    msp = doc.modelspace()
    two_layers(msp)
    callout(msp, "SCREED", (60, 115), at=(100, 60))
    assert proposal(analyse(doc), 50, 20)["class_id"] == names.UNKNOWN


def test_thin_solid_lines_on_a_sheet_are_a_group_and_compact_ones_are_not():
    doc = new_doc()
    msp = doc.modelspace()
    two_layers(msp)
    for y in (140, 160):
        hatch(msp, box_points(0, y, 200, y + 1), "SOLID")  # 1 unit is about 4 px here: thin
    for x in (0, 40):
        hatch(msp, box_points(x, 180, x + 30, 210), "SOLID")
    callout(msp, "MEMBRANE", (50, 140.5))
    callout(msp, "SEALANT", (10, 195), at=(10, 400))
    analysis = analyse(doc)
    assert proposal(analysis, 50, 160.5)["class_id"] == "membrane" and proposal(analysis, 50, 160.5)["source"] == "hatch"
    assert proposal(analysis, 55, 195)["class_id"] == names.UNKNOWN


def test_a_hatch_group_spans_the_details_of_a_file():
    doc, msp = sheet_of((0, 0), (900, 0))
    callout(msp, "CONCRETE SLAB", (50, 50), at=(100, 300))
    whole = pipeline.analyse_sheet(doc)
    second = whole.analyses[1]
    p = proposal(second, 950, 50)
    assert p["class_id"] == "slab" and p["source"] == "hatch" and "in detail 1" in p["reason"]


# ---------------------------------------------------------------- carrying names

def file_of_copies(tmp_path, *origins, change=None):
    doc, msp = sheet_of(*origins)
    if change:
        change(msp)
    path = tmp_path / "copies.dxf"
    doc.saveas(path)
    store = tmp_path / "store"
    return store, pipeline.propose_file(path, store)


def name_everything(record, word="slab"):
    for region in record["regions"]:
        if not region["dropped"]:
            sheets.correct(record, region["id"], word, {word})


def test_a_copy_gets_the_names_a_person_gave(tmp_path):
    store, (first, second) = file_of_copies(tmp_path, (0, 0), (900, 0))
    classes = ("slab", "screed", "membrane", "sealant")
    for region, class_id in zip(first["regions"], classes):
        sheets.correct(first, region["id"], class_id, set(classes))
    match = propagate.match_details(first, second)
    assert match.share == 1.0 and not match.mirrored and len(match.pairs) == 4
    assert propagate.between(first, second, by_hatch=False)["propagated"] == 4
    for a, b, _ in match.pairs:
        carried = sheets.region_of(second, b)["proposal"]
        assert carried["class_id"] == sheets.region_of(first, a)["proposal"]["class_id"]
        assert carried["source"] == "propagated" and carried["state"] == "proposed" and "near-identical" in carried["reason"]


def test_a_mirrored_copy_is_lined_up(tmp_path):
    doc = new_doc()
    msp = doc.modelspace()
    loop(msp, 0, 0, 200, 100)
    loop(msp, 0, 100, 60, 180)
    loop(msp, 60, 100, 200, 130)
    loop(msp, 900, 0, 1100, 100)
    loop(msp, 1040, 100, 1100, 180)
    loop(msp, 900, 100, 1040, 130)
    note(msp, "NOTE", (0, -40))
    path = tmp_path / "pair.dxf"
    doc.saveas(path)
    first, second = pipeline.propose_file(path, path.parent / "store")
    match = propagate.match_details(first, second)
    assert match.mirrored and len(match.pairs) == 3


def test_parts_that_moved_against_each_other_still_pair(tmp_path):
    """A variant keeps the wall and moves the slab: one offset cannot fit both."""
    doc = new_doc()
    msp = doc.modelspace()
    for x, rise in ((0, 0), (1000, 120)):
        for k in range(4):
            loop(msp, x + k * 53, 0, x + k * 53 + 50 + k, 40)
        for k in range(4):
            loop(msp, x + k * 53, 40 + rise, x + k * 53 + 50 + k, 90 + rise)
        lines(msp, [(x, 40), (x, 40 + rise + 1)], close=False)
    note(msp, "NOTE", (0, -40))
    path = tmp_path / "pair.dxf"
    doc.saveas(path)
    records = pipeline.propose_file(path, path.parent / "store")
    assert len(records) == 2
    assert len(propagate.match_details(*records).pairs) == 8


def test_different_details_carry_nothing(tmp_path):
    doc = new_doc()
    msp = doc.modelspace()
    section(msp, 0, 0)
    loop(msp, 900, 0, 1000, 400)
    loop(msp, 1000, 0, 1400, 30)
    loop(msp, 1000, 30, 1400, 70)
    loop(msp, 1000, 70, 1020, 90)
    loop(msp, 1020, 70, 1040, 90)
    note(msp, "NOTE", (0, -40))
    path = tmp_path / "pair.dxf"
    doc.saveas(path)
    first, second = pipeline.propose_file(path, path.parent / "store")
    assert propagate.match_details(first, second) is None
    name_everything(first)
    assert propagate.between(first, second, by_hatch=False)["propagated"] == 0


def test_a_carried_name_never_overwrites_a_person_or_the_sheet(tmp_path):
    store, (first, second) = file_of_copies(tmp_path, (0, 0), (900, 0))
    name_everything(first, "slab")
    decided, printed = second["regions"][0], second["regions"][1]
    sheets.correct(second, decided["id"], "screed", {"screed"})
    printed["proposal"].update(class_id="packer", source="callout")
    propagate.between(first, second, by_hatch=False)
    assert decided["proposal"]["class_id"] == "screed" and decided["proposal"]["state"] == "corrected"
    assert printed["proposal"]["class_id"] == "packer" and printed["proposal"]["source"] == "callout"


def test_a_region_like_a_rejected_one_is_set_aside_and_can_come_back(tmp_path):
    store, (first, second) = file_of_copies(tmp_path, (0, 0), (900, 0))
    name_everything(first)
    sheets.reject(first, first["regions"][0]["id"])
    assert propagate.between(first, second, by_hatch=False)["set_aside"] == 1
    gone = next(r for r in second["regions"] if r["dropped"])
    assert "rejected in detail 1" in gone["dropped"]
    sheets.restore(second, gone["id"])
    assert gone["dropped"] is None


def test_regions_left_over_can_take_a_class_from_the_same_hatch_and_shape(tmp_path):
    doc = new_doc()
    msp = doc.modelspace()
    section(msp, 0, 0)
    hatch(msp, box_points(900, 0, 1500, 300), "AR-CONC", scale=10)
    for k in range(4):
        loop(msp, 1500, k * 60, 1560, k * 60 + 58)
    note(msp, "NOTE", (0, -40))
    path = tmp_path / "pair.dxf"
    doc.saveas(path)
    first, second = pipeline.propose_file(path, path.parent / "store")
    name_everything(first)
    counts = propagate.between(first, second)
    p = next(r for r in second["regions"] if r["hatch"])["proposal"]
    assert counts["hatch"] == 1 and p["source"] == "hatch" and "named by a person" in p["reason"]


def test_a_hatch_means_nothing_outside_its_own_file(tmp_path):
    """Regression: hatch names carried between files were wrong 9 times in 20. Two offices can
    use one pattern for two materials."""
    doc = new_doc()
    msp = doc.modelspace()
    section(msp, 0, 0)
    hatch(msp, box_points(900, 0, 1500, 300), "AR-CONC", scale=10)
    for k in range(4):
        loop(msp, 1500, k * 60, 1560, k * 60 + 58)
    note(msp, "NOTE", (0, -40))
    path = tmp_path / "pair.dxf"
    doc.saveas(path)
    first, second = pipeline.propose_file(path, tmp_path / "store")
    name_everything(first)
    second["source"]["sha256"] = "another file"
    assert propagate.between(first, second)["hatch"] == 0


def test_near_identical_shapes_do_carry_between_files(tmp_path):
    store, (first, second) = file_of_copies(tmp_path, (0, 0), (900, 0))
    name_everything(first)
    second["source"]["sha256"] = "another file"
    assert propagate.between(first, second)["propagated"] == 4


def test_finishing_a_detail_on_the_page_carries_its_names(tmp_path):
    store, records = file_of_copies(tmp_path, (0, 0), (900, 0), (1800, 0))
    instance = review.make_server(store, port=0)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        first = records[0]
        path = f"/api/sheets/{first['sheet_id']}"
        ids = [r["id"] for r in first["regions"]]
        for region_id in ids[:-1]:
            call(instance, "POST", f"{path}/correct", {"region": region_id, "class_id": "slab"})
        assert sheets.read_sheet(store, records[1]["sheet_id"])["regions"][0]["proposal"]["source"] is None
        data = json.loads(call(instance, "POST", f"{path}/correct", {"region": ids[-1], "class_id": "slab"})[1])
        assert data["result"]["carried"]["propagated"] == 8 and data["result"]["carried"]["details"] == 2
        carried = sheets.read_sheet(store, records[1]["sheet_id"])
        assert all(r["proposal"]["source"] == "propagated" for r in carried["regions"])
        events = (store / records[1]["sheet_id"] / "events.jsonl").read_text()
        assert '"carried"' in events and first["sheet_id"] in events
        path = f"/api/sheets/{records[1]['sheet_id']}"
        data = json.loads(call(instance, "POST", f"{path}/acceptall", {"seconds": 5.0})[1])
        assert len(data["result"]["accepted"]) == 4 and data["summary"]["done"]
        assert all(r["seconds"] == 1.25 for r in data["regions"])
        assert call(instance, "POST", f"{path}/acceptall", {})[0].status == 409
    finally:
        instance.shutdown()
        instance.server_close()


def test_simulation_counts_picks_saved_and_saves_nothing_to_disk(tmp_path):
    store, records = file_of_copies(tmp_path, (0, 0), (900, 0), (1800, 0))
    before = [sheets.read_sheet(store, r["sheet_id"]) for r in records]
    result = propagate.simulate(records, lambda record, region: "slab", by_hatch=False)
    assert result["regions"] == 12 and result["picks_without"] == 12 and result["picks_with"] == 4
    assert result["propagated"] == 8 and result["details_no_pick"] == 2
    assert before == [sheets.read_sheet(store, r["sheet_id"]) for r in records]


def test_propagation_is_scored_against_truth_on_synthetic_sheets(measured):
    folder, rows = measured
    measure.run_variant(folder, rows, "clean", 3, log=lambda *_: None)
    truths = {Path(r["truth"]).stem: json.loads(Path(r["truth"]).read_text()) for r in rows}
    result = measure.propagation(folder / "clean" / "sheets", truths, 3)
    copies = result["near_copies"]
    assert copies["right"] > 20 and copies["wrong"] == 0
    assert result["across_sheets"]["regions"] > 20
