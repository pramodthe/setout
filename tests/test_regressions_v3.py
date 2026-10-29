"""Bugs found reviewing PR #14: each test reproduces one, then guards the fix."""
import copy
import io
import json
import threading
import urllib.error

import pytest
from PIL import Image

from app import database as d, review_v3, store
from app.agent_tools import Tools
from app.model_gateway import ModelGateway
from app.worker import Worker
from test_agent_v3 import READ, FakeRenderer, ScriptedModel, program, source_repo  # noqa: F401
from test_api_v3 import client  # noqa: F401


def _generated(repo):
    job = repo.enqueue("generate_detail", {"figure_id": "figure"})
    Worker(repo, responder=ScriptedModel(), renderer=FakeRenderer(repo)).run_once(only_ids=[job["id"]])
    return repo.get(d.versions, repo.run(job["id"])["working_version_id"])


def _with_cad_object(repo, base):
    """A successor of `base` with one bound CAD object (bindings set by hand)."""
    raw = copy.deepcopy(base["program"])
    dims = [{"name": n, "expr": e} for n, e in (("x0", "0"), ("x1", "width"), ("y0", "height"),
                                                  ("y1", "height + depth"), ("z0", "0"), ("z1", "depth"))]
    raw["parts"].append({"id": "bracket", "type": "bracket", "label": None, "shape": "cad", "role": "object",
                         "form": "shaped", "cad_brief": "fixing bracket", "param_bindings": {"size": "width"},
                         "dims": dims, "placement": {"x": "0", "y": "height", "z": "0"},
                         "install_after": ["wall"], "features": [], "path": [], "note": None})
    read = repo.get(d.reads, base["drawing_read_id"])["response"]
    from app.native_program import prepare
    native, spec, _ = prepare(raw, base["program"]["source"], base["detail_id"], read, base["program"])
    with d.transaction(repo.engine) as con:
        made = repo.insert_version(con, detail_id=base["detail_id"], expected_parent=base["id"],
                                   program=native.model_dump(mode="json"), spec=spec.model_dump(mode="json"),
                                   bindings={"bracket": {"component_id": "c1", "build_id": "b1"}},
                                   figure_id=base["figure_id"], read_id=base["drawing_read_id"])
    return repo.get(d.versions, made["version_id"])


# 1. A reviewer's value edit must not drop CAD objects it doesn't touch.
def test_review_edit_keeps_cad_bindings_it_does_not_touch(source_repo):
    version = _with_cad_object(source_repo, _generated(source_repo))
    edited = review_v3.apply(source_repo, version, [{"name": "height", "value": 160}], [], [], reviewer="R")
    assert source_repo.get(d.versions, edited["version_id"])["bindings"] == {"bracket": {"component_id": "c1", "build_id": "b1"}}
    current = source_repo.get(d.versions, edited["version_id"])
    edited = review_v3.apply(source_repo, current, [{"name": "width", "value": 120}], [], [], reviewer="R")
    assert source_repo.get(d.versions, edited["version_id"])["bindings"] == {}  # the bracket reads width


def test_api_correction_keeps_cad_bindings_it_does_not_touch(client, source_repo):
    version = _with_cad_object(source_repo, _generated(source_repo))
    changed = copy.deepcopy(version["program"])
    next(p for p in changed["params"] if p["name"] == "height")["value"] = 170
    response = client.post(f"/api/versions/{version['id']}/reviews",
                           json={"reviewer": "R", "action": "correct", "data": {"program": changed}})
    assert response.status_code == 200, response.text
    assert source_repo.get(d.versions, response.json()["version_id"])["bindings"]["bracket"]["build_id"] == "b1"


# 3. SAM regions must reach the scene, and survive later versions.
def test_located_regions_reach_the_scene_and_survive_edits(client, source_repo):
    version = _generated(source_repo)
    region = {"x0": .1, "y0": .2, "x1": .3, "y1": .4, "c": .9, "prompt": "Wall"}
    with d.transaction(source_repo.engine) as con:
        con.execute(d.sam_regions.insert().values(id="r1", version_id=version["id"], part_key="wall", data=region))
    scene = client.get(f"/api/versions/{version['id']}/scene").json()
    assert scene["context"]["drawing_regions"] == {"wall": region}
    edited = review_v3.apply(source_repo, version, [{"name": "width", "value": 120}], [], [], reviewer="R")
    scene = client.get(f"/api/specs/{version['detail_id']}/scene").json()
    assert scene["version_id"] == edited["version_id"] and scene["context"]["drawing_regions"] == {"wall": region}
    card = next(e for e in client.get("/api/library").json()["details"] if e["detail_id"] == version["detail_id"])
    assert card["located"] == 1


# 8. A revise/CAD/locate job whose tool failed is reported as failed.
def test_failed_tool_jobs_are_not_reported_done(client, source_repo):
    version = _generated(source_repo)
    job = source_repo.enqueue("locate", {"version_id": version["id"]})
    fence = source_repo.claim("w", only_ids=[job["id"]])
    source_repo.end(fence, "needs_review", {"error": {"code": "invalid_tool_input", "message": "SAM is unavailable"},
                                            "version_id": version["id"]})
    shown = client.get(f"/api/generate/{job['id']}").json()
    assert shown["state"] == "failed" and "SAM is unavailable" in shown["error"]
    job = source_repo.enqueue("revise_detail", {"detail_id": version["detail_id"], "base_version_id": version["id"],
                                                "requested_changes": "x"})
    fence = source_repo.claim("w", only_ids=[job["id"]])
    source_repo.end(fence, "needs_review", {"version_id": version["id"], "open_issues": ["budget"]})
    shown = client.get(f"/api/generate/{job['id']}").json()
    assert shown["state"] == "failed" and "budget" in shown["error"]  # nothing new was made


# 13. Publishing shows on the library card.
def test_published_detail_is_reviewed_on_its_card(client, source_repo):
    version = _generated(source_repo)
    with d.transaction(source_repo.engine) as con:
        con.execute(d.details.update().where(d.details.c.id == version["detail_id"]).values(
                    published_version_id=version["id"]))
    card = next(e for e in client.get("/api/library").json()["details"] if e["detail_id"] == version["detail_id"])
    assert card["review"] == "reviewed"


# 17. Generating the same drawing again revises its detail instead of adding another.
def test_regenerating_a_drawing_targets_its_existing_detail(client, source_repo, tmp_path, monkeypatch):
    from app import main
    monkeypatch.setattr(store, "VAR", tmp_path)
    pdf = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(pdf, "PDF")
    doc = client.post("/api/documents", files={"file": ("own.pdf", pdf.getvalue(), "application/pdf")}).json()
    crop = doc["figures"][0]["crop_id"]
    first = client.post("/api/generate", json={"doc_id": doc["id"], "crop_id": crop}).json()
    detail_id = main.ai_detail_id(doc["sha256"], crop)
    assert source_repo.get(d.jobs, first["id"])["payload"]["detail_id"] == detail_id
    version = _generated(source_repo)  # stands in for the first run's draft
    with d.transaction(source_repo.engine) as con:
        made = source_repo.insert_version(con, detail_id=detail_id, expected_parent=None, program=version["program"],
                                          spec=version["spec"], figure_id=source_repo.get(d.jobs, first["id"])["payload"]["figure_id"],
                                          read_id=version["drawing_read_id"])
    again = client.post("/api/generate", json={"doc_id": doc["id"], "crop_id": crop}).json()
    payload = source_repo.get(d.jobs, again["id"])["payload"]
    assert payload["detail_id"] == detail_id and payload["expected_current_version_id"] == made["version_id"]


# 2. One unloadable old record must not hide the rest of the library.
def test_one_bad_library_record_does_not_block_the_others(tmp_path, monkeypatch):
    from app.program import DetailProgram, compile_program
    from app.repository import Repository
    from app.versioning import import_library
    monkeypatch.setattr(store, "VAR", tmp_path)
    library = tmp_path / "library"
    library.mkdir()
    raw = program()
    raw.update(version="2.0", detail_id="ai-good", origin="legacy test", source={"filename": "own.pdf", "sha256": "a" * 64,
               "page_index": 0, "crop_id": "p0_x", "page_width_pt": 300, "page_height_pt": 300})
    parsed = DetailProgram.model_validate(raw)
    (library / "ai-good.json").write_text(json.dumps({"program": parsed.model_dump(mode="json"),
                                                       "spec": compile_program(parsed).model_dump(mode="json")}))
    (library / "ai-bad.json").write_text(json.dumps({"spec": {"detail_id": "ai-bad", "components": "broken"}}))
    repo = Repository("sqlite:///" + str(tmp_path / "v3.db"), tmp_path / "artifacts")
    import_library(repo)
    assert repo.get(d.details, "ai-good")["current_version_id"]


# 16. An archived detail with a figure but no saved read still shows its drawing.
def test_drawing_of_archived_detail_without_a_read(client, source_repo):
    version = _generated(source_repo)
    with d.transaction(source_repo.engine) as con:
        source_repo.insert_version(con, detail_id="ai-archived", expected_parent=None, program={}, spec=version["spec"],
                                   figure_id="figure", format_version="legacy-v2")
    response = client.get("/api/specs/ai-archived/drawing")
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"


# 4. Patches to a rejected first proposal build on each other.
def test_patches_to_a_rejected_proposal_converge(source_repo):
    job = source_repo.enqueue("generate_detail", {"figure_id": "figure"})
    fence = source_repo.claim("w", only_ids=[job["id"]])
    tools = Tools(source_repo, fence, ModelGateway(source_repo, fence, ScriptedModel()))
    tools.run("read", "read_drawing", {})
    candidate = program()
    candidate["parts"].append({**copy.deepcopy(candidate["parts"][0]), "id": "lining", "type": "lining",
                               "label": None, "role": "covered_layer", "features": []})
    candidate["relations"] = [{"source": "lining", "target": "wall", "kind": "covers", "constraints": {
        "axis": "x", "source_face": "min", "target_face": "max", "min_gap": 0, "max_gap": 1, "min_coverage": 90}}]
    candidate["parts"][0]["role"] = "object"  # error A: an object must be CAD with a form
    first = tools.run("w1", "write_program", {"program": candidate})
    assert "objects require parametric CAD" in first["error"]["message"]
    second = tools.run("p1", "patch_program", {"edits": [{"op": "replace", "path": "/parts/@wall/role", "value": "substrate"}]})
    assert "min_coverage" in second["error"]["message"]  # error B
    third = tools.run("p2", "patch_program", {"edits": [{"op": "replace", "path": "/relations/0/constraints/min_coverage", "value": 0.9}]})
    assert third.get("version_id"), third


# 5. An agent can't silently overwrite or drop a reviewer's value.
def test_agent_cannot_revert_a_reviewers_value(source_repo):
    version = _generated(source_repo)
    corrected = review_v3.apply(source_repo, version, [{"name": "width", "value": 120}], [], [], reviewer="R")
    job = source_repo.enqueue("revise_detail", {"detail_id": version["detail_id"], "base_version_id": corrected["version_id"],
                                                "requested_changes": "x"})
    fence = source_repo.claim("w", only_ids=[job["id"]])
    tools = Tools(source_repo, fence, ModelGateway(source_repo, fence, ScriptedModel()))
    for edits in ([{"op": "replace", "path": "/params/@width/value", "value": 100},
                   {"op": "replace", "path": "/params/@width/provenance", "value": "assumption"}],
                  [{"op": "replace", "path": "/params/@width/provenance", "value": "assumption"}]):
        result = tools.run("p" + str(len(edits)), "patch_program", {"edits": edits})
        assert "reviewer" in result["error"]["message"], result


# 10. The embedded worker survives errors around a job.
def test_worker_thread_survives_claim_and_end_errors(source_repo, monkeypatch):
    worker = Worker(source_repo)
    calls = {"n": 0}
    def flaky_claim(owner, only_ids=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is locked")
        worker.stop.set()
        return None
    monkeypatch.setattr(source_repo, "claim", flaky_claim)
    thread = threading.Thread(target=worker.serve)
    thread.start()
    thread.join(5)
    assert not thread.is_alive() and calls["n"] == 2


# 9. A provider error response releases the reservation; a 400 is not retried.
@pytest.mark.parametrize("code,retryable", [(400, False), (429, True), (503, True)])
def test_provider_errors_release_their_reservation(source_repo, code, retryable):
    from app.model_gateway import ProviderRejected
    job = source_repo.enqueue("generate_detail", {"figure_id": "figure"})
    fence = source_repo.claim("w", only_ids=[job["id"]])
    def refuse(payload):
        raise urllib.error.HTTPError("https://api", code, "no", {}, io.BytesIO(b'{"error":"bad"}'))
    gateway = ModelGateway(source_repo, fence, refuse)
    with pytest.raises(urllib.error.HTTPError if retryable else ProviderRejected):
        gateway.call({"model": "m", "input": [], "max_output_tokens": 4096}, "agent", "turn-0")
    run = source_repo.run(job["id"])
    assert run["reserved"].get("tokens", 0) == 0 and run["consumed"].get("tokens", 0) == 0


def test_rejected_request_ends_the_job_for_review_not_retry(source_repo):
    job = source_repo.enqueue("generate_detail", {"figure_id": "figure"})
    def refuse(payload):
        raise urllib.error.HTTPError("https://api", 400, "bad request", {}, io.BytesIO(b'{"error":"schema"}'))
    Worker(source_repo, responder=refuse).run_once()
    final = source_repo.get(d.jobs, job["id"])
    assert final["state"] == "needs_review" and final["error"]["code"] == "provider_rejected"
