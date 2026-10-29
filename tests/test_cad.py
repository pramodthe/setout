"""CadQuery sandbox without a Modal account: static gate, STL parsing, and
archived v2 AI-modelled parts through build + verify."""

import json
import struct

import pytest

from app import cad_sandbox, store
from app.cad_sandbox import CadCodeError, check_code, parse_stl
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
    positions = [0, 0, 0, 300, 0, 0, 300, 120, 300]  # an archived v2 mesh, already fitted
    (tmp_path / "library" / "cad" / "t").mkdir(parents=True)
    (tmp_path / "library" / "cad" / "t" / "grate.json").write_text(
        json.dumps({"positions": positions}))
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
