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


def test_card_counts_printed_values_confirmed_against_the_drawing():
    from app import library
    spec = door_sill_d3()
    assert library.entry(spec, "human")["drawing_values"] >= 3  # seeds: tagged on the parts
    # AI details: part dims mix printed values with assumptions, so they're
    # tagged "assumption"; the confirmed printed values are listed in context
    spec.context["drawing_params"] = [{"name": "a", "value": 150.0}, {"name": "b", "value": 1800.0}]
    assert library.entry(spec, "ai")["drawing_values"] == 2


def test_scene_endpoint_builds_and_verifies():
    # D4: D3's real fillet/membrane gap is reported as a FAIL now (tests/test_verify.py).
    s = client.get("/api/specs/conpro-subsill-D4/scene").json()
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


def test_ratios_are_one_value_not_two_numbers():
    """#7: "1:80" restates the stored fall ratio; its 80 is not a free number."""
    spec = door_sill_d3()  # fall_ratio 0.0125 (quoted "1:80 min. Fall to outlet")
    ok = [{"say": "The screed falls 1:80, or 1 in 80, toward the outlet.", "actions": []}]
    assert guide.ungrounded(ok, spec) == {}
    invented = [{"say": "Keep an 80 mm gap.", "actions": []}]  # 80 only existed inside 1:80
    assert guide.ungrounded(invented, spec) == {0: [80.0]}
    wrong = [{"say": "Fall it at 1:100.", "actions": []}]
    assert guide.ungrounded(wrong, spec) == {0: ["1:100"]}


def test_ratio_param_without_a_quote_still_grounds():
    spec = door_sill_d3()
    p = spec.component("screed").param("fall_ratio")
    p.source_quote = None  # e.g. an AI detail where the ratio is a bare param
    spec.component("screed").source_label = None  # the label also said "1:80"
    assert guide.ungrounded([{"say": "Fall 1:80.", "actions": []}], spec) == {}
