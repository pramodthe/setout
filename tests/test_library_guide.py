"""Library index, on-demand scene, and the guide's grounding (model stubbed)."""

import os

os.environ.setdefault("SETOUT_VAR", "/tmp/setout-test-var")

from fastapi.testclient import TestClient  # noqa: E402

from app import guide  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import door_sill_d3  # noqa: E402

client = TestClient(app)


def test_library_lists_seeds_by_situation_and_component():
    body = client.get("/api/library").json()
    d3 = next(d for d in body["details"] if d["detail_id"] == "conpro-door-sill-D3")
    assert d3["situation"] == ["Residential", "External · Balcony / Doorway", "Door sill"]
    assert d3["made_by"] == "human" and d3["drawing_values"] >= 3
    types = {c["type"]: c for c in body["components"]}
    assert "membrane" in types
    assert any(x["detail_id"] == "conpro-door-sill-D3" for x in types["membrane"]["details"])


def test_scene_endpoint_builds_and_verifies():
    s = client.get("/api/specs/conpro-door-sill-D3/scene").json()
    assert s["solids"] and s["verification"]
    assert not any(c["status"] == "FAIL" for c in s["verification"])
    assert client.get("/api/specs/nope/scene").status_code == 404


def test_grounding_allows_detail_numbers_only():
    spec = door_sill_d3()
    ok = [{"say": "The step is 100 mm from the drawing, the gap 6 mm, fall 1:80.", "actions": []}]
    assert guide.ungrounded(ok, spec) == {}
    bad = [{"say": "Lap the membrane 250 mm.", "actions": []}]
    assert guide.ungrounded(bad, spec) == {0: [250.0]}


def test_ask_retries_then_flags_and_cleans_actions(monkeypatch):
    spec = door_sill_d3()
    answers = iter([
        {"steps": [{"say": "Lap it 250 mm.", "actions": []}], "follow_up": ""},
        {"steps": [{"say": "The step is 100 mm (from the drawing).",
                    "actions": [{"type": "focus", "ids": ["slab_lower", "ghost"], "value": 0, "view": ""},
                                {"type": "focus", "ids": ["nope"], "value": 0, "view": ""},
                                {"type": "install_step", "ids": [], "value": 99, "view": ""},
                                {"type": "view", "ids": [], "value": 0, "view": ""}]}],
         "follow_up": "Ask about the sealant."},
    ])
    calls = []
    monkeypatch.setattr(guide.meta_client, "respond_json",
                        lambda content, *a, **k: (calls.append(content) or next(answers), None))
    out = guide.ask(spec, "how is the step formed?")
    assert out["retried"] and out["ungrounded"] == 0 and len(calls) == 2
    assert "250" in calls[1][0]["text"]  # the retry names the invented number
    acts = out["steps"][0]["actions"]
    assert acts[0]["ids"] == ["slab_lower"]  # unknown id dropped
    assert [a["type"] for a in acts] == ["focus", "install_step"]  # empty focus/view dropped
    assert acts[1]["value"] == len(spec.install_order())  # clamped


def test_ask_flags_what_is_still_ungrounded(monkeypatch):
    # a model that insists on an invented value: retried once, then flagged
    spec = door_sill_d3()
    monkeypatch.setattr(guide.meta_client, "respond_json", lambda *a, **k: (
        {"steps": [{"say": "Lap the membrane 375 mm.", "actions": []}], "follow_up": ""}, None))
    out = guide.ask(spec, "how far do I lap it?")
    assert out["retried"] and out["ungrounded"] == 1
    assert out["steps"][0]["ungrounded"] == [375.0]
