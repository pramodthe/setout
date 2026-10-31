"""Harness output for the hand-authored floor-waste script. Needs Blender 4.2."""
import json
import os
from pathlib import Path

import pytest

from app.make.floor_waste_script import SCRIPT
from app.make.helpers import mm
from app.make.worker import resolve_blender, run
from eval.make.muse_bpy_eval import score_build


def _blender():
    binary = resolve_blender()
    if not binary:
        pytest.skip("Blender 4.2 is not installed")
    return binary


def _meshes(tree):
    found = []

    def visit(node):
        if node.get("type") == "mesh":
            found.append(node)
        for child in node.get("children") or []:
            visit(child)

    visit(tree)
    return found


def _collections(tree):
    names = []

    def visit(node):
        if node.get("type") == "collection":
            names.append(node["name"])
        for child in node.get("children") or []:
            visit(child)

    visit(tree)
    return names


def test_millimetre_helper():
    assert mm(1000) == 1.0
    assert mm(1.5) == pytest.approx(0.0015)


def test_overrides_are_reapplied_after_the_script(monkeypatch):
    binary = _blender()
    secret = "sk-testsecretvalue999"
    monkeypatch.setenv("LLM_API_KEY", secret)
    result = run(
        SCRIPT,
        {
            "Upturn": {"dims": {"upturn_h": 150}},
            "Sealant": {"location": [5, 6, 7], "rotation_deg": [0, 0, 90]},
        },
        timeout=60,
        preview=False,
        blender_bin=binary,
    )
    assert result["ok"], result["traceback"] + "\n" + result["stdout_tail"]
    assert secret not in result["stdout_tail"]
    assert secret not in result["traceback"]
    assert "LLM_API_KEY" not in result["secret_env_present"]
    tree = json.loads(result["files"]["tree.json"])
    assert tree["name"] == "FloorWasteJunction"
    assert tree["type"] == "collection"
    names = {node["name"] for node in _meshes(tree)}
    assert names >= {"Substrate", "Screed", "Membrane", "Upturn", "Stainless Angle", "Tile", "Sealant"}
    assert {"Structure", "Waterproofing", "Finishes"} <= set(_collections(tree))
    upturn = next(node for node in _meshes(tree) if node["name"] == "Upturn")
    height = upturn["bbox_mm"]["max"][2] - upturn["bbox_mm"]["min"][2]
    assert height == pytest.approx(150, abs=0.5)
    assert upturn["dims"]["upturn_h"] == 150
    sealant = next(node for node in _meshes(tree) if node["name"] == "Sealant")
    assert sealant["location"] == pytest.approx([5, 6, 7], abs=0.05)
    assert sealant["rotation_deg"][2] == pytest.approx(90, abs=1.0)
    assert result["files"]["scene.glb"][:4] == b"glTF"
    assert b"Substrate" in result["files"]["scene.glb"]


def test_dimension_override_scales_when_the_script_ignores_it():
    """A height edit still resizes the mesh when the script never reads OVERRIDES."""
    binary = _blender()
    script = (
        'box_mm("Upturn", (2, 400, 100), (90, 0, 150), collection="Waterproofing", '
        'dims={"upturn_hgt": 100, "floor_width": 400}, material="membrane")\n'
    )
    result = run(
        script,
        {"Upturn": {"dims": {"upturn_hgt": 160}}},
        timeout=60,
        preview=False,
        blender_bin=binary,
    )
    assert result["ok"], result["traceback"] + "\n" + result["stdout_tail"]
    tree = json.loads(result["files"]["tree.json"])
    upturn = next(node for node in _meshes(tree) if node["name"] == "Upturn")
    span = upturn["bbox_mm"]["max"][2] - upturn["bbox_mm"]["min"][2]
    assert span == pytest.approx(160, abs=0.5)
    assert upturn["dims"]["upturn_hgt"] == 160
    depth = upturn["bbox_mm"]["max"][1] - upturn["bbox_mm"]["min"][1]
    assert depth == pytest.approx(400, abs=0.5)


def test_infinite_loop_inside_blender_is_killed():
    binary = _blender()
    result = run(
        "print('LOOP_START', flush=True)\nwhile True:\n    pass\n",
        timeout=8,
        preview=False,
        blender_bin=binary,
    )
    assert result["ok"] is False
    assert result["files"] == {}
    assert "killed at timeout" in result["traceback"]
    assert "LOOP_START" in result["stdout_tail"]


def test_reference_script_writes_preview(monkeypatch):
    binary = _blender()
    monkeypatch.setenv("LLM_API_KEY", "sk-testsecretvalue999")
    result = run(SCRIPT, {}, timeout=180, preview=True, blender_bin=binary)
    assert result["ok"], result["traceback"] + "\n" + result["stdout_tail"]
    assert result["engine"].startswith("BLENDER_EEVEE")
    png = result["files"]["preview.png"]
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    scored = score_build(result)
    assert scored["pass"] is True
    assert scored["objects"] >= 5
    proof = os.environ.get("SETOUT_MAKE_PROOF_DIR")
    if proof:
        dest = Path(proof)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "preview.png").write_bytes(png)
        (dest / "scene.glb").write_bytes(result["files"]["scene.glb"])
        (dest / "tree.json").write_bytes(result["files"]["tree.json"])
