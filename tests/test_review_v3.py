"""The review screen on immutable versions, and which details take which path."""
import pytest

from app import database as d, review_v3
from app.worker import Worker
from test_agent_v3 import FakeRenderer, ScriptedModel, source_repo  # noqa: F401
from test_api_v3 import client  # noqa: F401


@pytest.fixture
def version(source_repo):
    job = source_repo.enqueue("generate_detail", {"figure_id": "figure"})
    Worker(source_repo, responder=ScriptedModel(), renderer=FakeRenderer(source_repo)).run_once()
    return source_repo.get(d.versions, source_repo.run(job["id"])["working_version_id"])


def _dim(version, part, name):
    component = next(c for c in version["spec"]["components"] if c["id"] == part)
    return next(p for p in component["params"] if p["name"] == name)["value"]


def test_edit_moves_every_dependent_dimension_in_a_new_version(source_repo, version):
    review = review_v3.apply(source_repo, version, [{"name": "width", "value": 120}], [], [], reviewer="Expert")
    revised = source_repo.get(d.versions, review["version_id"])
    assert revised["parent_version_id"] == version["id"]
    width = next(p for p in review["params"] if p["name"] == "width")
    assert width["provenance"] == "user_input" and width["value"] == 120 and "wall" in width["used_by"]
    assert _dim(revised, "wall", "x1") == 120
    assert _dim(source_repo.get(d.versions, version["id"]), "wall", "x1") == 100  # history is immutable


def test_drawing_values_listed_first_and_accept_is_recorded(source_repo, version):
    review = review_v3.apply(source_repo, version, [], ["height"], [])
    assert review["params"][0]["name"] == "height" and review["params"][0]["provenance"] == "drawing"
    assert review["params"][0]["accepted"] and review["status"] == "draft"
    assert review["version_id"] == version["id"]  # accepting a value is not a new version


def test_labels_only_from_the_drawing(source_repo, version):
    with pytest.raises(ValueError):
        review_v3.apply(source_repo, version, [], [], [{"part_id": "wall", "label": "Luxury feature wall"}])
    review = review_v3.apply(source_repo, version, [], [], [{"part_id": "wall", "label": None}])
    assert next(p for p in review["parts"] if p["id"] == "wall")["label"] is None


def test_breaking_edit_is_rejected_and_nothing_saved(source_repo, version):
    before = len(source_repo.list(d.versions))
    with pytest.raises(ValueError):
        review_v3.apply(source_repo, version, [{"name": "width", "value": float("nan")}], [], [])
    assert len(source_repo.list(d.versions)) == before


def test_publish_needs_a_name(source_repo, version):
    with pytest.raises(ValueError):
        review_v3.apply(source_repo, version, [], [], [], publish=True, reviewer=" ")


def test_seeds_stay_in_code_and_ai_details_change_only_through_versions(client, source_repo, version):
    seed = "conpro-door-sill-D3"
    # A seed's earlier import never shadows the hand-transcribed spec in code.
    with d.transaction(source_repo.engine) as con:
        source_repo.insert_version(con, detail_id=seed, expected_parent=None, program={},
                                   spec=version["spec"], format_version="legacy-v2")
    assert client.get(f"/api/specs/{seed}").json()["origin"].startswith("human-transcription")
    assert client.get(f"/api/specs/{seed}/review").status_code == 404
    for action, body in (("cad", {"part_id": "x"}), ("critique", {}), ("locate", None)):
        assert client.post(f"/api/specs/{seed}/{action}", json=body).status_code in (409, 503)
    detail = version["detail_id"]
    assert client.patch(f"/api/specs/{detail}", json={"component_id": "wall", "param": "x1", "value": 1}).status_code == 409
    assert client.patch("/api/specs/no-such-detail", json={"component_id": "wall"}).status_code == 404
    # The AI self-check is a persistent job, not an in-process thread.
    job = client.post(f"/api/specs/{detail}/critique", json={}).json()
    assert source_repo.get(d.jobs, job["id"])["kind"] == "revise_detail"
    assert client.get(f"/api/generate/{job['id']}").json()["native_state"] == "queued"
