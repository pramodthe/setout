"""CadQuery path without a Modal account: static gate, STL parsing, axis
mapping, envelope fitting, and an AI-modelled part through build + verify
using a stand-in runner (the real runner executes in a Modal sandbox)."""

import base64
import json
import struct

import pytest

from app import cad_sandbox, store
from app.cad_sandbox import CadCodeError, check_code, fit_to_envelope, parse_stl, run_cad
from app.geometry import build_assembly
from app.program import DetailProgram, compile_program
from app.spec import SourceDoc
from app.verify import summarize, verify_all

GOOD = """
import cadquery as cq
import math

def build(W, H, D):
    return cq.Workplane("XY").box(W, D, H).edges("|Z").fillet(min(W, D) / 10)
"""


@pytest.mark.parametrize("bad, why", [
    ("import os\ndef build(W, H, D):\n    return 1", "import"),
    ("import cadquery as cq\ndef build(W, H, D):\n    return open('/etc/passwd')", "open"),
    ("def build(W, H, D):\n    return ().__class__", "dunder"),
    ("def build(W, H, D):\n    exec('1')", "exec"),
    ("from subprocess import run\ndef build(W, H, D):\n    return 1", "subprocess"),
    ("import cadquery as cq\ndef make(W, H, D):\n    return 1", "build"),
    ("def build(W, H, D)\n    return 1", "syntax"),
])
def test_static_gate_rejects(bad, why):
    with pytest.raises(CadCodeError):
        check_code(bad)


def test_static_gate_accepts_plain_cadquery():
    check_code(GOOD)


def _binary_stl(tris: list[float]) -> bytes:
    n = len(tris) // 9
    out = bytearray(b"\0" * 80) + struct.pack("<I", n)
    for i in range(n):
        out += struct.pack("<3f", 0, 0, 0) + struct.pack("<9f", *tris[i * 9:i * 9 + 9]) + b"\0\0"
    return bytes(out)


TRI = [0, 0, 0, 10, 0, 0, 0, 20, 30]  # CadQuery coords: X, Y (depth), Z (up)


def test_parse_stl_binary_and_ascii():
    assert parse_stl(_binary_stl(TRI)) == pytest.approx(TRI)
    ascii_stl = ("solid t\nfacet normal 0 0 0\nouter loop\n"
                 "vertex 0 0 0\nvertex 10 0 0\nvertex 0 20 30\n"
                 "endloop\nendfacet\nendsolid t\n").encode()
    assert parse_stl(ascii_stl) == pytest.approx(TRI)


def test_axes_are_a_rotation_not_a_mirror():
    # x = X, y = Z (up), z = -Y: the front (Y=0) faces the viewer (max z)
    f = fit_to_envelope(TRI, (0, 100, 0, 100, 0, 100))
    pts = [f.positions[i:i + 3] for i in range(0, 9, 3)]
    ys = [p[1] for p in pts]
    assert max(ys) - min(ys) == pytest.approx(30)  # CadQuery Z became y
    zs = [p[2] for p in pts]
    assert zs[0] > zs[2]  # Y=0 vertex is in front of the Y=20 vertex
    # orientation: signed volume sign of (a, b, c, origin) flips only under a mirror
    def det(a, b, c):
        return (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0])
                + a[2] * (b[0] * c[1] - b[1] * c[0]))
    src = [TRI[0:3], TRI[3:6], TRI[6:9]]
    mapped = [[p[0], p[2], -p[1]] for p in src]
    assert det(*src) * det(*mapped) >= 0


def test_fit_shrinks_oversized_models_and_centres_them():
    big = [0, 0, 0, 400, 0, 0, 0, 50, 100]  # 400 wide into a 200-wide envelope
    f = fit_to_envelope(big, (1000, 1200, 0, 300, 0, 300))
    xs, ys = f.positions[0::3], f.positions[1::3]
    assert f.scale == pytest.approx(0.5)
    assert min(xs) >= 1000 - 1e-9 and max(xs) <= 1200 + 1e-9
    assert min(ys) == pytest.approx(0)  # stands on the envelope floor


def test_run_cad_with_stand_in_runner_never_executes_locally():
    seen = {}

    def runner(script: str) -> str:
        seen["script"] = script
        payload = {"bbox": [0, 0, 0, 10, 20, 30],
                   "stl": base64.b64encode(_binary_stl(TRI)).decode()}
        return "noise\nRESULT " + json.dumps(payload)

    f = run_cad(GOOD, (0, 100, 0, 100, 0, 100), runner)
    assert "def build(W, H, D)" in seen["script"] and "W, H, D = 100.0" in seen["script"]
    assert len(f.positions) == 9


def test_run_cad_rejects_bad_code_before_the_runner():
    def runner(script: str) -> str:  # must never be called
        raise AssertionError("bad code reached the sandbox")

    with pytest.raises(CadCodeError):
        run_cad("import socket\ndef build(W, H, D):\n    return 1", (0, 1, 0, 1, 0, 1), runner)


def test_ai_modelled_part_builds_and_is_flagged_illustrative(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "VAR", tmp_path)
    src = SourceDoc(filename="t.pdf", sha256="0" * 64, page_index=0, crop_id="p0_x",
                    page_width_pt=595, page_height_pt=842)
    env = [("x0", "0"), ("x1", "300"), ("y0", "0"), ("y1", "120"), ("z0", "0"), ("z1", "300")]
    prog = DetailProgram(
        detail_id="t", title="t", origin="test", source=src,
        params=[{"name": "a", "value": 1, "provenance": "assumption"}],
        parts=[{"id": "slab", "type": "floor_slab", "shape": "box",
                "dims": [{"name": k, "expr": v} for k, v in env]},
               {"id": "grate", "type": "floor_grate", "shape": "cad",
                "cad_brief": "square slotted floor grate",
                "dims": [{"name": k, "expr": v} for k, v in env]}])
    spec = compile_program(prog)
    fitted = fit_to_envelope(TRI, (0, 300, 0, 120, 0, 300))
    (tmp_path / "library" / "cad" / "t").mkdir(parents=True)
    (tmp_path / "library" / "cad" / "t" / "grate.json").write_text(
        json.dumps({"positions": fitted.positions}))
    spec.context["cad_parts"] = {"grate": {"file": "cad/t/grate.json", "scale": 1,
                                           "code_sha": "abc"}}
    meshes = build_assembly(spec)
    results = verify_all(spec, meshes, None)
    by = {r.id: r for r in results}
    assert by["B-built-grate"].status == "PASS"
    assert by["C-cad-grate"].status == "N/A"
    assert "AI-modelled" in spec.component("grate").note
    assert summarize(results)["failed"] == ["C-labels"]  # no drawing labels in this toy


def test_cad_part_requires_a_brief():
    src = SourceDoc(filename="t.pdf", sha256="0" * 64, page_index=0, crop_id="p0_x",
                    page_width_pt=595, page_height_pt=842)
    with pytest.raises(ValueError):
        DetailProgram(detail_id="t", title="t", origin="t", source=src,
                      parts=[{"id": "g", "type": "x", "shape": "cad",
                              "dims": [{"name": k, "expr": "1"} for k in
                                       ("x0", "x1", "y0", "y1", "z0", "z1")]}])


def test_available_is_false_without_modal(monkeypatch):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "modal":
            raise ImportError
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    assert cad_sandbox.available() is False


def test_remodel_part_swaps_in_cad_geometry(tmp_path, monkeypatch):
    """On-request re-model: stubbed Muse + stubbed sandbox, real pipeline."""
    from app import generate as gen
    from app.generate import EXAMPLE, gate

    monkeypatch.setattr(store, "VAR", tmp_path)
    read = {"drawing_type": "isometric_room", "rotation_deg": 0, "title": "",
            "elements": [], "dimensions": [{"value_mm": 150, "quote_verbatim": "150mm",
                                            "applies_to": "upturn"}]}
    raw, _ = gate(EXAMPLE, read)
    src = SourceDoc(filename="t.pdf", sha256="f" * 64, page_index=0, crop_id="p0_x",
                    page_width_pt=595, page_height_pt=842)
    prog = DetailProgram(
        detail_id="ai-x", title="t", origin="ai-generated:test", kind="room", source=src,
        params=[{**q, "source_quote": q["source_quote"] or None, "note": q["note"] or None}
                for q in raw["params"]],
        parts=[{**p, "note": p["note"] or None, "fixture": p.get("fixture") or None,
                "cad_brief": None, "path": [{**pt, "z": pt.get("z") or None} for pt in p["path"]]}
               for p in raw["parts"]])
    spec = compile_program(prog)
    lib = tmp_path / "library"
    lib.mkdir()
    (lib / "ai-x.json").write_text(json.dumps({
        "program": prog.model_dump(mode="json"), "spec": spec.model_dump(mode="json")}))
    fig = tmp_path / "fig.png"
    fig.write_bytes(b"\x89PNG fake")
    monkeypatch.setattr(gen, "find_document_by_sha", lambda sha: {"id": "d"})
    monkeypatch.setattr(gen, "figure_path", lambda d, c: fig)
    monkeypatch.setattr(gen.meta_client, "respond_json",
                        lambda *a, **k: ({"code": GOOD, "summary": "bath"}, None))

    def runner(script: str) -> str:
        return "RESULT " + json.dumps({"bbox": [], "stl": base64.b64encode(_binary_stl(TRI)).decode()})

    out = gen.remodel_part("ai-x", "bath", cad_runner=runner)
    assert out["triangles"] == 1
    new = gen.load_library_spec("ai-x")
    bath = new.component("bath")
    assert bath.generator.id == "cad_mesh" and "AI-modelled" in bath.note
    meshes = build_assembly(new)
    res = {r.id: r for r in verify_all(new, meshes, None)}
    assert res["B-built-bath"].status == "PASS" and res["C-cad-bath"].status == "N/A"


def test_cad_mesh_path_must_stay_in_library(tmp_path, monkeypatch):
    """#6: a tampered record can't make load_cad_mesh read outside var/library."""
    from app.geometry import load_cad_mesh
    monkeypatch.setattr(store, "VAR", tmp_path)
    (tmp_path / "library").mkdir()
    (tmp_path / "secret.json").write_text(json.dumps({"positions": [0] * 9}))
    src = SourceDoc(filename="t.pdf", sha256="0" * 64, page_index=0, crop_id="p0_x",
                    page_width_pt=595, page_height_pt=842)
    env = [{"name": k, "expr": "1"} for k in ("x0", "x1", "y0", "y1", "z0", "z1")]
    spec = compile_program(DetailProgram(
        detail_id="t", title="t", origin="t", source=src,
        parts=[{"id": "g", "type": "x", "shape": "cad", "cad_brief": "b", "dims": env}]))
    for bad in ("../secret.json", "/etc/hosts", "cad/../../secret.json"):
        spec.context["cad_parts"] = {"g": {"file": bad}}
        with pytest.raises(ValueError):
            load_cad_mesh(spec, spec.component("g"))


def test_figure_lookup_rejects_bad_ids_and_creates_nothing(tmp_path, monkeypatch):
    from app.store import figure_path
    monkeypatch.setattr(store, "VAR", tmp_path)
    for doc, crop in (("..", "p0_x"), ("abc", "p0_x"), ("0123456789ab", "../x"),
                      ("0123456789ab", "p0_..")):
        assert figure_path(doc, crop) is None
    assert not (tmp_path / "docs").exists()
