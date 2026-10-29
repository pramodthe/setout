"""Render -> compare -> correct: the verifier guard decides, not the model.
Muse is stubbed; everything else (gates, compile, build, verify) is real."""

import copy
import json

import pytest

from app import generate as gen
from app import store
from app.generate import EXAMPLE, _llm_view, gate
from app.program import DetailProgram, compile_program
from app.spec import SourceDoc

READ = {"drawing_type": "isometric_room", "rotation_deg": 0, "title": "",
        "elements": [{"label_verbatim": "Waterproofed walls", "kind": "label", "where": ""}],
        "dimensions": [{"value_mm": 150, "quote_verbatim": "150mm", "applies_to": "upturn"}]}
PNG = "data:image/jpeg;base64,AAAA"


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "VAR", tmp_path)
    raw = copy.deepcopy(EXAMPLE)
    raw["parts"][1]["label"] = "Waterproofed walls"
    raw, _ = gate(raw, READ)
    src = SourceDoc(filename="t.pdf", sha256="e" * 64, page_index=0, crop_id="p0_x",
                    page_width_pt=595, page_height_pt=842)
    prog = gen._to_program(raw, DetailProgram(detail_id="ai-c", title="t", origin="ai-generated:test",
                                              kind="room", source=src))
    (tmp_path / "library").mkdir()
    (tmp_path / "library" / "ai-c.json").write_text(json.dumps({
        "program": prog.model_dump(mode="json"),
        "spec": compile_program(prog).model_dump(mode="json"), "read": READ}))
    fig = tmp_path / "fig.png"
    fig.write_bytes(b"\x89PNG")
    monkeypatch.setattr(gen, "find_document_by_sha", lambda sha: {"id": "d"})
    monkeypatch.setattr(gen, "figure_path", lambda d, c: fig)
    return prog


def _answer(monkeypatch, program_view: dict, issues=None):
    monkeypatch.setattr(gen.meta_client, "respond_json", lambda *a, **k: (
        {"issues": issues or [], "program": program_view}, None))


def test_harmless_fix_is_accepted_and_recorded(lib, monkeypatch):
    view = _llm_view(lib)
    for q in view["params"]:
        if q["name"] == "wall_t":
            q["value"] = 110  # an assumption, nudged
    _answer(monkeypatch, view, [{"part_id": "back_wall", "problem": "too thin", "fix": "110"}])
    v = gen.critique("ai-c", [PNG, PNG])
    assert v["accepted"] and v["diff"]["params_changed"] == ["wall_t"]
    rec = json.loads((store.VAR / "library" / "ai-c.json").read_text())
    assert rec["revisions"][-1]["accepted"]
    assert any(q["name"] == "wall_t" and q["value"] == 110 for q in rec["program"]["params"])


def test_fix_that_loses_drawing_evidence_is_rejected(lib, monkeypatch):
    view = _llm_view(lib)
    for part in view["parts"]:
        if part["id"] == "membrane_upturn_back":  # the only user of upturn_h (150mm)
            for d in part["dims"]:
                if d["name"] == "y1":
                    d["expr"] = "floor + 120"
    _answer(monkeypatch, view)
    v = gen.critique("ai-c", [PNG])
    assert not v["accepted"] and "lose drawing evidence" in v["reason"]
    rec = json.loads((store.VAR / "library" / "ai-c.json").read_text())
    up = next(p for p in rec["program"]["parts"] if p["id"] == "membrane_upturn_back")
    assert any(d["expr"] == "floor + upturn_h" for d in up["dims"])  # unchanged


def test_no_change_is_not_a_revision_of_the_detail(lib, monkeypatch):
    _answer(monkeypatch, _llm_view(lib))
    v = gen.critique("ai-c", [PNG])
    assert not v["accepted"] and v["reason"] == "no changes proposed"


def test_cad_parts_are_frozen(lib, monkeypatch, tmp_path):
    # make the bath a cad part in the stored record
    rec = json.loads((tmp_path / "library" / "ai-c.json").read_text())
    for p in rec["program"]["parts"]:
        if p["id"] == "bath":
            p.update(shape="cad", fixture=None, cad_brief="freestanding tub")
    prog = DetailProgram.model_validate(rec["program"])
    spec = compile_program(prog)
    (tmp_path / "library" / "cad" / "ai-c").mkdir(parents=True)
    tri = [0, 0, 0, 100, 0, 0, 0, 100, 100]
    (tmp_path / "library" / "cad" / "ai-c" / "bath.json").write_text(json.dumps({"positions": tri}))
    spec.context["cad_parts"] = {"bath": {"file": "cad/ai-c/bath.json"}}
    rec.update(program=prog.model_dump(mode="json"), spec=spec.model_dump(mode="json"))
    (tmp_path / "library" / "ai-c.json").write_text(json.dumps(rec))

    view = _llm_view(prog)
    for part in view["parts"]:
        if part["id"] == "bath":
            part["cad_brief"] = "something else entirely"
            part["dims"] = [{"name": d["name"], "expr": "1"} for d in part["dims"]]
        if part["id"] == "back_wall":
            part["note"] = "checked"
    _answer(monkeypatch, view)
    v = gen.critique("ai-c", [PNG])
    rec = json.loads((tmp_path / "library" / "ai-c.json").read_text())
    bath = next(p for p in rec["program"]["parts"] if p["id"] == "bath")
    assert bath["cad_brief"] == "freestanding tub"
    assert v["accepted"]  # the back_wall note change went through


def test_renders_must_be_images(lib, monkeypatch):
    with pytest.raises(ValueError):
        gen.critique("ai-c", ["http://example.com/x.png"])
