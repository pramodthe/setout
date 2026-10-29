"""Review/publish, SAM drawing links and public demo mode (no network)."""

import copy
import io
import json

import pytest

from app import generate as gen
from app import review, sam, store
from app.generate import EXAMPLE, gate
from app.program import DetailProgram, compile_program
from app.spec import SourceDoc

READ = {"drawing_type": "isometric_room", "rotation_deg": 90, "title": "",
        "elements": [{"label_verbatim": "Waterproofed walls", "kind": "label", "where": ""},
                     {"label_verbatim": "Bath", "kind": "label", "where": ""}],
        "dimensions": [{"value_mm": 150, "quote_verbatim": "150mm", "applies_to": "upturn"}]}


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "VAR", tmp_path)
    raw, _ = gate(copy.deepcopy(EXAMPLE), READ)
    src = SourceDoc(filename="t.pdf", sha256="d" * 64, page_index=0, crop_id="p0_x",
                    page_width_pt=595, page_height_pt=842)
    prog = gen._to_program(raw, DetailProgram(detail_id="ai-r", title="t", source=src,
                                              origin="ai-generated:test (unreviewed; x)", kind="room"))
    (tmp_path / "library").mkdir()
    (tmp_path / "library" / "ai-r.json").write_text(json.dumps({
        "program": prog.model_dump(mode="json"), "read": READ,
        "spec": compile_program(prog).model_dump(mode="json")}))
    from PIL import Image
    fig = tmp_path / "fig.png"
    Image.new("RGB", (40, 20), "white").save(fig)
    monkeypatch.setattr(gen, "find_document_by_sha", lambda sha: {"id": "d"})
    monkeypatch.setattr(gen, "figure_path", lambda d, c: fig)
    return tmp_path


def _dim(spec_json, part, name):
    c = next(c for c in spec_json["components"] if c["id"] == part)
    return next(p for p in c["params"] if p["name"] == name)


def test_edit_moves_every_dependent_dimension(lib):
    r = review.apply("ai-r", [{"name": "wall_t", "value": 110}], [], [])
    wt = next(p for p in r["params"] if p["name"] == "wall_t")
    assert wt["provenance"] == "user_input" and wt["edited"] == {"from": 90, "to": 110}
    assert set(wt["used_by"]) >= {"floor_slab", "back_wall"}
    spec = json.loads((lib / "library" / "ai-r.json").read_text())["spec"]
    assert _dim(spec, "back_wall", "x0")["value"] == -110  # "0 - wall_t" followed the edit
    assert _dim(spec, "floor_slab", "z0")["value"] == -110


def test_drawing_values_listed_first_and_accept_is_recorded(lib):
    r = review.apply("ai-r", [], ["upturn_h"], [])
    assert r["params"][0]["name"] == "upturn_h" and r["params"][0]["provenance"] == "drawing"
    assert r["params"][0]["accepted"] and r["status"] == "draft"


def test_labels_only_from_the_drawing(lib):
    r = review.apply("ai-r", [], [], [{"part_id": "bath", "label": "Bath"}])
    assert next(p for p in r["parts"] if p["id"] == "bath")["label"] == "Bath"
    with pytest.raises(ValueError):
        review.apply("ai-r", [], [], [{"part_id": "bath", "label": "Luxury spa tub"}])


def test_breaking_edit_is_rejected_and_nothing_saved(lib):
    before = (lib / "library" / "ai-r.json").read_text()
    with pytest.raises(ValueError):
        review.apply("ai-r", [{"name": "room_d", "value": -100}], [], [])  # z1 < z0
    assert (lib / "library" / "ai-r.json").read_text() == before


def test_publish_needs_a_name_and_marks_the_detail(lib):
    with pytest.raises(ValueError):
        review.apply("ai-r", [], [], [], publish=True, reviewer=" ")
    r = review.apply("ai-r", [], [], [], publish=True, reviewer="Pramod")
    assert r["status"] == "reviewed" and r["by"] == "Pramod"
    spec = json.loads((lib / "library" / "ai-r.json").read_text())["spec"]
    assert "reviewed by Pramod" in spec["origin"] and spec["context"]["review_status"] == "reviewed"


def test_sam_parsing_prompts_and_confidence_floor():
    text = ("<0f>0<|box;x1=10;y1=20;x2=30;y2=40;w=100;h=200;c=0.9|><|mask;x|>"
            "1<|box;x1=0;y1=0;x2=5;y2=5;w=100;h=200;c=0.2|>")
    boxes = sam.parse_boxes(text)
    assert boxes[0] == {"x0": 0.1, "y0": 0.1, "x1": 0.3, "y1": 0.2, "c": 0.9}
    assert sam.prompt_for("bath", None) == "bath"
    assert sam.prompt_for("membrane", None) is None  # thin layers: SAM can't see them
    assert sam.prompt_for("membrane", "Waterproofed walls") == "Waterproofed walls"
    found = sam.locate(b"png", [("bath", "bath", None), ("m", "membrane", None)],
                       call=lambda png, prompt: boxes)
    assert set(found) == {"bath"} and found["bath"]["c"] == 0.9  # 0.2 box dropped
    assert sam.prompts_for("basin_vanity", None) == ["basin vanity", "washbasin", "sink"]
    assert sam.prompts_for("bath", "Bath") == ["Bath", "bath", "bathtub"]
    # the best-scoring name wins
    scores = {"bath": 0.66, "bathtub": 0.91}
    best = sam.locate(b"png", [("tub", "bath", None)], call=lambda png, pr: [
        {"x0": 0, "y0": 0, "x1": 1, "y1": 1, "c": scores.get(pr, 0)}])
    assert best["tub"]["prompt"] == "bathtub"


def test_locate_stores_regions_on_the_upright_drawing(lib):
    seen = {}

    def call(png, prompt):
        from PIL import Image
        seen["size"] = Image.open(io.BytesIO(png)).size
        return [{"x0": 0.1, "y0": 0.1, "x1": 0.5, "y1": 0.5, "c": 0.8}] if prompt == "Bath" else []

    review.apply("ai-r", [], [], [{"part_id": "bath", "label": "Bath"}])
    out = gen.locate_on_drawing("ai-r", call=call)
    assert out["located"] == 1 and seen["size"] == (20, 40)  # rotated 90 degrees upright
    spec = json.loads((lib / "library" / "ai-r.json").read_text())["spec"]
    assert spec["context"]["drawing_regions"]["bath"]["prompt"] == "Bath"
    assert spec["context"]["drawing_rotation_deg"] == 90
    review.apply("ai-r", [{"name": "wall_t", "value": 100}], [], [])  # recompile
    spec = json.loads((lib / "library" / "ai-r.json").read_text())["spec"]
    assert "bath" in spec["context"]["drawing_regions"]  # links survive edits


def test_public_mode_blocks_writes_and_drawings(monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as m
    monkeypatch.setattr(m, "PUBLIC", True)
    monkeypatch.setattr(m, "GUIDE_PER_HOUR", 0)
    c = TestClient(m.app)
    assert c.post("/api/documents", files={"file": ("x.pdf", b"%PDF-1", "application/pdf")}).status_code == 403
    assert c.get("/api/specs/conpro-door-sill-D3/drawing").status_code == 403
    assert c.patch("/api/specs/conpro-door-sill-D3", json={"component_id": "x"}).status_code == 403
    assert c.post("/api/guide", json={"detail_id": "x", "question": "q"}).status_code == 429
    assert c.get("/api/specs/conpro-door-sill-D3/scene").status_code == 200  # browsing works


def test_preview_is_our_render_and_public_can_only_read(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import app.main as m
    monkeypatch.setattr(store, "VAR", tmp_path)
    c = TestClient(m.app)
    jpeg = b"\xff\xd8\xff\xe0" + b"0" * 64
    url = "/api/specs/conpro-door-sill-D3/preview"
    assert c.get(url).status_code == 404
    assert c.post(url, content=b"\x89PNG....").status_code == 400  # JPEG only
    assert c.post("/api/specs/no-such-detail/preview", content=jpeg).status_code == 404
    assert c.post("/api/specs/..%2Fx/preview", content=jpeg).status_code == 404
    assert c.post(url, content=jpeg).status_code == 200
    assert c.get(url).content == jpeg
    monkeypatch.setattr(m, "PUBLIC", True)
    assert c.get(url).status_code == 200  # a render, not a source drawing
    assert c.post(url, content=jpeg).status_code == 403
