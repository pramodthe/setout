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
