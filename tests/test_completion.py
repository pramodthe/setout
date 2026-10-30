"""Archetype completion: beyond-the-cut parts proposed, gated, built and confirmed."""
import copy
import json

import pytest

from app import completion, database as d, ontology
from app.agent_tools import Tools
from app.geometry import build_assembly, export_scene_json
from app.model_gateway import ModelGateway
from app.native_program import NativeProgram, prepare
from app.program_edits import apply_edits
from test_agent_v3 import FakeRenderer, response_json, source_repo  # noqa: F401
from test_api_v3 import client  # noqa: F401

READ = {"drawing_type": "section", "rotation_deg": 0, "title": "Door sill",
        "elements": [{"label_verbatim": "Waterproof membrane", "kind": "label", "where": "left", "category": "layer"},
                     {"label_verbatim": "Sill section", "kind": "label", "where": "centre", "category": "object"}],
        "dimensions": [{"value_mm": 100, "quote_verbatim": "100mm minimum step", "applies_to": "step"}]}
SOURCE = {"filename": "own.pdf", "sha256": "a" * 64, "page_index": 0, "crop_id": "p0_x",
          "page_width_pt": 300, "page_height_pt": 300, "rotation_deg": 0}


def _dims(**expressions):
    return [{"name": k, "expr": v} for k, v in expressions.items()]


def _param(name, value, provenance="assumption", **extra):
    return {"name": name, "value": value, "unit": "mm", "kind": "exact", "provenance": provenance, **extra}


def door_program():
    """A threshold section: only what the cut shows (slabs, membrane, sill)."""
    return {"version": "3.0", "title": "Door sill", "kind": "section", "situation": [], "assembly_class": "door_threshold",
            "params": [_param("step", 100, "drawing", source_quote="100mm minimum step", source_dimension_id="d0"),
                       _param("slab_t", 150), _param("inner", 300), _param("outer", 400), _param("sill_x0", -120),
                       _param("sill_depth", 110), _param("sill_h", 60), _param("memb_t", 2), _param("run", 900)],
            "parts": [
                {"id": "slab_in", "type": "slab", "label": None, "class_id": "slab", "shape": "box", "role": "substrate",
                 "dims": _dims(x0="0 - inner", x1="0", y0="0 - slab_t", y1="0", z0="0", z1="run"),
                 "install_after": [], "features": []},
                {"id": "slab_out", "type": "slab", "label": None, "class_id": "slab", "shape": "box", "role": "substrate",
                 "dims": _dims(x0="0", x1="outer", y0="0 - slab_t - step", y1="0 - step", z0="0", z1="run"),
                 "install_after": [], "features": [{"id": "step", "kind": "face_distance",
                     "selector": {"axis": "y", "face": "max", "other_part": "slab_in", "other_face": "max"},
                     "expected_expr": "0 - step", "source_dimension_id": "d0", "comparison": "max"}]},
                {"id": "membrane", "type": "membrane", "label": "Waterproof membrane", "class_id": "membrane", "shape": "box",
                 "role": "covered_layer", "dims": _dims(x0="0 - inner", x1="0", y0="0", y1="memb_t", z0="0", z1="run"),
                 "install_after": ["slab_in"], "features": []},
                {"id": "sill", "type": "door_sill", "label": "Sill section", "class_id": "door_sill", "shape": "cad",
                 "role": "object", "form": "shaped", "cad_brief": "aluminium sill extrusion",
                 "dims": _dims(x0="sill_x0", x1="sill_x0 + sill_depth", y0="memb_t", y1="memb_t + sill_h", z0="0", z1="run"),
                 "param_bindings": {"width": "sill_depth", "depth": "run", "height": "sill_h"},
                 "placement": {"x": "sill_x0", "y": "memb_t", "z": "run"}, "install_after": ["membrane"], "features": []}],
            "relations": [{"source": "membrane", "target": "slab_in", "kind": "covers", "constraints": {
                "axis": "y", "source_face": "min", "target_face": "max", "min_gap": 0, "max_gap": 0, "min_coverage": 1.0}}],
            "unresolved": []}


def _prepared(raw, base=None):
    return prepare(raw, SOURCE, "door", READ, base)


def _built(spec):
    """Meshes the way verification builds them: CAD parts stand in as boxes."""
    preflight = spec.model_copy(deep=True)
    for component in preflight.components:
        if component.generator.id == "cad_mesh":
            component.generator.id = "box"
    return build_assembly(preflight)


def test_completion_proposes_the_parts_beyond_the_cut_and_they_build():
    native, _, _ = _prepared(door_program())
    ont = ontology.load()
    result = completion.archetype_completion(native, ont, READ)
    assert result.ontology_version == ont.version and result.suggestions == []
    assert [(p.id, p.class_id) for p in result.parts] == [("jamb_1", "door_jamb"), ("jamb_2", "door_jamb"),
                                                           ("head", "door_head"), ("leaf", "door_leaf")]
    for part in result.parts:
        slot = part.inferred_by.rsplit(":", 1)[1]
        assert part.provenance == "inferred" and part.confidence is None and part.label is None
        assert part.inferred_by == f"archetype:door_threshold@{ont.version}:{slot}" and slot in ("jamb", "head", "leaf")
        assert part.role == "object" and part.shape == "cad" and part.form and part.param_bindings
    # Every size with no derivation is a named assumption that says so; the
    # jamb depth and the head height derive from parts already in the program.
    assert {p.name: p.value for p in result.params} == {"jamb_width": 18, "jamb_height": 2010, "leaf_thickness": 40}
    assert all(p.provenance.value == "assumption" and "no source" in p.note and "http" in p.note for p in result.params)
    assert {(r.source, r.target, r.kind) for r in result.relations} == {
        ("jamb_1", "sill", "supported_by"), ("jamb_2", "sill", "supported_by"), ("head", "jamb_1", "supported_by"),
        ("head", "jamb_2", "supported_by"), ("leaf", "jamb_1", "hosted_in"), ("leaf", "jamb_2", "hosted_in"), ("leaf", "head", "hosted_in")}
    applied = completion.apply(native.model_dump(mode="json"), result)
    native2, spec, notes = _prepared(applied, native.model_dump(mode="json"))
    assert notes == [] and [p.provenance for p in native2.parts][-4:] == ["inferred"] * 4
    meshes = _built(spec)
    sill = meshes["sill"].bounds()
    jamb_1, jamb_2, head, leaf = (meshes[k].bounds() for k in ("jamb_1", "jamb_2", "head", "leaf"))
    assert jamb_1[1] == jamb_2[1] == sill[4]  # jambs stand on the sill's top face
    assert (jamb_1[2], jamb_1[5]) == (sill[2], sill[2] + 18) and (jamb_2[2], jamb_2[5]) == (sill[5] - 18, sill[5])
    assert (jamb_1[0], jamb_1[3]) == (sill[0], sill[3])  # frame depth derived from the sill
    assert head[1] == jamb_1[4] and (head[2], head[5]) == (sill[2], sill[5])  # head spans the jambs
    assert (leaf[2], leaf[5]) == (jamb_1[5], jamb_2[2]) and leaf[4] == head[1] and leaf[3] - leaf[0] == 40
    scene = export_scene_json(spec, meshes)
    badges = {s["id"]: (s["class_id"], s["provenance"], s["confidence"], s["inferred_by"]) for s in scene["solids"]}
    assert badges["jamb_1"] == ("door_jamb", "inferred", None, f"archetype:door_threshold@{ont.version}:jamb")
    assert badges["sill"] == ("door_sill", "drawing", None, None) and scene["context"]["assembly_class"] == "door_threshold"
    # The same proposal as patch_program edits.
    patched = apply_edits(native.model_dump(mode="json"), completion.edits(result))
    assert len(_prepared(patched, native.model_dump(mode="json"))[0].parts) == 8


def test_completion_suggests_instead_of_guessing():
    ont = ontology.load()
    without_sill = door_program()
    without_sill["parts"] = [p for p in without_sill["parts"] if p["id"] != "sill"]
    native, _, _ = _prepared(without_sill)
    result = completion.archetype_completion(native, ont, READ)
    assert result.parts == [] and result.params == []
    reasons = {s.slot: s.reason for s in result.suggestions}
    assert set(reasons) == {"threshold", "jamb", "head", "leaf"}
    assert "anchor" in reasons["jamb"] and "Sill section" in reasons["threshold"]  # the drawing labels the sill
    unlabelled = dict(READ, elements=[READ["elements"][0]])
    native, _, _ = prepare(without_sill, SOURCE, "door", unlabelled)
    reasons = {s.slot: s.reason for s in completion.archetype_completion(native, ont, unlabelled).suggestions}
    assert "coordinates" in reasons["threshold"]  # a cut layer needs the drawing's coordinates
    # A labelled part is authored from the drawing, never inferred.
    labelled = dict(READ, elements=READ["elements"] + [{"label_verbatim": "Door jamb", "kind": "label", "where": "r", "category": "object"}])
    native, _, _ = prepare(door_program(), SOURCE, "door", labelled)
    result = completion.archetype_completion(native, ont, labelled)
    assert {s.slot for s in result.suggestions} == {"jamb", "head", "leaf"} and "Door jamb" in next(
        s.reason for s in result.suggestions if s.slot == "jamb")
    # Section visibility describes sections; other drawing types get suggestions only.
    result = completion.archetype_completion(native, ont, dict(READ, drawing_type="plan"))
    assert result.parts == [] and all("plan" in s.reason for s in result.suggestions)
    with pytest.raises(ValueError, match="assembly_class"):
        completion.archetype_completion(NativeProgram.model_validate({**native.model_dump(mode="json"), "assembly_class": None}), ont, READ)


def _inferred(ident, class_id, slot, **extra):
    version = ontology.load().version
    return {"id": ident, "type": class_id, "label": None, "class_id": class_id, "shape": "cad", "role": "object",
            "form": "shaped", "cad_brief": "placeholder", "provenance": "inferred",
            "inferred_by": f"archetype:door_threshold@{version}:{slot}",
            "dims": _dims(x0="sill_x0", x1="sill_x0 + sill_depth", y0="memb_t + sill_h", y1="memb_t + sill_h + run",
                          z0="0", z1="sill_depth"),
            "param_bindings": {"width": "sill_depth", "depth": "sill_depth", "height": "run"},
            "placement": {"x": "sill_x0", "y": "memb_t + sill_h", "z": "sill_depth"},
            "install_after": ["sill"], "features": [], **extra}


@pytest.mark.parametrize("change, message", [
    (lambda raw: raw["parts"].append(_inferred("ghost", "membrane", "membrane")), "required slot"),
    (lambda raw: raw["parts"].append(_inferred("ghost", "door_leaf", "jamb")), "takes class_id"),
    (lambda raw: raw["parts"].append(_inferred("ghost", "door_sill", "threshold")), "already filled"),
    (lambda raw: raw["parts"].append(_inferred("ghost", "door_jamb", "jamb", inferred_by="my guess")), "required slot"),
    (lambda raw: raw["parts"].append(_inferred("ghost", "door_jamb", "jamb", provenance="user_input")), "reviewer"),
    (lambda raw: raw["parts"].append(_inferred("ghost", "door_jamb", "jamb", provenance="drawing")), "inferred_by is set"),
    (lambda raw: (raw["parts"].append(_inferred("ghost", "door_jamb", "jamb")), raw.update(assembly_class=None)), "assembly_class"),
])
def test_invalid_inferred_claims_are_rejected_with_a_reason(change, message):
    raw = door_program()
    change(raw)
    with pytest.raises(ValueError, match=message):
        _prepared(raw)


def test_an_agent_cannot_relabel_or_confirm_inferred_parts():
    native, _, _ = _prepared(door_program())
    base = native.model_dump(mode="json")
    applied = completion.apply(base, completion.archetype_completion(native, ontology.load(), READ))
    inferred, _, _ = _prepared(applied, base)
    inferred = inferred.model_dump(mode="json")
    relabelled = copy.deepcopy(inferred)
    next(p for p in relabelled["parts"] if p["id"] == "leaf").update(provenance="drawing", inferred_by=None)
    with pytest.raises(ValueError, match="claim the drawing shows it"):
        _prepared(relabelled, inferred)
    confirmed = copy.deepcopy(inferred)
    next(p for p in confirmed["parts"] if p["id"] == "leaf").update(provenance="user_input")
    with pytest.raises(ValueError, match="only a reviewer"):
        _prepared(confirmed, inferred)
    # A reviewer can confirm; then an agent can neither drop nor demote the part.
    reviewed, _, _ = prepare(confirmed, SOURCE, "door", READ, inferred, human=True)
    reviewed = reviewed.model_dump(mode="json")
    assert next(p for p in reviewed["parts"] if p["id"] == "leaf")["provenance"] == "user_input"
    dropped = copy.deepcopy(reviewed)
    dropped["parts"] = [p for p in dropped["parts"] if p["id"] != "leaf"]
    with pytest.raises(ValueError, match="reviewer confirmed"):
        _prepared(dropped, reviewed)
    demoted = copy.deepcopy(reviewed)
    next(p for p in demoted["parts"] if p["id"] == "leaf")["provenance"] = "inferred"
    with pytest.raises(ValueError, match="reviewer confirmed"):
        _prepared(demoted, reviewed)
    # Adding a second inferred leaf while a confirmed one fills the slot is a duplicate.
    duplicate = copy.deepcopy(reviewed)
    duplicate["parts"].append(_inferred("leaf_2", "door_leaf", "leaf"))
    with pytest.raises(ValueError, match="already filled"):
        _prepared(duplicate, reviewed)


# ------------------------------------------------------------ agent tool

def _responder(payload):
    purpose = payload["text"]["format"]["name"]
    assert purpose == "read", purpose
    return response_json(json.loads(json.dumps(READ)))


def _draft(repo, monkeypatch, mode):
    monkeypatch.setenv("SETOUT_COMPLETION", mode)
    job = repo.enqueue("generate_detail", {"figure_id": "figure"})
    fence = repo.claim("completion-test", only_ids=[job["id"]])
    tools = Tools(repo, fence, ModelGateway(repo, fence, _responder))
    tools.run("read", "read_drawing", {})
    written = tools.run("write", "write_program", {"program": door_program()})
    assert written.get("version_id"), written
    return tools, written


def _scene_parts(repo, verification_id):
    report = repo.get(d.verifications, verification_id)
    scene = json.loads(repo.artifacts.read(report["quality"]["scene_artifact_id"]))
    return {s["id"]: s for s in scene["solids"]}


def test_complete_assembly_tool_suggests_and_the_gate_applies(source_repo, monkeypatch):
    tools, written = _draft(source_repo, monkeypatch, "suggest")
    proposal = tools.run("complete", "complete_assembly", {"version_id": written["version_id"]})
    assert proposal["mode"] == "suggest" and [p["id"] for p in proposal["parts"]] == ["jamb_1", "jamb_2", "head", "leaf"]
    assert proposal["suggestions"] == [] and proposal["edits"] and "patch_program" in proposal["note"]
    assert source_repo.run(tools.fence.job_id)["working_version_id"] == written["version_id"]  # nothing applied yet
    applied = tools.run("apply", "patch_program", {"edits": proposal["edits"]})
    assert applied.get("version_id") and applied["version_id"] != written["version_id"], applied
    parts = _scene_parts(source_repo, applied["verification_id"])
    assert parts["head"]["provenance"] == "inferred" and parts["head"]["inferred_by"].endswith(":head")
    assert parts["head"]["class_id"] == "door_head" and parts["head"]["confidence"] is None
    assert parts["sill"]["provenance"] == "drawing" and parts["sill"]["class_id"] == "door_sill"
    # Inferred objects are placeholders until modelled: never a PASS.
    assert any(c["id"] == "cad-leaf" and c["status"] == "FAIL" for c in applied["checks"])
    stored = source_repo.get(d.versions, applied["version_id"])["program"]
    assert stored["assembly_class"] == "door_threshold" and [p["provenance"] for p in stored["parts"]][-4:] == ["inferred"] * 4


def test_complete_assembly_tool_applies_in_mode_on_and_is_absent_when_off(source_repo, monkeypatch):
    tools, written = _draft(source_repo, monkeypatch, "on")
    applied = tools.run("complete", "complete_assembly", {"version_id": written["version_id"]})
    assert applied.get("version_id") and applied["version_id"] != written["version_id"], applied
    assert applied["completion"]["mode"] == "on" and [p["id"] for p in applied["completion"]["parts"]] == ["jamb_1", "jamb_2", "head", "leaf"]
    assert source_repo.run(tools.fence.job_id)["working_version_id"] == applied["version_id"]
    assert _scene_parts(source_repo, applied["verification_id"])["leaf"]["provenance"] == "inferred"
    again = tools.run("complete-again", "complete_assembly", {"version_id": applied["version_id"]})
    assert again["parts"] == [] and again["edits"] == []  # every required slot is now filled
    monkeypatch.setenv("SETOUT_COMPLETION", "off")
    assert "complete_assembly" not in {t["name"] for t in tools.definitions()}
    assert tools.run("off", "complete_assembly", {"version_id": applied["version_id"]})["error"]["code"] == "invalid_tool_input"


def test_critic_prompt_names_the_required_slots_and_the_retention_rule(source_repo, monkeypatch):
    prompts = []
    def responder(payload):
        purpose = payload["text"]["format"]["name"]
        if purpose == "read":
            return response_json(json.loads(json.dumps(READ)))
        prompts.append(payload["input"][0]["content"][0]["text"])
        return response_json({"verdict": "pass", "issues": []})
    monkeypatch.setenv("SETOUT_COMPLETION", "on")
    job = source_repo.enqueue("generate_detail", {"figure_id": "figure"})
    fence = source_repo.claim("critic-test", only_ids=[job["id"]])
    tools = Tools(source_repo, fence, ModelGateway(source_repo, fence, responder), renderer=FakeRenderer(source_repo))
    tools.run("read", "read_drawing", {})
    written = tools.run("write", "write_program", {"program": door_program()})
    applied = tools.run("complete", "complete_assembly", {"version_id": written["version_id"]})
    rendered = tools.run("render", "render", {"version_id": applied["version_id"]})
    verdict = tools.run("critic", "critic", rendered["critic_args"])
    assert verdict["verdict"] == "pass" and len(prompts) == 1
    text = prompts[0]
    assert "Assembly class door_threshold requires these slots" in text and "jamb (class door_jamb, min 2" in text
    assert "RETENTION" in text and "'not in the drawing' is never a reason" in text
    assert '"provenance":"inferred"' in text  # the critic sees which parts are placeholders


def test_reviewer_confirms_inferred_parts_through_the_review_api(client, source_repo, monkeypatch):
    tools, written = _draft(source_repo, monkeypatch, "on")
    applied = tools.run("complete", "complete_assembly", {"version_id": written["version_id"]})
    version_id = applied["version_id"]
    body = {"reviewer": "Expert", "action": "correct", "data": {"confirm_inferred": ["jamb_1", "jamb_2"]}}
    response = client.post(f"/api/versions/{version_id}/reviews", json=body)
    assert response.status_code == 200, response.text
    confirmed = source_repo.get(d.versions, response.json()["version_id"])
    assert confirmed["parent_version_id"] == version_id and confirmed["origin"] == "user-revised:v3"
    parts = {p["id"]: p for p in confirmed["program"]["parts"]}
    assert parts["jamb_1"]["provenance"] == parts["jamb_2"]["provenance"] == "user_input"
    assert parts["jamb_1"]["confidence"] is None and parts["jamb_1"]["inferred_by"].endswith(":jamb")
    assert parts["head"]["provenance"] == "inferred"  # untouched
    assert {c["id"]: c["provenance"] for c in confirmed["spec"]["components"]}["jamb_1"] == "user_input"
    lessons = [m for m in source_repo.list(d.memories) if m["source_version_id"] == confirmed["id"]]
    assert lessons and lessons[0]["state"] == "proposed" and "jamb_1" in lessons[0]["text"]
    # Stale version: the detail has moved on.
    assert client.post(f"/api/versions/{version_id}/reviews", json=body).status_code == 409
    bad = dict(body, data={"confirm_inferred": ["sill"]})
    assert client.post(f"/api/versions/{confirmed['id']}/reviews", json=bad).status_code == 422
    assert client.post(f"/api/versions/{confirmed['id']}/reviews", json=dict(body, data={})).status_code == 422
    scene = client.get(f"/api/versions/{version_id}/scene").json()
    assert scene["version_id"] == version_id and "quality" in scene
    assert {s["id"]: s["provenance"] for s in scene["solids"]}["head"] == "inferred"
