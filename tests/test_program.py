"""Spec v2 detail programs: evaluator, provenance flow, compile -> build ->
verify, and the deterministic AI gates (no model calls)."""

import pytest

from app.generate import gate
from v2_example import EXAMPLE
from app.geometry import build_assembly
from app.program import DetailProgram, compile_program, evaluate, expr_names
from app.spec import Provenance, SourceDoc
from app.verify import summarize, verify_all

SRC = SourceDoc(filename="t.pdf", sha256="0" * 64, page_index=0, crop_id="p0_x",
                page_width_pt=595, page_height_pt=842)

READ = {"drawing_type": "isometric_room", "rotation_deg": 0, "title": "",
        "elements": [{"label_verbatim": "Waterproofed walls", "kind": "label",
                      "where": "left"}],
        "dimensions": [{"value_mm": 150, "quote_verbatim": "150mm",
                        "applies_to": "upturn"}]}


def _program(raw: dict) -> DetailProgram:
    return DetailProgram(
        detail_id="t", title=raw["title"], origin="test", kind=raw["kind"],
        situation=raw["situation"], source=SRC,
        params=[{**q, "source_quote": q["source_quote"] or None,
                 "note": q["note"] or None} for q in raw["params"]],
        parts=[{**p, "note": p["note"] or None} for p in raw["parts"]])


def test_evaluator_arithmetic_and_names():
    env = {"a": 100.0, "b": 50.0}
    assert evaluate("a + b / 2", env) == 125
    assert evaluate("-a", env) == -100
    assert evaluate("max(a, b) - min(a, b)", env) == 50
    assert expr_names("floor + upturn_h") == {"floor", "upturn_h"}


@pytest.mark.parametrize("bad", ["__import__('os')", "a.b", "a ** 2", "[1]",
                                 "lambda: 1", "open('x')", "a if b else c"])
def test_evaluator_rejects_code(bad):
    with pytest.raises(ValueError):
        evaluate(bad, {"a": 1.0, "b": 1.0, "c": 1.0})


def test_unknown_name_rejected():
    with pytest.raises(ValueError):
        evaluate("nope + 1", {})


def test_example_compiles_builds_and_verifies():
    raw = {**EXAMPLE, "parts": [dict(p) for p in EXAMPLE["parts"]]}
    raw["parts"][1]["label"] = "Waterproofed walls"  # C-labels needs >= 1
    prog, _ = gate(raw, READ)
    spec = compile_program(_program(prog))
    meshes = build_assembly(spec)
    assert all(m.tri_count > 0 for m in meshes.values())
    results = verify_all(spec, meshes, None)
    rep = summarize(results)
    assert rep["failed"] == [], rep
    built = [r for r in results if r.id.startswith("B-built-")]
    assert len(built) == len(spec.components) and all(r.status == "PASS" for r in built)
    trace = {r.id: r for r in results if r.id.startswith("C-trace-")}
    assert trace["C-trace-upturn_h"].status == "PASS"


def test_provenance_flows_through_expressions():
    prog, _ = gate(EXAMPLE, READ)
    spec = compile_program(_program(prog))
    up = spec.component("membrane_upturn_back").param("y1")  # floor + upturn_h
    assert up.provenance == Provenance.DRAWING  # datum floor is neutral
    assert up.source_quote == "150mm" and up.value == 150
    x1 = spec.component("membrane_upturn_back").param("x1")  # room_w
    assert x1.provenance == Provenance.ASSUMPTION
    z1 = spec.component("back_wall").param("z1")  # bare literal "0"
    assert z1.provenance == Provenance.ASSUMPTION


def test_gate_demotes_unconfirmed_drawing_values():
    raw = {**EXAMPLE, "params": [dict(q) for q in EXAMPLE["params"]]}
    for q in raw["params"]:
        if q["name"] == "upturn_h":
            q["value"], q["source_quote"] = 200, "200mm"  # not printed
    prog, notes = gate(raw, READ)
    q = next(q for q in prog["params"] if q["name"] == "upturn_h")
    assert q["provenance"] == "assumption" and "not confirmed" in q["note"]
    assert any("upturn_h" in n for n in notes)


def test_gate_value_must_match_quote():
    raw = {**EXAMPLE, "params": [dict(q) for q in EXAMPLE["params"]]}
    for q in raw["params"]:
        if q["name"] == "upturn_h":
            q["value"] = 100  # quote "150mm" exists, number does not match
    prog, _ = gate(raw, READ)
    q = next(q for q in prog["params"] if q["name"] == "upturn_h")
    assert q["provenance"] == "assumption"


def test_gate_keeps_only_verbatim_labels():
    raw = {**EXAMPLE, "parts": [dict(p) for p in EXAMPLE["parts"]]}
    raw["parts"][1]["label"] = "waterproofed  WALLS"  # same words, case/space
    raw["parts"][2]["label"] = "Invented label"
    prog, notes = gate(raw, READ)
    assert prog["parts"][1]["label"] == "Waterproofed walls"
    assert prog["parts"][2]["label"] is None
    assert any("Invented label" in n for n in notes)


def test_program_rejects_missing_dims_and_bad_ids():
    raw = {**EXAMPLE, "parts": [dict(p) for p in EXAMPLE["parts"]]}
    raw["parts"][0] = {**raw["parts"][0], "dims": raw["parts"][0]["dims"][:2]}
    with pytest.raises(ValueError):
        _program(raw)


# ---------------------------------------------------------------- new shapes

from app.geometry import FIXTURES, triangulate  # noqa: E402


def _one_part(part: dict, params: list[dict] | None = None):
    raw = {"title": "t", "kind": "section", "situation": [],
           "params": params or [{"name": "a", "value": 100, "unit": "mm",
                                 "provenance": "assumption", "source_quote": "",
                                 "kind": "exact", "note": ""}],
           "parts": [{"label": "", "install_after": [], "note": "", "fixture": "",
                      "dims": [], "path": [], **part}]}
    prog, _ = gate(raw, READ)
    spec = compile_program(_program(prog))
    meshes = build_assembly(spec)
    return spec, meshes, verify_all(spec, meshes, None)


def test_triangulate_concave_outline_preserves_area():
    L = [(0, 0), (100, 0), (100, 10), (10, 10), (10, 80), (0, 80)]  # L-angle
    tris = triangulate(L)
    area = sum(abs((L[b][0] - L[a][0]) * (L[c][1] - L[a][1])
                   - (L[b][1] - L[a][1]) * (L[c][0] - L[a][0])) / 2 for a, b, c in tris)
    assert len(tris) == 4 and abs(area - (100 * 10 + 10 * 70)) < 1e-9
    assert triangulate(L[::-1])  # either winding


def test_profile_extrudes_outline_exactly():
    path = [{"x": x, "y": y, "z": ""} for x, y in
            [("0", "0"), ("a", "0"), ("a", "10"), ("10", "10"), ("10", "80"), ("0", "80")]]
    spec, meshes, res = _one_part({"id": "angle", "type": "waterstop", "shape": "profile",
                                   "dims": [{"name": "z0", "expr": "0"}, {"name": "z1", "expr": "a * 6"}],
                                   "path": path})
    assert meshes["angle"].bounds() == (0, 0, 0, 100, 80, 600)
    assert next(r for r in res if r.id == "B-built-angle").status == "PASS"


def test_revolve_bounds_are_exact():
    path = [{"x": "a / 2", "y": "0", "z": ""}, {"x": "a", "y": "40", "z": ""}]
    spec, meshes, res = _one_part({"id": "waste", "type": "waste", "shape": "revolve",
                                   "dims": [{"name": "cx", "expr": "300"}, {"name": "cz", "expr": "200"}],
                                   "path": path})
    b = meshes["waste"].bounds()
    assert [round(v, 6) for v in b] == [200, 0, 100, 400, 40, 300]
    assert next(r for r in res if r.id == "B-built-waste").status == "PASS"


def test_sweep_follows_its_path():
    path = [{"x": "0", "y": "0", "z": "0"}, {"x": "0", "y": "0", "z": "500"},
            {"x": "300", "y": "0", "z": "500"}]
    spec, meshes, _ = _one_part({"id": "pipe", "type": "pipe", "shape": "sweep",
                                 "dims": [{"name": "r", "expr": "25"}], "path": path})
    b = meshes["pipe"].bounds()
    assert b[0] >= -26 and b[3] <= 326 and b[5] <= 526 and meshes["pipe"].tri_count > 0


@pytest.mark.parametrize("kind", FIXTURES)
def test_fixtures_stay_inside_envelope(kind):
    dims = [{"name": k, "expr": v} for k, v in
            (("x0", "0"), ("x1", "1500"), ("y0", "0"), ("y1", "800"), ("z0", "0"), ("z1", "700"))]
    spec, meshes, res = _one_part({"id": "fx", "type": kind, "shape": "fixture",
                                   "fixture": kind, "dims": dims})
    assert meshes["fx"].tri_count > 0
    assert next(r for r in res if r.id == "B-built-fx").status == "PASS"
