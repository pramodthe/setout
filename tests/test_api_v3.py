"""Jobs/SSE/public mode and version-specific human approval."""
import pytest
from fastapi.testclient import TestClient

from app import database as d, meta_client
from app.worker import Worker
from test_agent_v3 import FakeRenderer, ScriptedModel, source_repo


@pytest.fixture
def client(source_repo, monkeypatch):
    from app import main, runtime, v3_api
    monkeypatch.setattr(main, "PUBLIC", False)
    monkeypatch.setattr(runtime, "repository", lambda: source_repo)
    monkeypatch.setattr(v3_api, "repository", lambda: source_repo)
    monkeypatch.setattr(meta_client, "configured", lambda: True)
    monkeypatch.setenv("SETOUT_WORKER", "external")
    return TestClient(main.app)


def test_job_validation_idempotency_cancel_and_sse_replay(client):
    body = {"kind": "generate_detail", "payload": {"figure_id": "figure"}}
    headers = {"Idempotency-Key": "request-1"}
    first = client.post("/api/jobs", json=body, headers=headers)
    assert first.status_code == 202, first.text
    ident = first.json()["id"]
    assert client.post("/api/jobs", json=body, headers=headers).json()["id"] == ident
    changed = {"kind": "generate_detail", "payload": {"figure_id": "figure", "detail_id": "different"}}
    assert client.post("/api/jobs", json=changed, headers=headers).status_code == 409
    assert client.post("/api/jobs", json={"kind": "generate_detail", "payload": {"figure_id": "figure", "unknown": True}}).status_code == 422
    assert client.post(f"/api/jobs/{ident}/cancel").json()["state"] == "cancelled"
    assert client.post(f"/api/jobs/{ident}/cancel").json()["state"] == "cancelled"
    stream = client.get(f"/api/jobs/{ident}/events", headers={"Last-Event-ID": "1"})
    assert "id: 2\n" in stream.text and "id: 1\n" not in stream.text
    assert "event: terminal" in stream.text


def test_private_run_artifact_and_version_routes_are_blocked_in_public(client, source_repo, monkeypatch):
    from app import main
    monkeypatch.setattr(main, "PUBLIC", True)
    for url in ("/api/jobs/any", "/api/runs/any/steps", "/api/artifacts/any", "/api/versions/any", "/api/details/any/versions"):
        assert client.get(url).status_code == 403
    assert client.post("/api/jobs", json={"kind": "render", "payload": {"version_id": "any"}}).status_code == 403


def test_review_fix_creates_draft_without_changing_published_history(client, source_repo):
    repo = source_repo
    job = repo.enqueue("generate_detail", {"figure_id": "figure"})
    Worker(repo, responder=ScriptedModel(), renderer=FakeRenderer(repo)).run_once()
    first = repo.get(d.versions, repo.run(job["id"])["working_version_id"])
    detail_id = first["detail_id"]
    assert client.post(f"/api/versions/{first['id']}/reviews", json={"reviewer": "Expert", "action": "accept"}).status_code == 200
    published = client.post(f"/api/details/{detail_id}/publish", json={"version_id": first["id"], "expected_published_version_id": None})
    assert published.status_code == 200, published.text
    changed = first["program"]
    changed["params"][1]["value"] = 120
    corrected = client.post(f"/api/versions/{first['id']}/reviews", json={"reviewer": "Expert", "action": "correct", "data": {"program": changed}})
    assert corrected.status_code == 200, corrected.text
    second = repo.get(d.versions, corrected.json()["version_id"])
    assert second["id"] != first["id"]
    assert repo.get(d.versions, first["id"])["program"]["params"][1]["value"] == 100
    assert second["program"]["params"][1]["provenance"] == "user_input"
    stale = client.post(f"/api/versions/{first['id']}/reviews",json={"reviewer":"Expert","action":"correct","data":{"program":changed}})
    assert stale.status_code == 409,stale.text
    assert len(repo.list(d.versions)) == 2
    assert repo.get(d.details, detail_id)["published_version_id"] == first["id"]
    assert client.post(f"/api/details/{detail_id}/publish", json={"version_id": second["id"], "expected_published_version_id": first["id"]}).status_code == 409
    history = client.get(f"/api/details/{detail_id}/versions").json()
    assert len(history["versions"]) == 2
    assert client.get(f"/api/specs/{detail_id}/review").json()["status"] == "draft"


def test_artifact_corruption_fails_explicitly(client, source_repo):
    figure = source_repo.get(d.figures, "figure")
    url = f"/api/artifacts/{figure['artifact_id']}"
    assert client.get(url).status_code == 200
    source_repo.artifacts.resolve(figure["artifact_id"]).write_bytes(b"changed")
    assert client.get(url).status_code == 409


def test_batch_accounts_for_incomplete_children_without_claiming_detail_success(client, source_repo):
    repo = source_repo
    with d.transaction(repo.engine) as con:
        figure = repo.get(d.figures, "figure")
        con.execute(d.figures.insert().values(**{**figure,"id":"figure-2","crop_key":"p0_other"}))
    dataset = client.post("/api/eval/datasets", json={"figure_ids":["figure","figure-2"]})
    assert dataset.status_code == 201, dataset.text
    body = {"kind":"batch_eval","payload":{"dataset_artifact_id":dataset.json()["dataset_artifact_id"]}}
    created = client.post("/api/jobs", json=body, headers={"Idempotency-Key":"cohort"})
    assert created.status_code == 202, created.text
    def needs_review(payload):
        import json
        return {"output":[{"type":"function_call","name":"finish","call_id":"finish","arguments":json.dumps({
            "status":"needs_review","summary":"Manual review required","open_issues":["held-out case"],
            "version_id":None,"verification_id":None,"critic_verdict_id":None})}],
            "usage":{"input_tokens":10,"output_tokens":10}}
    Worker(repo, responder=needs_review).run_once()
    final = repo.get(d.jobs, created.json()["id"])
    assert final["state"] == "succeeded"
    assert final["result"]["states"] == {"needs_review":2}
    assert len(final["result"]["rows"]) == 2
    assert client.post("/api/jobs", json=body, headers={"Idempotency-Key":"cohort"}).json()["id"] == final["id"]
    assert len(repo.list(d.jobs)) == 3
