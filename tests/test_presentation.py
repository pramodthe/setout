"""Rendering is optional, independent of checks, and cannot replace newer geometry."""
import json
import struct

import pytest
from fastapi.testclient import TestClient

from app import database as d, presentation, studio
from app.repository import LostLease, Repository, uid
from app.worker import Worker
from app.main import app


def glb(doc=None):
    data = json.dumps(doc or {"asset": {"version": "2.0"}}).encode()
    data += b" " * (-len(data) % 4)
    return struct.pack("<4sIIII", b"glTF", 2, 20 + len(data), len(data), 0x4E4F534A) + data


@pytest.fixture
def repo(tmp_path, monkeypatch):
    repo = Repository("sqlite:///" + str(tmp_path / "db.sqlite"), tmp_path / "artifacts")
    monkeypatch.setenv("SETOUT_RENDERER", "local")
    monkeypatch.setattr(studio, "repository", lambda: repo)
    monkeypatch.setattr(studio, "ensure_worker", lambda: None)
    monkeypatch.setattr(presentation, "render_assets", lambda _: {
        "model.glb": glb({"asset": {"version": "2.0", "generator": "finished"}}),
        "render.jpg": b"rendered-image", "render.json": b'{"seconds": 12}',
    })
    return repo


def scene(repo):
    ident = uid()
    model = studio._keep(repo, glb(), "model/gltf-binary")
    with d.transaction(repo.engine) as con:
        con.execute(d.studio_scenes.insert().values(id=ident, state="working", title="A detail", steps=["model code"],
            model_artifact_id=model, parts=[{"name": "part"}], checks={"last": {"passed": False}}, tokens=42))
    studio._set(repo, ident, state="ready")
    return studio.get(ident)


def finish(repo, made):
    assert Worker(repo, kinds=("studio_render",)).run_once(only_ids=[made["presentation"]["job_id"]])
    return studio.get(made["id"])


def test_finishing_is_opt_in(repo, monkeypatch):
    monkeypatch.delenv("SETOUT_RENDERER")
    made = scene(repo)
    assert made["presentation"] is None
    assert not repo.list(d.jobs)


def test_ready_model_stays_usable_then_gains_surfaces_and_a_render(repo):
    made = scene(repo)
    assert made["state"] == "ready" and made["presentation"]["state"] == "queued"
    assert studio.file_of(made["id"], "model.glb")[0] == glb()
    held = finish(repo, made)
    assert held["presentation"]["state"] == "succeeded"
    assert studio.file_of(made["id"], "render") == (b"rendered-image", "image/jpeg")
    assert studio.file_of(made["id"], "surface.glb")[0] != glb()
    assert studio.file_of(made["id"], "model.glb")[0] == glb()
    assert {k: held[k] for k in ("checks", "parts", "tokens", "model_artifact_id")} == {k: made[k] for k in ("checks", "parts", "tokens", "model_artifact_id")}
    studio._set(repo, made["id"], state="ready")
    assert len(repo.list(d.jobs)) == 1  # repeated publish/restart reuses the render
    assert studio.get(made["id"])["presentation"]["state"] == "succeeded"


def test_app_can_enhance_an_existing_model_without_an_ai_call(repo, monkeypatch):
    monkeypatch.setenv("SETOUT_RENDERER", "off")
    made = scene(repo)
    assert not made["can_render"] and made["presentation"] is None
    monkeypatch.setenv("SETOUT_RENDERER", "local")
    monkeypatch.setattr(studio.meta_client, "configured", lambda: False)
    client = TestClient(app)
    first = client.post(f"/api/studio/{made['id']}/presentation")
    assert first.status_code == 202
    queued = first.json()
    assert queued["can_render"] and queued["state"] == "ready"
    assert queued["presentation"]["state"] == "queued"
    second = client.post(f"/api/studio/{made['id']}/presentation")
    assert second.json()["presentation"]["job_id"] == queued["presentation"]["job_id"]
    assert len(repo.list(d.jobs)) == 1
    done = finish(repo, queued)
    assert client.get(f"/api/studio/{made['id']}/render").content == b"rendered-image"
    assert done["checks"] == made["checks"] and done["tokens"] == made["tokens"]
    again = client.post(f"/api/studio/{made['id']}/presentation")
    assert again.json()["presentation"]["state"] == "succeeded" and len(repo.list(d.jobs)) == 1


def test_app_retries_a_failed_enhancement_without_rebuilding(repo, monkeypatch):
    made = scene(repo)
    fence = repo.claim("failed-render", only_ids=[made["presentation"]["job_id"]])
    repo.end(fence, "failed", error={"message": "temporary renderer error"})
    monkeypatch.setattr(studio.meta_client, "configured", lambda: False)
    retried = TestClient(app).post(f"/api/studio/{made['id']}/presentation")
    assert retried.status_code == 202
    assert retried.json()["presentation"]["job_id"] != made["presentation"]["job_id"]
    done = finish(repo, retried.json())
    assert done["state"] == "ready" and done["model_artifact_id"] == made["model_artifact_id"]


@pytest.mark.parametrize("state,model,enabled,expected", [("working", True, True, 409), ("ready", False, True, 422), ("ready", True, False, 422)])
def test_render_action_rejects_unavailable_models_or_disabled_service(repo, monkeypatch, state, model, enabled, expected):
    monkeypatch.setenv("SETOUT_RENDERER", "off")
    made = scene(repo)
    studio._set(repo, made["id"], state=state, model_artifact_id=made["model_artifact_id"] if model else None)
    if enabled:
        monkeypatch.setenv("SETOUT_RENDERER", "local")
    response = TestClient(app).post(f"/api/studio/{made['id']}/presentation")
    assert response.status_code == expected
    assert not repo.list(d.jobs)


def test_render_failure_does_not_fail_or_block_model(repo, monkeypatch):
    made = scene(repo)
    monkeypatch.setattr(presentation, "render_assets", lambda _: (_ for _ in ()).throw(RuntimeError("render failed")))
    held = finish(repo, made)
    assert held["state"] == "ready" and held["error"] is None
    assert held["presentation"]["state"] == "failed"
    assert studio.file_of(made["id"], "model.glb")[0] == glb()
    with pytest.raises(KeyError):
        studio.file_of(made["id"], "render")
    assert studio.diagnostics(made["id"])["jobs"][0]["error"]["message"] == "render failed"


@pytest.mark.parametrize("change", ["revision", "delete"])
def test_late_render_cannot_publish_after_revision_or_deletion(repo, monkeypatch, change):
    made = scene(repo)
    original = presentation.render_assets

    def during(data):
        if change == "revision":
            studio._set(repo, made["id"], state="working")
        else:
            studio.delete(made["id"])
        return original(data)

    monkeypatch.setattr(presentation, "render_assets", during)
    Worker(repo).run_once(only_ids=[made["presentation"]["job_id"]])
    assert not any(a["media_type"] == "image/jpeg" for a in repo.list(d.artifacts))
    if change == "revision":
        assert studio.get(made["id"])["presentation"] is None
        assert repo.get(d.jobs, made["presentation"]["job_id"])["result"] == {"discarded": True}
        studio._set(repo, made["id"], state="ready")
        revised = studio.get(made["id"])
        assert revised["presentation"]["job_id"] != made["presentation"]["job_id"]
        assert revised["presentation"]["state"] == "queued"
    else:
        assert not repo.list(d.studio_scenes)


def test_expired_worker_cannot_publish_a_render(repo, monkeypatch):
    made = scene(repo)
    fence = repo.claim("first", only_ids=[made["presentation"]["job_id"]])
    job = repo.get(d.jobs, fence.job_id)
    original = presentation.render_assets

    def during(data):
        repo.retry(fence, {"message": "lease lost"})
        return original(data)

    monkeypatch.setattr(presentation, "render_assets", during)
    with pytest.raises(LostLease):
        presentation.run_job(repo, fence, job, None)
    assert not presentation.artifacts(studio.get(made["id"])["presentation"])


def test_render_lane_does_not_claim_build_or_measure_jobs(repo):
    first = repo.enqueue("studio_build", {})
    second = repo.enqueue("studio_measure", {})
    made = scene(repo)
    finish(repo, made)
    assert repo.get(d.jobs, first["id"])["state"] == "queued"
    assert repo.get(d.jobs, second["id"])["state"] == "queued"
    assert repo.claim("build", kinds=("studio_build",)).job_id == first["id"]


def test_delete_preserves_shared_renders_and_removes_unshared_files(repo):
    first, second = scene(repo), scene(repo)
    one, two = finish(repo, first), finish(repo, second)
    artifacts = presentation.artifacts(one["presentation"])
    assert artifacts == presentation.artifacts(two["presentation"])
    studio.delete(one["id"])
    assert studio.file_of(two["id"], "render")[0] == b"rendered-image"
    studio.delete(two["id"])
    assert not {a["id"] for a in repo.list(d.artifacts)} & artifacts
    for ident in artifacts:
        with pytest.raises(FileNotFoundError):
            repo.artifacts.read(ident)


@pytest.mark.parametrize("item", [{"uri": "file:///etc/passwd"}, {"uri": "https://example.com/texture.png"}, {"uri": "../../private.png"}])
@pytest.mark.parametrize("collection", ["images", "buffers"])
def test_reject_external_resources(item, collection):
    with pytest.raises(ValueError, match="external URIs"):
        presentation.validate_glb(glb({collection: [item]}))


def test_reject_truncated_glb():
    with pytest.raises(ValueError):
        presentation.validate_glb(glb()[:-1])
