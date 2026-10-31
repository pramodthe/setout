"""The section route as the app runs it. Muse is stubbed, so no network and no tokens."""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from app import section_draft
from goal import compose, hidden, regions


def drawing(path):
    """Three courses of brick on a slab, with an air gap beside them."""
    image = Image.new("L", (400, 400), 255)
    pen = ImageDraw.Draw(image)
    for row in range(3):
        pen.rectangle((40, 40 + row * 60, 140, 92 + row * 60), outline=0, width=2)
    pen.rectangle((40, 220, 360, 320), outline=0, width=2)
    image.save(path)
    return path


LABELS = lambda found: {"assembly": "brick wall on a slab", "regions": [  # noqa: E731
    {"id": r.id, "family": "concrete" if r.width > 200 else "brick", "depth": "continuous" if r.width > 200 else "units"} for r in found]}


def test_a_drawing_gives_closed_regions_and_the_outside_is_none_of_them(tmp_path):
    found, size = regions.find(drawing(tmp_path / "s.png"))
    assert len(found) == 4 and size == (400, 400)


def test_a_small_section_cannot_list_more_than_real_assemblies_hold(tmp_path):
    found, size = regions.find(drawing(tmp_path / "s.png"))
    many = [{"family": "fixing", "what": "bolt", "shape": "rod", "near_region": 1, "columns": 500, "rows": 40,
             "across": 0.01, "up": 0.1, "along": 0.01}]
    assert sum(g["columns"] * g["rows"] for g in hidden.calibrated(many, found, 10)) <= hidden.PIECES_PER_REGION * len(found) * 1.2
    few = [{**many[0], "columns": 3, "rows": 1}]
    assert hidden.calibrated(few, found, 10) == few


def test_what_the_drawing_shows_and_what_it_does_not_stay_labelled(tmp_path):
    found, size = regions.find(drawing(tmp_path / "s.png"))
    marked = {r["id"]: r for r in LABELS(found)["regions"]}
    groups = [{"family": "fixing", "what": "wall tie", "shape": "rod", "near_region": 1, "columns": 4, "rows": 1,
               "across": 0.1, "up": 0.01, "along": 0.01}]
    scene = section_draft.scene_of(hidden.assemble(found, marked, groups, size, compose.DEPTH_SHARE), "t", "a", 2.0, size[1])
    by = {s["provenance"] for s in scene["solids"]}
    assert by == {"assumption", "inferred"}  # never `drawing`: the picture gave no measurement
    assert all(s["inferred_by"] for s in scene["solids"] if s["provenance"] == "inferred")
    assert not any(s["params"] for s in scene["solids"]) and scene["verification"] == []
    assert len(scene["install_order"]) == len(scene["solids"]) and scene["solids"][0]["name"] == "concrete"


def test_a_draft_runs_end_to_end_and_never_touches_a_version(tmp_path, monkeypatch):
    from goal import labels
    picture = drawing(tmp_path / "s.png")
    monkeypatch.setattr(section_draft, "VAR", tmp_path)
    monkeypatch.setattr(section_draft, "figure_path", lambda doc, crop: picture)
    monkeypatch.setattr(labels, "ask", lambda p, found, size: (LABELS(found), {"total_tokens": 7}))
    monkeypatch.setattr(hidden, "ask", lambda p, found, size, labelled: ({"groups": []}, {"total_tokens": 5}))
    ident = "a" * 32
    section_draft._save(ident, {"id": ident, "state": "running"})
    section_draft._run(ident, "doc", "crop")
    record = section_draft.get(ident)
    assert record["state"] == "done" and record["tokens"] == 12 and record["from_drawing"] > 4 and record["inferred"] == 0
    assert record["scene"]["origin"] == "section-draft-v0"


def test_a_drawing_with_no_outlines_fails_with_a_message(tmp_path, monkeypatch):
    blank = tmp_path / "b.png"
    Image.new("L", (100, 100), 255).save(blank)
    monkeypatch.setattr(section_draft, "VAR", tmp_path)
    monkeypatch.setattr(section_draft, "figure_path", lambda doc, crop: blank)
    ident = "b" * 32
    section_draft._save(ident, {"id": ident, "state": "running"})
    section_draft._run(ident, "doc", "crop")
    assert section_draft.get(ident)["state"] == "failed" and "outlines" in section_draft.get(ident)["error"]


@pytest.mark.parametrize("ident", ["../etc/passwd", "abc", "A" * 32])
def test_a_draft_id_is_only_ever_a_token(ident):
    assert section_draft.get(ident) is None


def test_hidden_parts_do_not_repeat_what_the_section_built(tmp_path):
    found, size = regions.find(drawing(tmp_path / "s.png"))
    marked = {r["id"]: r for r in LABELS(found)["regions"]}
    repeat = {"family": "brick", "what": "more bricks", "shape": "unit", "near_region": 1, "columns": 9, "rows": 1,
              "across": 0.1, "up": 0.05, "along": 0.2}
    joints = {**repeat, "family": "mortar", "what": "head joints"}
    tie = {**repeat, "family": "reinforcement", "what": "wall tie", "shape": "bar", "columns": 2}
    alone = hidden.assemble(found, marked, [], size, compose.DEPTH_SHARE)
    both = hidden.assemble(found, marked, [repeat, joints], size, compose.DEPTH_SHARE)
    assert len(both) == len(alone)
    assert len(hidden.assemble(found, marked, [tie], size, compose.DEPTH_SHARE)) == len(alone) + 2


def test_ties_lie_in_the_mortar_beds_the_section_shows(tmp_path):
    found, size = regions.find(drawing(tmp_path / "s.png"))
    marked = {r["id"]: r for r in LABELS(found)["regions"]}
    brick = next(r for r in found if r.width < 200)
    bed = next(r for r in found if r.id != brick.id and r.width < 200)
    marked[bed.id] = {"family": "mortar", "depth": "continuous"}
    tie = {"family": "reinforcement", "what": "wall tie", "shape": "bar", "near_region": brick.id, "columns": 1, "rows": 5,
           "across": 0.1, "up": 0.01, "along": 0.01}
    built = hidden.build([tie], found, size, 0.8, marked)
    assert len(built) == 1
    assert abs(built[0]["triangles"][..., 2].mean() - (size[1] - (bed.box[1] + bed.box[3]) / 2)) < 12


def test_a_cutaway_steps_layers_back_and_keeps_the_base_whole(tmp_path):
    found, size = regions.find(drawing(tmp_path / "s.png"))
    marked = {r["id"]: r for r in LABELS(found)["regions"]}
    runs = compose.stepped(found, marked, size, "left", 300.0)
    slab = next(r for r in found if r.width > 200)
    assert slab.id not in runs and all(0 < run <= 300 for run in runs.values())
    whole = max(p["triangles"][..., 1].max() for p in compose.build(found, marked, size))
    cut = compose.build(found, marked, size, compose.DEPTH_SHARE, "left")
    bricks = [p for p in cut if p["family"] == "brick"]
    assert max(p["triangles"][..., 1].max() for p in bricks) < whole
    assert max(p["triangles"][..., 1].max() for p in cut) == pytest.approx(whole)  # the base still runs the whole way


def test_a_blender_scene_reads_as_a_detail_and_stays_a_trial(tmp_path, monkeypatch):
    from app import blender_details
    monkeypatch.setattr(blender_details, "VAR", tmp_path)
    row = {"name": "membrane", "material": "membrane", "group": "layers", "pieces": 2, "tris": [10.0, 20.0, 30.0] * 3}
    blender_details.publish("blender-test", "A wall", "blender-trial", [row, {**row, "name": "fixing", "material": "galvanised"}])
    scene = blender_details.scene("blender-test")
    first = scene["solids"][0]
    assert first["tris"][:3] == [20.0, 30.0, 0.0]  # across, up, and the cut face nearest the section view
    assert scene["install_order"] == ["membrane", "fixing"] and scene["solids"][1]["layer"] == "steel"
    assert first["provenance"] == "assumption" and "verified" in first["note"] and scene["verification"] == []
    assert blender_details.scene("blender-../x") is None and blender_details.scene("other") is None
